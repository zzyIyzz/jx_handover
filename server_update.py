"""Safe, non-destructive preparation of a versioned Windows release package.

This module deliberately does not overwrite the running program directory.
It verifies the published SHA256, rejects unsafe ZIP paths, extracts to a
temporary sibling directory and only then exposes the completed version
folder.  The previous version remains available for rollback.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid
import zipfile


SHA256_PATTERN = re.compile(r"\b([0-9A-Fa-f]{64})\b")
VERSION_PATTERN = re.compile(r"V(\d+\.\d+\.\d+)", re.IGNORECASE)
REQUIRED_FILES = ("服务器控制器.exe", "交接班服务器.exe")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_expected_hash(zip_path: Path, sha_path: Path | None = None) -> tuple[str, Path]:
    candidate = sha_path or Path(f"{zip_path}.sha256")
    if not candidate.is_file():
        raise FileNotFoundError(
            f"未找到校验文件：{candidate.name}。请把 ZIP 和同名 .sha256 放在同一目录。"
        )
    try:
        content = candidate.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        content = candidate.read_text(encoding="ascii")
    match = SHA256_PATTERN.search(content)
    if not match:
        raise ValueError("SHA256 文件中没有有效的 64 位校验值。")
    return match.group(1).upper(), candidate


def _safe_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    return (
        bool(normalized)
        and not normalized.startswith(("/", "\\"))
        and not path.is_absolute()
        and not (len(normalized) >= 2 and normalized[1] == ":")
        and ".." not in path.parts
    )


def inspect_release_package(
    zip_path: Path,
    *,
    sha_path: Path | None = None,
) -> dict:
    zip_path = zip_path.expanduser().resolve()
    if not zip_path.is_file() or zip_path.suffix.lower() != ".zip":
        raise FileNotFoundError("请选择完整的 Windows 发布 ZIP。")
    expected, resolved_sha = _read_expected_hash(zip_path, sha_path)
    actual = _sha256(zip_path)
    if actual != expected:
        raise RuntimeError(
            f"升级包 SHA256 不一致。\n发布值：{expected}\n实际值：{actual}"
        )

    with zipfile.ZipFile(zip_path, "r") as archive:
        infos = archive.infolist()
        if not infos:
            raise RuntimeError("升级 ZIP 是空包。")
        if any(not _safe_member(info.filename) for info in infos):
            raise RuntimeError("升级 ZIP 包含不安全路径，已拒绝解压。")
        broken = archive.testzip()
        if broken:
            raise RuntimeError(f"升级 ZIP 内部文件损坏：{broken}")
        file_names = {
            info.filename.replace("\\", "/").rstrip("/")
            for info in infos
            if not info.is_dir()
        }
        top_levels = {name.split("/", 1)[0] for name in file_names}
        if len(top_levels) != 1:
            raise RuntimeError("升级 ZIP 必须只包含一个完整版本目录。")
        top_level = next(iter(top_levels))
        for required in REQUIRED_FILES:
            member = f"{top_level}/{required}"
            if member not in file_names:
                raise RuntimeError(f"升级包缺少必要程序：{required}")
            info = archive.getinfo(member)
            if info.file_size <= 0:
                raise RuntimeError(f"升级包中的程序为空：{required}")

    match = VERSION_PATTERN.search(top_level)
    version = match.group(1) if match else "unknown"
    return {
        "source_zip": str(zip_path),
        "sha_file": str(resolved_sha.resolve()),
        "sha256": actual,
        "package_size": zip_path.stat().st_size,
        "top_level": top_level,
        "version": version,
        "verified": True,
    }


def _extract_safe(zip_path: Path, staging: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            if not _safe_member(info.filename):
                raise RuntimeError("升级 ZIP 包含不安全路径。")
            destination = (staging / Path(info.filename.replace("\\", "/"))).resolve()
            try:
                destination.relative_to(staging.resolve())
            except ValueError as exc:
                raise RuntimeError("升级 ZIP 解压路径越界。") from exc
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def prepare_release_package(
    zip_path: Path,
    install_parent: Path,
    *,
    sha_path: Path | None = None,
) -> dict:
    """Verify and expose a new version folder without touching older versions."""
    inspected = inspect_release_package(zip_path, sha_path=sha_path)
    install_parent = install_parent.expanduser().resolve()
    install_parent.mkdir(parents=True, exist_ok=True)
    if install_parent == Path(install_parent.anchor):
        raise ValueError("不能把磁盘根目录直接作为版本安装目录。")

    final = install_parent / str(inspected["top_level"])
    prepared_manifest = final / "prepared-update.json"
    if final.exists():
        try:
            existing = json.loads(prepared_manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            existing = {}
        if existing.get("sha256") == inspected["sha256"] and all(
            (final / filename).is_file() for filename in REQUIRED_FILES
        ):
            return {**existing, "install_path": str(final), "already_prepared": True}
        raise FileExistsError(
            f"目标版本目录已存在但无法确认内容一致：{final}。不会覆盖该目录。"
        )

    # Do not repeat a potentially long Chinese release directory name in the
    # staging path; that can exceed Windows' legacy path boundary while the
    # final destination itself is valid.
    staging = install_parent / f".jxupd-{uuid.uuid4().hex[:8]}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        _extract_safe(Path(str(inspected["source_zip"])), staging)
        extracted = staging / str(inspected["top_level"])
        if not extracted.is_dir():
            raise RuntimeError("升级包解压后缺少版本目录。")
        for required in REQUIRED_FILES:
            if not (extracted / required).is_file():
                raise RuntimeError(f"解压后缺少必要程序：{required}")
        manifest = {
            **inspected,
            "prepared_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "install_path": str(final),
            "previous_versions_preserved": True,
            "switch_instructions": [
                "在网页系统管理中创建完整备份。",
                "用旧版控制器安全停止服务器并关闭控制器。",
                "从新版本目录运行服务器控制器并启动服务器。",
                "若验证失败，停止新版并从旧版本目录重新启动。",
            ],
        }
        temporary_manifest = extracted / f".jx-{uuid.uuid4().hex[:8]}.tmp"
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary_manifest, extracted / "prepared-update.json")
        os.replace(extracted, final)
        return {**manifest, "already_prepared": False}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
