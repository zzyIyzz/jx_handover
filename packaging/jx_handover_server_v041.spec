# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).resolve().parent
backend_root = project_root / "backend"
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

server_hidden_imports = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + ["sqlalchemy.dialects.sqlite"]
)
shared_data_files = [
    (
        str(project_root / "backend" / "app" / "templates" / "word" / "handover_v1.docx"),
        "backend/app/templates/word",
    ),
    (str(project_root / "frontend" / "dist"), "frontend/dist"),
    (
        str(project_root / "resources" / "交接班系统标准导入模板_V0.3.0.xlsx"),
        "resources",
    ),
]

server_analysis = Analysis(
    [str(project_root / "server_runner.py")],
    pathex=[str(project_root), str(backend_root)],
    binaries=[],
    datas=shared_data_files,
    hiddenimports=server_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # OpenAI exposes optional dataframe/embedding helpers. This application
    # only uses chat completions, and all XLSX parsing uses openpyxl directly.
    # Excluding these optional stacks cuts the Windows package and startup time
    # without removing any supported handover workflow.
    excludes=["pandas", "numpy", "sounddevice"],
    noarchive=False,
    optimize=0,
)
server_pyz = PYZ(server_analysis.pure)
server_exe = EXE(
    server_pyz,
    server_analysis.scripts,
    [],
    exclude_binaries=True,
    name="交接班服务器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=str(project_root / "packaging" / "handover.ico"),
    version=str(project_root / "packaging" / "version_info_v041_server.txt"),
    uac_admin=False,
)

controller_analysis = Analysis(
    [str(project_root / "server_controller.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
controller_pyz = PYZ(controller_analysis.pure)
controller_exe = EXE(
    controller_pyz,
    controller_analysis.scripts,
    [],
    exclude_binaries=True,
    name="服务器控制器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=str(project_root / "packaging" / "handover.ico"),
    version=str(project_root / "packaging" / "version_info_v041_server.txt"),
    uac_admin=True,
)

distribution = COLLECT(
    server_exe,
    controller_exe,
    server_analysis.binaries,
    server_analysis.datas,
    controller_analysis.binaries,
    controller_analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="江西片区智能交接班_局域网服务器_V0.4.1_win-x64",
)
