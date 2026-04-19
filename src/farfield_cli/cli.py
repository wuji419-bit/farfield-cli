from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:4311"
DEFAULT_START_COMMAND = "pnpm --filter @farfield/server dev"
DEFAULT_STARTUP_TIMEOUT_SECONDS = 20
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
HEALTH_PATH = "/api/health"


@dataclass(frozen=True)
class BridgeContext:
    base_url: str
    autostarted: bool = False
    process_pid: int | None = None


@dataclass(frozen=True)
class Config:
    base_url: str
    project_dir: str
    autostart: bool
    start_command: str
    startup_timeout_seconds: int
    request_timeout_seconds: int


class BridgeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        bridge: BridgeContext | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.details = details or {}
        self.bridge = bridge
        self.http_status = http_status


def _as_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def canonicalize_base_url(raw_value: Any) -> str:
    raw = str(raw_value or "").strip() or DEFAULT_BASE_URL
    if "://" not in raw:
        raw = f"http://{raw}"

    parsed = urlsplit(raw)
    if parsed.scheme != "http":
        raise BridgeError(
            "base_url must use plain http over loopback.",
            details={"base_url": raw},
        )

    host = (parsed.hostname or "").strip().lower()
    if host not in {"127.0.0.1", "localhost"}:
        raise BridgeError(
            "base_url must point to 127.0.0.1 or localhost.",
            details={"base_url": raw},
        )

    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise BridgeError(
            "base_url must not include a path, query string, or fragment.",
            details={"base_url": raw},
        )

    port = parsed.port or 4311
    return urlunsplit(("http", f"{host}:{port}", "", "", ""))


def looks_like_farfield_repo(path_value: Any) -> bool:
    raw = str(path_value or "").strip()
    if not raw:
        return False
    try:
        candidate = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return False

    return (
        candidate.is_dir()
        and (candidate / "package.json").is_file()
        and (candidate / "apps" / "server" / "package.json").is_file()
    )


def discover_project_dir(configured_project_dir: Any) -> str:
    raw_configured = str(configured_project_dir or "").strip()
    if raw_configured:
        return raw_configured

    candidates: list[str] = []

    current_cwd = str(os.getcwd() or "").strip()
    if current_cwd:
        candidates.append(current_cwd)
        candidates.append(str(Path(current_cwd) / "farfield"))

    for env_name in ("MESSAGING_CWD", "FARFIELD_CLI_PROJECT_DIR"):
        value = str(os.environ.get(env_name, "") or "").strip()
        if value:
            candidates.append(value)
            candidates.append(str(Path(value) / "farfield"))

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if looks_like_farfield_repo(candidate):
            return candidate

    return ""


def load_config(args: argparse.Namespace) -> Config:
    base_url = canonicalize_base_url(
        getattr(args, "base_url", None) or os.environ.get("FARFIELD_CLI_BASE_URL", "")
    )
    project_dir = discover_project_dir(
        getattr(args, "project_dir", None) or os.environ.get("FARFIELD_CLI_PROJECT_DIR", "")
    )
    autostart = not getattr(args, "no_autostart", False)
    autostart = _parse_bool(os.environ.get("FARFIELD_CLI_AUTOSTART"), autostart)

    return Config(
        base_url=base_url,
        project_dir=project_dir,
        autostart=autostart,
        start_command=(
            getattr(args, "start_command", None)
            or os.environ.get("FARFIELD_CLI_START_COMMAND", "")
            or DEFAULT_START_COMMAND
        ),
        startup_timeout_seconds=_as_int(
            getattr(args, "startup_timeout", None) or os.environ.get("FARFIELD_CLI_STARTUP_TIMEOUT"),
            DEFAULT_STARTUP_TIMEOUT_SECONDS,
        ),
        request_timeout_seconds=_as_int(
            getattr(args, "request_timeout", None) or os.environ.get("FARFIELD_CLI_REQUEST_TIMEOUT"),
            DEFAULT_REQUEST_TIMEOUT_SECONDS,
        ),
    )


def bridge_dict(bridge: BridgeContext | None, fallback_base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    if bridge is None:
        bridge = BridgeContext(base_url=fallback_base_url)
    return {
        "base_url": bridge.base_url,
        "autostarted": bridge.autostarted,
        "process_pid": bridge.process_pid,
    }


def success_result(bridge: BridgeContext, data: Any) -> dict[str, Any]:
    return {
        "success": True,
        "bridge": bridge_dict(bridge),
        "data": data,
    }


def failure_result(
    message: str,
    *,
    bridge: BridgeContext | None = None,
    http_status: int | None = None,
    details: dict[str, Any] | None = None,
    fallback_base_url: str = DEFAULT_BASE_URL,
) -> dict[str, Any]:
    return {
        "success": False,
        "error": str(message),
        "http_status": http_status,
        "bridge": bridge_dict(bridge, fallback_base_url=fallback_base_url),
        "details": details or {},
    }


class SidecarManager:
    def _healthcheck(self, base_url: str, timeout_seconds: int) -> tuple[bool, str | None]:
        try:
            response = requests.get(
                f"{base_url}{HEALTH_PATH}",
                timeout=min(timeout_seconds, 3),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            return False, str(exc)
        if response.status_code == 200:
            return True, None
        return False, f"HTTP {response.status_code}"

    def _resolve_project_dir(self, config: Config, bridge: BridgeContext) -> Path:
        if not config.project_dir:
            raise BridgeError(
                "No Farfield repo was found. Run inside the Farfield repo, use a parent directory containing farfield/, or pass --project-dir.",
                details={"required_files": ["package.json", "apps/server/package.json"]},
                bridge=bridge,
            )

        project_dir = Path(config.project_dir).expanduser().resolve()
        if not project_dir.exists() or not project_dir.is_dir():
            raise BridgeError(
                "project_dir does not exist or is not a directory.",
                details={"project_dir": str(project_dir)},
                bridge=bridge,
            )

        if not looks_like_farfield_repo(project_dir):
            raise BridgeError(
                "project_dir must point at the Farfield repository root.",
                details={"project_dir": str(project_dir)},
                bridge=bridge,
            )

        if config.start_command == DEFAULT_START_COMMAND and not (project_dir / "node_modules").exists():
            raise BridgeError(
                "Farfield dependencies do not appear to be installed. Run `pnpm install` in the Farfield repo first.",
                details={"project_dir": str(project_dir)},
                bridge=bridge,
            )

        return project_dir

    def _spawn_sidecar(self, config: Config, bridge: BridgeContext) -> BridgeContext:
        project_dir = self._resolve_project_dir(config, bridge)
        if config.start_command == DEFAULT_START_COMMAND:
            if not (shutil.which("pnpm") or shutil.which("pnpm.cmd")):
                raise BridgeError(
                    "pnpm is not available on PATH, so the Farfield sidecar cannot be auto-started.",
                    details={"project_dir": str(project_dir)},
                    bridge=bridge,
                )

        env = os.environ.copy()
        env["HOST"] = "127.0.0.1"
        env["PORT"] = str(urlsplit(config.base_url).port or 4311)
        for key in ("CODEX_CLI_PATH", "CODEX_IPC_SOCKET"):
            value = os.environ.get(key)
            if value:
                env[key] = value

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000008

        process = subprocess.Popen(
            shlex.split(config.start_command, posix=os.name != "nt"),
            cwd=str(project_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),
            creationflags=creationflags,
        )
        return BridgeContext(
            base_url=config.base_url,
            autostarted=True,
            process_pid=process.pid,
        )

    def _wait_for_health(self, config: Config, bridge: BridgeContext) -> BridgeContext:
        deadline = time.monotonic() + config.startup_timeout_seconds
        last_error: str | None = None
        while time.monotonic() < deadline:
            healthy, error = self._healthcheck(config.base_url, config.request_timeout_seconds)
            if healthy:
                return bridge
            last_error = error
            time.sleep(0.5)
        raise BridgeError(
            "Timed out waiting for the Farfield sidecar health endpoint.",
            details={"last_health_error": last_error},
            bridge=bridge,
        )

    def ensure_bridge_ready(self, config: Config) -> BridgeContext:
        bridge = BridgeContext(base_url=config.base_url)
        healthy, _ = self._healthcheck(config.base_url, config.request_timeout_seconds)
        if healthy:
            return bridge
        if not config.autostart:
            raise BridgeError(
                "Farfield is not reachable at base_url and autostart is disabled.",
                details={"base_url": config.base_url},
                bridge=bridge,
            )
        bridge = self._spawn_sidecar(config, bridge)
        return self._wait_for_health(config, bridge)


class FarfieldHttpClient:
    def __init__(self, bridge: BridgeContext, request_timeout_seconds: int) -> None:
        self.bridge = bridge
        self.timeout = request_timeout_seconds

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        url = f"{self.bridge.base_url}{path}"
        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=json_body,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise BridgeError(
                f"Failed to reach the Farfield bridge: {exc}",
                details={"method": method, "url": url},
                bridge=self.bridge,
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = response.text

        if 200 <= response.status_code < 300:
            if isinstance(payload, (dict, list)):
                return payload
            raise BridgeError(
                "Farfield returned a non-JSON success response.",
                http_status=response.status_code,
                details={"method": method, "url": url, "body": payload},
                bridge=self.bridge,
            )

        message = None
        if isinstance(payload, dict):
            message = payload.get("error") or payload.get("message")
        if not message:
            message = f"Farfield returned HTTP {response.status_code}."

        raise BridgeError(
            str(message),
            http_status=response.status_code,
            details={"method": method, "url": url, "body": payload},
            bridge=self.bridge,
        )


def compact_dict(**kwargs: Any) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


def load_json_payload(raw_json: str | None, json_file: str | None) -> Any:
    if raw_json and json_file:
        raise BridgeError("Use either --json or --json-file, not both.")
    if raw_json:
        return json.loads(raw_json)
    if json_file:
        if json_file == "-":
            return json.load(sys.stdin)
        with open(json_file, "r", encoding="utf-8") as handle:
            return json.load(handle)
    raise BridgeError("A JSON payload is required. Use --json or --json-file.")


def execute(args: argparse.Namespace, operation) -> int:
    config = load_config(args)
    bridge = None
    try:
        manager = SidecarManager()
        bridge = manager.ensure_bridge_ready(config)
        client = FarfieldHttpClient(bridge, config.request_timeout_seconds)
        result = success_result(bridge, operation(client, args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except BridgeError as exc:
        result = failure_result(
            str(exc),
            bridge=bridge or exc.bridge,
            http_status=exc.http_status,
            details=exc.details,
            fallback_base_url=config.base_url,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1


def op_status(client: FarfieldHttpClient, args: argparse.Namespace) -> Any:
    return client.request("GET", "/api/health")


def op_list_threads(client: FarfieldHttpClient, args: argparse.Namespace) -> Any:
    params = {
        "limit": args.limit,
        "archived": str(bool(args.archived)).lower(),
        "all": str(bool(args.all_pages)).lower(),
        "maxPages": args.max_pages,
    }
    if args.cursor:
        params["cursor"] = args.cursor
    return client.request("GET", "/api/threads", params=params)


def op_get_thread_state(client: FarfieldHttpClient, args: argparse.Namespace) -> Any:
    thread_id = quote(args.thread_id, safe="")
    thread = client.request(
        "GET",
        f"/api/threads/{thread_id}",
        params={"includeTurns": str(bool(args.include_turns)).lower()},
    )
    if not args.include_stream_events:
        return thread
    return {
        "thread": thread,
        "live_state": client.request("GET", f"/api/threads/{thread_id}/live-state"),
        "stream_events": client.request(
            "GET",
            f"/api/threads/{thread_id}/stream-events",
            params={"limit": args.event_limit},
        ),
    }


def op_list_models(client: FarfieldHttpClient, args: argparse.Namespace) -> Any:
    return client.request("GET", "/api/models", params={"limit": args.limit})


def op_list_collaboration_modes(client: FarfieldHttpClient, args: argparse.Namespace) -> Any:
    return client.request("GET", "/api/collaboration-modes")


def op_start_thread(client: FarfieldHttpClient, args: argparse.Namespace) -> Any:
    body = compact_dict(
        cwd=args.cwd,
        model=args.model,
        modelProvider=args.model_provider,
        personality=args.personality,
        sandbox=args.sandbox,
        approvalPolicy=args.approval_policy,
        ephemeral=(True if args.ephemeral else None),
    )
    return client.request("POST", "/api/threads", json_body=body)


def op_send_message(client: FarfieldHttpClient, args: argparse.Namespace) -> Any:
    body = compact_dict(
        ownerClientId=args.owner_client_id,
        text=args.text,
        cwd=args.cwd,
        isSteering=bool(args.steering),
    )
    return client.request(
        "POST",
        f"/api/threads/{quote(args.thread_id, safe='')}/messages",
        json_body=body,
    )


def op_set_collaboration_mode(client: FarfieldHttpClient, args: argparse.Namespace) -> Any:
    body = compact_dict(
        ownerClientId=args.owner_client_id,
        collaborationMode=load_json_payload(args.json_payload, args.json_file),
    )
    return client.request(
        "POST",
        f"/api/threads/{quote(args.thread_id, safe='')}/collaboration-mode",
        json_body=body,
    )


def op_submit_user_input(client: FarfieldHttpClient, args: argparse.Namespace) -> Any:
    body = compact_dict(
        ownerClientId=args.owner_client_id,
        requestId=args.request_id,
        response=load_json_payload(args.json_payload, args.json_file),
    )
    return client.request(
        "POST",
        f"/api/threads/{quote(args.thread_id, safe='')}/user-input",
        json_body=body,
    )


def op_interrupt(client: FarfieldHttpClient, args: argparse.Namespace) -> Any:
    body = compact_dict(ownerClientId=args.owner_client_id)
    return client.request(
        "POST",
        f"/api/threads/{quote(args.thread_id, safe='')}/interrupt",
        json_body=body,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="farfield-cli")
    parser.set_defaults(func=None)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url")
    common.add_argument("--project-dir")
    common.add_argument("--start-command")
    common.add_argument("--no-autostart", action="store_true")
    common.add_argument("--startup-timeout", type=int)
    common.add_argument("--request-timeout", type=int)

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", parents=[common]).set_defaults(func=op_status)

    list_threads = subparsers.add_parser("list-threads", parents=[common])
    list_threads.add_argument("--limit", type=int, default=80)
    list_threads.add_argument("--archived", action="store_true")
    list_threads.add_argument("--all-pages", action="store_true")
    list_threads.add_argument("--max-pages", type=int, default=20)
    list_threads.add_argument("--cursor")
    list_threads.set_defaults(func=op_list_threads)

    get_thread = subparsers.add_parser("get-thread-state", parents=[common])
    get_thread.add_argument("--thread-id", required=True)
    get_thread.add_argument("--no-include-turns", dest="include_turns", action="store_false")
    get_thread.add_argument("--include-stream-events", action="store_true")
    get_thread.add_argument("--event-limit", type=int, default=60)
    get_thread.set_defaults(func=op_get_thread_state, include_turns=True)

    list_models = subparsers.add_parser("list-models", parents=[common])
    list_models.add_argument("--limit", type=int, default=100)
    list_models.set_defaults(func=op_list_models)

    subparsers.add_parser("list-collaboration-modes", parents=[common]).set_defaults(
        func=op_list_collaboration_modes
    )

    start_thread = subparsers.add_parser("start-thread", parents=[common])
    start_thread.add_argument("--cwd")
    start_thread.add_argument("--model")
    start_thread.add_argument("--model-provider")
    start_thread.add_argument("--personality")
    start_thread.add_argument("--sandbox")
    start_thread.add_argument("--approval-policy")
    start_thread.add_argument("--ephemeral", action="store_true")
    start_thread.set_defaults(func=op_start_thread)

    send_message = subparsers.add_parser("send-message", parents=[common])
    send_message.add_argument("--thread-id", required=True)
    send_message.add_argument("--text", required=True)
    send_message.add_argument("--owner-client-id")
    send_message.add_argument("--cwd")
    send_message.add_argument("--steering", action="store_true")
    send_message.set_defaults(func=op_send_message)

    set_mode = subparsers.add_parser("set-collaboration-mode", parents=[common])
    set_mode.add_argument("--thread-id", required=True)
    set_mode.add_argument("--owner-client-id")
    set_mode.add_argument("--json", dest="json_payload")
    set_mode.add_argument("--json-file")
    set_mode.set_defaults(func=op_set_collaboration_mode)

    submit_input = subparsers.add_parser("submit-user-input", parents=[common])
    submit_input.add_argument("--thread-id", required=True)
    submit_input.add_argument("--request-id", type=int, required=True)
    submit_input.add_argument("--owner-client-id")
    submit_input.add_argument("--json", dest="json_payload")
    submit_input.add_argument("--json-file")
    submit_input.set_defaults(func=op_submit_user_input)

    interrupt = subparsers.add_parser("interrupt", parents=[common])
    interrupt.add_argument("--thread-id", required=True)
    interrupt.add_argument("--owner-client-id")
    interrupt.set_defaults(func=op_interrupt)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.func is None:
        parser.print_help()
        return 2
    return execute(args, args.func)
