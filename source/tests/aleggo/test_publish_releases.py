"""Round 14 tests: publish pipeline URL convention and version ordering.

unittest style (no pytest on this machine); also collected by pytest in CI.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS.parent))


def _load_publish():
    spec = importlib.util.spec_from_file_location(
        "publish_lean_bundle", TOOLS / "publish-lean-bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublishReleasesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pub = _load_publish()

    def _fragment(self, version: str, platform: str = "linux-x64") -> dict:
        return {"bundle_version": version, "platform": platform,
                "file": f"aleggo-lean-bundle-{platform}.tar.zst",
                "sha256": "a" * 64, "size": 1234, "format": "tar.zst",
                "lean_toolchain": "leanprover/lean4:v4.32.0",
                "mathlib_rev": "v4.32.0", "built_at": "2026-01-01T00:00:00Z"}

    def test_artifact_url_matches_oss_key(self):
        # build-lean-bundle.py uploads to OSS key
        #   aleggo/lean/<version>/<platform>/<file>
        # so the manifest URL must be <cdn-base>/aleggo/lean/<version>/<platform>/<file>.
        merged = self.pub.build_releases(
            {}, [self._fragment("v4.32.0-1")], "https://cdn.example.com")
        url = merged["releases"]["v4.32.0-1"]["platforms"]["linux-x64"]["url"]
        self.assertEqual(
            url,
            "https://cdn.example.com/aleggo/lean/v4.32.0-1/linux-x64/"
            "aleggo-lean-bundle-linux-x64.tar.zst")

    def test_latest_is_numeric_not_lexicographic(self):
        # Plain max() would rank "v4.32.0-2" above "v4.32.0-10".
        merged = self.pub.build_releases(
            {}, [self._fragment("v4.32.0-2"), self._fragment("v4.32.0-10")],
            "https://cdn.example.com")
        self.assertEqual(merged["latest"], "v4.32.0-10")

    def test_existing_releases_preserved(self):
        existing = {"latest": "v4.32.0-1",
                    "releases": {"v4.32.0-1": {"platforms": {"linux-x64": {}},
                                              "lean_toolchain": "x", "mathlib_rev": "y",
                                              "built_at": "z"}}}
        merged = self.pub.build_releases(
            existing, [self._fragment("v4.32.0-2")], "https://cdn.example.com")
        self.assertIn("v4.32.0-1", merged["releases"])
        self.assertIn("v4.32.0-2", merged["releases"])
        self.assertEqual(merged["latest"], "v4.32.0-2")


if __name__ == "__main__":
    unittest.main()
