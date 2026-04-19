import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from farfield_cli.cli import canonicalize_base_url, discover_project_dir, looks_like_farfield_repo


class RepoDiscoveryTests(unittest.TestCase):
    def test_looks_like_farfield_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "apps" / "server").mkdir(parents=True)
            (repo / "package.json").write_text("{}", encoding="utf-8")
            (repo / "apps" / "server" / "package.json").write_text("{}", encoding="utf-8")
            self.assertTrue(looks_like_farfield_repo(repo))

    def test_discover_project_dir_prefers_current_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "apps" / "server").mkdir(parents=True)
            (repo / "package.json").write_text("{}", encoding="utf-8")
            (repo / "apps" / "server" / "package.json").write_text("{}", encoding="utf-8")

            old = os.getcwd()
            os.chdir(repo)
            try:
                self.assertEqual(Path(discover_project_dir("")).resolve(), repo.resolve())
            finally:
                os.chdir(old)


class BaseUrlTests(unittest.TestCase):
    def test_default_base_url(self):
        self.assertEqual(canonicalize_base_url(""), "http://127.0.0.1:4311")

    def test_rejects_remote_host(self):
        with self.assertRaises(Exception):
            canonicalize_base_url("http://10.0.0.8:4311")


if __name__ == "__main__":
    unittest.main()
