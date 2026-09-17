"""Regenerate the Windows version resources from the repository VERSION file.

Both ``version_info*.txt`` files used to carry a hand-typed number, and both
drifted: the desktop controller said 0.3.0 and the LAN server said 0.4.1 long
after the application had moved on.  Nothing failed, so nobody noticed - the
EXE properties simply disagreed with the running service, the management page
and the release manifest.  Generating them here keeps one version source.

Run by the packaging scripts before PyInstaller::

    python packaging/make_version_info.py
"""
from __future__ import annotations

from pathlib import Path
import re
import sys


PACKAGING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGING_DIR.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# (file name, translation table, strings) - the two executables are described
# differently on purpose, so only the numbers are shared.
DESKTOP_TARGET = (
    "version_info.txt",
    "040904B0",
    (1033, 1200),
    {
        "CompanyName": "江西片区",
        "FileDescription": "智能交接班系统控制器",
        "InternalName": "JXHandover",
        "LegalCopyright": "内部使用",
        "OriginalFilename": "交接班系统.exe",
        "ProductName": "江西片区智能交接班系统",
    },
)
SERVER_TARGET = (
    "version_info_v041_server.txt",
    "080404b0",
    (2052, 1200),
    {
        "CompanyName": "江西片区检修中心",
        "FileDescription": "江西片区智能交接班局域网服务器",
        "InternalName": "JXHandoverServer",
        "LegalCopyright": "江西片区检修中心",
        "OriginalFilename": "交接班服务器.exe",
        "ProductName": "江西片区智能交接班局域网服务器",
    },
)


def read_version() -> tuple[str, tuple[int, int, int, int]]:
    """Return ``("0.5.3", (0, 5, 3, 0))`` from the single version source."""
    try:
        text = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SystemExit(f"读取版本文件失败：{VERSION_FILE}（{exc}）") from exc
    match = VERSION_PATTERN.match(text.splitlines()[0].strip() if text else "")
    if not match:
        raise SystemExit(f"VERSION 文件内容不是 x.y.z 格式：{text!r}")
    major, minor, patch = (int(group) for group in match.groups())
    return text, (major, minor, patch, 0)


def render(target, version: str, numeric: tuple[int, int, int, int]) -> str:
    file_name, string_table, translation, strings = target
    fields = dict(strings)
    fields["FileVersion"] = version
    fields["ProductVersion"] = version
    # PyInstaller reads the keys in a fixed order; keeping it stable makes a
    # version bump show up as a two-line diff instead of a rewritten file.
    ordered = [
        "CompanyName",
        "FileDescription",
        "FileVersion",
        "InternalName",
        "LegalCopyright",
        "OriginalFilename",
        "ProductName",
        "ProductVersion",
    ]
    body = ",\n".join(
        f"          StringStruct(u'{key}', u'{fields[key]}')" for key in ordered
    )
    prefix = "# UTF-8\n" if file_name == SERVER_TARGET[0] else ""
    return f"""{prefix}VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numeric},
    prodvers={numeric},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'{string_table}',
        [
{body}
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [{translation[0]}, {translation[1]}])])
  ]
)
"""


def _self_check(content: str, version: str) -> None:
    """Fail loudly rather than handing PyInstaller a file it cannot parse.

    The generated text is executed as Python by the build, so a missing comma
    would only surface much later as an opaque packaging error.
    """
    namespace: dict = {}
    try:
        exec(  # noqa: S102 - the content is generated from constants above
            compile(content, "<version_info>", "exec"),
            {
                "VSVersionInfo": lambda **kw: kw,
                "FixedFileInfo": lambda **kw: kw,
                "StringFileInfo": lambda *a: a,
                "StringTable": lambda *a: a,
                "StringStruct": lambda *a: a,
                "VarFileInfo": lambda *a: a,
                "VarStruct": lambda *a: a,
            },
            namespace,
        )
    except SyntaxError as exc:
        raise SystemExit(f"生成的版本资源无法解析：{exc}") from exc
    if version not in content:
        raise SystemExit("生成的版本资源中找不到目标版本号")


def main() -> int:
    version, numeric = read_version()
    changed: list[str] = []
    for target in (DESKTOP_TARGET, SERVER_TARGET):
        path = PACKAGING_DIR / target[0]
        content = render(target, version, numeric)
        _self_check(content, version)
        existing = path.read_text(encoding="utf-8") if path.is_file() else None
        if existing == content:
            continue
        path.write_text(content, encoding="utf-8", newline="\n")
        changed.append(target[0])
    if changed:
        print(f"已按 V{version} 更新：" + "、".join(changed))
    else:
        print(f"版本资源已是 V{version}，无需更新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
