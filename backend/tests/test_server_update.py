"""Release-package verification and non-destructive staging tests."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from server_update import inspect_release_package, prepare_release_package


def make_package(root: Path, *, unsafe: bool = False) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    package = root / "江西片区智能交接班_局域网服务器_V0.4.1_win-x64.zip"
    top = "江西片区智能交接班_局域网服务器_V0.4.1_win-x64"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{top}/服务器控制器.exe", b"controller")
        archive.writestr(f"{top}/交接班服务器.exe", b"server")
        archive.writestr(f"{top}/_internal/readme.txt", b"runtime")
        if unsafe:
            archive.writestr("../escape.txt", b"unsafe")
    digest = hashlib.sha256(package.read_bytes()).hexdigest().upper()
    sha = Path(f"{package}.sha256")
    sha.write_text(f"{digest}  {package.name}\n", encoding="utf-8")
    return package, sha


class ServerUpdateTest(unittest.TestCase):
    def test_verified_package_is_staged_once_without_overwriting_old_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, _sha = make_package(root)
            install = root / "versions"
            old = install / "V0.4.0"
            old.mkdir(parents=True)
            (old / "keep.txt").write_text("old-version", encoding="utf-8")

            inspected = inspect_release_package(package)
            prepared = prepare_release_package(package, install)
            repeated = prepare_release_package(package, install)

            self.assertEqual(inspected["version"], "0.4.1")
            self.assertTrue(Path(prepared["install_path"]).is_dir())
            self.assertTrue((Path(prepared["install_path"]) / "prepared-update.json").is_file())
            self.assertEqual((old / "keep.txt").read_text(encoding="utf-8"), "old-version")
            self.assertFalse(prepared["already_prepared"])
            self.assertTrue(repeated["already_prepared"])

    def test_hash_mismatch_and_zip_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, sha = make_package(root)
            sha.write_text("0" * 64, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                inspect_release_package(package)

            unsafe, _sha = make_package(root / "unsafe", unsafe=True)
            with self.assertRaises(RuntimeError):
                inspect_release_package(unsafe)


if __name__ == "__main__":
    unittest.main()
