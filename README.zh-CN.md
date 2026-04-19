# farfield-cli

通过 [Farfield](https://github.com/drewcotten/farfield) sidecar 控制 Codex App 的本地 CLI 桥接工具。

特别感谢 [Drew Cotten](https://github.com/drewcotten) 开源了 Farfield。`farfield-cli` 正是基于 Farfield 的能力来做本地桥接，这个项目也继续采用 MIT 开源。

英文版说明：[README.md](README.md)

## 这个项目是干什么的

目标非常直接：

- clone Farfield
- 先在 Farfield 目录里执行一次 `pnpm install`
- 进入 Farfield 目录
- 直接运行 `farfield-cli ...`

不做前端。不做远程暴露。不折腾复杂面板。只保留一个 loopback-only 的小 CLI，用来：

- 自动启动 Farfield server sidecar
- 查看和读取 Codex 线程
- 发送消息
- 切换协作模式
- 提交等待中的用户输入
- 中断当前运行

这样做也方便别的 agent、脚本、自动化系统直接调用一个稳定 CLI，而不用自己去实现 Farfield 的 HTTP 协议。

## 安装

```bash
pip install farfield-cli
```

或者从源码安装：

```bash
git clone https://github.com/wuji419-bit/farfield-cli.git
cd farfield-cli
pip install -e .
```

## 前置条件

1. Codex desktop 和 Farfield 运行在同一台机器上
2. 本地已经 clone 了 Farfield
3. 在 Farfield 仓库里至少执行过一次 `pnpm install`

默认情况下，`farfield-cli` 会认为 Farfield 监听在：

```text
http://127.0.0.1:4311
```

## 零配置使用

如果你当前目录是：

- Farfield 仓库根目录，或者
- 一个包含 `farfield/` 子目录的父目录

那么 `farfield-cli` 会自动发现 Farfield 仓库，并在第一次调用时自动启动 sidecar。

```bash
cd /path/to/farfield
farfield-cli status
farfield-cli list-threads
farfield-cli list-models
```

## 常用命令

```bash
farfield-cli status
farfield-cli list-threads --limit 20
farfield-cli get-thread-state --thread-id thread_123 --include-stream-events
farfield-cli send-message --thread-id thread_123 --text "继续做这个任务"
farfield-cli interrupt --thread-id thread_123
```

传递 opaque JSON 的例子：

```bash
farfield-cli set-collaboration-mode \
  --thread-id thread_123 \
  --json '{"mode":"default"}'

farfield-cli submit-user-input \
  --thread-id thread_123 \
  --request-id 7 \
  --json '{"kind":"text","text":"继续"}'
```

## 可选配置

所有配置都不是必须的。

环境变量：

- `FARFIELD_CLI_BASE_URL`
- `FARFIELD_CLI_PROJECT_DIR`
- `FARFIELD_CLI_START_COMMAND`
- `FARFIELD_CLI_AUTOSTART`
- `FARFIELD_CLI_STARTUP_TIMEOUT`
- `FARFIELD_CLI_REQUEST_TIMEOUT`
- `CODEX_CLI_PATH`
- `CODEX_IPC_SOCKET`

默认值：

```text
base_url           = http://127.0.0.1:4311
start_command      = pnpm --filter @farfield/server dev
autostart          = true
startup_timeout    = 20
request_timeout    = 30
```

v1 强制只允许 loopback。也就是说只接受 `127.0.0.1` 和 `localhost`，不会去连远程主机。

## 输出格式

所有命令都输出 JSON。

成功时大致像这样：

```json
{
  "success": true,
  "bridge": {
    "base_url": "http://127.0.0.1:4311",
    "autostarted": false,
    "process_pid": null
  },
  "data": {}
}
```

失败时大致像这样：

```json
{
  "success": false,
  "error": "human-readable message",
  "http_status": 409,
  "bridge": {
    "base_url": "http://127.0.0.1:4311",
    "autostarted": false,
    "process_pid": null
  },
  "details": {}
}
```

## 开发

运行测试：

```bash
python -m unittest discover -s tests -p "test_*.py"
```
