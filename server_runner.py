"""Headless V0.4 LAN server process for the Windows server package.

The controller intentionally launches this executable as a detached process.
Closing the controller therefore cannot terminate the shared service.  A
targeted ``stop.request`` file is the only supported stop mechanism; the
runner converts it into Uvicorn's graceful shutdown flag.
"""
from __future__ import annotations

import ctypes
from datetime import datetime
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
import uuid


APP_VERSION = "0.4.0"
HOST = "0.0.0.0"
PORT = 8765
MUTEX_NAME = "Global\\JXHandoverServer-v040"
ERROR_ALREADY_EXISTS = 183


def _resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()


RESOURCE_ROOT = _resource_root()
BACKEND_ROOT = RESOURCE_ROOT / "backend"
if str(RESOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(RESOURCE_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from server_config import (  # noqa: E402
    ERROR_PATH,
    PID_PATH,
    ROOT,
    RUNNER_LOG_PATH,
    SECRETS_PATH,
    SETTINGS_PATH,
    STOP_REQUEST_PATH,
    apply_server_environment,
    configured_data_root,
    load_server_settings,
    save_server_settings,
)


def _configure_logging() -> None:
    RUNNER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        RUNNER_LOG_PATH,
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ))
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_error(message: str) -> None:
    try:
        _atomic_write(ERROR_PATH, message)
    except OSError:
        pass


def _port_is_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", PORT)) == 0


def _acquire_mutex() -> int | None:
    if os.name != "nt":
        return 1
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    error = ctypes.get_last_error()
    if not handle or error == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return int(handle)


def _release_mutex(handle: int | None) -> None:
    if os.name == "nt" and handle:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _write_pid(instance_id: str) -> None:
    _atomic_write(PID_PATH, json.dumps({
        "pid": os.getpid(),
        "instance_id": instance_id,
        "version": APP_VERSION,
        "host": HOST,
        "port": PORT,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2))


def _remove_own_pid(instance_id: str) -> None:
    try:
        state = json.loads(PID_PATH.read_text(encoding="utf-8"))
        if state.get("instance_id") == instance_id:
            PID_PATH.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass


def _monitor_control(server, instance_id: str, daily_backup) -> None:
    next_backup_check = time.monotonic() + 60
    while not server.should_exit:
        if STOP_REQUEST_PATH.exists():
            try:
                request = json.loads(STOP_REQUEST_PATH.read_text(encoding="utf-8"))
                target = str(request.get("instance_id") or "").strip()
                STOP_REQUEST_PATH.unlink(missing_ok=True)
                if target and target != instance_id:
                    logging.warning(
                        "Ignored stale stop request for instance %s (current %s)",
                        target,
                        instance_id,
                    )
                else:
                    logging.info(
                        "Graceful stop requested by %s",
                        request.get("requested_by") or "local-controller",
                    )
                    server.should_exit = True
                    return
            except (OSError, ValueError, TypeError):
                logging.exception("Invalid stop.request; request was ignored")
                try:
                    STOP_REQUEST_PATH.unlink(missing_ok=True)
                except OSError:
                    pass
        if time.monotonic() >= next_backup_check:
            try:
                result = daily_backup()
                if result:
                    logging.info(
                        "Daily database backup completed: %s",
                        result.get("local_path") or result.get("database_file"),
                    )
                    if result.get("nas_error"):
                        logging.warning("NAS backup copy failed: %s", result["nas_error"])
            except Exception:  # noqa: BLE001 - backup failure must not stop service
                logging.exception("Periodic daily database backup check failed")
            next_backup_check = time.monotonic() + 60
        time.sleep(0.4)


def run_server() -> int:
    _configure_logging()
    logging.info("Starting LAN server V%s", APP_VERSION)
    mutex_handle = _acquire_mutex()
    if mutex_handle is None:
        logging.info("Another server runner already owns the global mutex")
        return 0

    instance_id = uuid.uuid4().hex
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_PATH.exists() or not SECRETS_PATH.exists():
            settings, secrets_value = load_server_settings()
            save_server_settings(settings, secrets_value)
        settings, secrets_value = apply_server_environment()
        logging.info(
            "Configuration loaded: public_host=%s data_root=%s auth_required=%s ai_configured=%s nas_configured=%s",
            settings.get("public_host") or "(unset)",
            configured_data_root(settings),
            bool(settings.get("auth_required", True)),
            bool(secrets_value.get("qwen_api_key")),
            bool(settings.get("nas_backup_dir")),
        )

        if _port_is_in_use():
            raise RuntimeError(
                "端口 0.0.0.0:8765 已被占用。服务器未强制结束任何进程。"
            )

        STOP_REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        STOP_REQUEST_PATH.unlink(missing_ok=True)
        ERROR_PATH.unlink(missing_ok=True)
        _write_pid(instance_id)

        # Import only after server settings have populated the environment.
        import uvicorn
        from app.main import app
        from app.services.backup import maybe_daily_backup

        uvicorn_config = uvicorn.Config(
            app,
            host=HOST,
            port=PORT,
            log_config=None,
            access_log=True,
            proxy_headers=False,
            server_header=False,
        )
        server = uvicorn.Server(uvicorn_config)
        monitor = threading.Thread(
            target=_monitor_control,
            args=(server, instance_id, maybe_daily_backup),
            name="server-control-monitor",
            daemon=True,
        )
        monitor.start()
        logging.info("LAN server instance %s listening on %s:%s", instance_id, HOST, PORT)
        server.run()
        if not server.started:
            raise RuntimeError(
                "Web 应用未能完成启动，请查看 server-runner.log 中的启动异常。"
            )
        logging.info("LAN server instance %s stopped gracefully", instance_id)
        return 0
    except Exception as exc:  # noqa: BLE001 - frozen runner must persist diagnostics
        details = f"服务器启动或运行失败：{exc}\n\n{traceback.format_exc()}"
        logging.error(details)
        _write_error(details)
        return 1
    finally:
        _remove_own_pid(instance_id)
        _release_mutex(mutex_handle)


def main() -> None:
    raise SystemExit(run_server())


if __name__ == "__main__":
    main()
