# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH).resolve().parent
# PyInstaller evaluates hook helpers before Analysis applies ``pathex``.  Put
# the backend on sys.path first so every ``app.*`` module is discovered in the
# windowed build instead of relying on imports reached by chance.
backend_root = project_root / "backend"
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))
hidden_imports = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + collect_submodules("openpyxl")
    + collect_submodules("pwdlib")
    + collect_submodules("argon2")
    + ["sqlalchemy.dialects.sqlite"]
)
data_files = [
    (
        str(project_root / "backend" / "app" / "templates" / "word" / "handover_v1.docx"),
        "backend/app/templates/word",
    ),
    (str(project_root / "frontend" / "dist"), "frontend/dist"),
    (
        str(project_root / "resources" / "交接班系统标准导入模板_V0.3.0.xlsx"),
        "resources",
    ),
] + collect_data_files("openpyxl")

a = Analysis(
    [str(project_root / "launcher.py")],
    pathex=[str(project_root), str(project_root / "backend")],
    binaries=[],
    datas=data_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="交接班系统",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "packaging" / "handover.ico"),
    version=str(project_root / "packaging" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="江西片区智能交接班_V0.3.0_win-x64",
)
