"""DOCX 结构校验：发布前必须通过，损坏文件禁止发布。"""
from pathlib import Path
import zipfile


def validate_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "r") as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"DOCX ZIP 损坏: {bad}")
        if "word/document.xml" not in zf.namelist():
            raise RuntimeError("无效的 DOCX 结构：缺少 word/document.xml")
