"""Windowed administrator controller for the V0.4.1 Windows LAN server."""
from __future__ import annotations

import ctypes
from datetime import datetime
import ipaddress
import json
import logging
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_VERSION = "0.4.1"
PORT = 8765
LOCAL_HEALTH_URL = f"http://127.0.0.1:{PORT}/api/health"
CONTROLLER_MUTEX = "Local\\JXHandoverServerController-v040"
ERROR_ALREADY_EXISTS = 183
HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()


RESOURCE_ROOT = _resource_root()
if str(RESOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(RESOURCE_ROOT))

from server_config import (  # noqa: E402
    APP_VERSION as CONFIG_VERSION,
    DEFAULT_MODEL,
    ERROR_PATH,
    PID_PATH,
    ROOT,
    RUNNER_LOG_PATH,
    STOP_REQUEST_PATH,
    configured_data_root,
    detect_public_hosts,
    load_server_settings,
    public_url,
    save_server_settings,
    validate_local_data_root,
)
from server_migration import migrate_v030_data, relocate_server_data  # noqa: E402
from server_recovery import import_backup_bundle, schedule_imported_restore  # noqa: E402
from server_update import prepare_release_package  # noqa: E402


if CONFIG_VERSION != APP_VERSION:
    raise RuntimeError("服务器控制器与配置模块版本不一致。")


STATE_COLORS = {
    "未启动": "#64748b",
    "正在启动": "#d97706",
    "运行中": "#16803c",
    "停止中": "#d97706",
    "启动失败": "#c62828",
}


def _resolve_ipv4(host: str) -> list[str]:
    try:
        return sorted({
            str(result[4][0])
            for result in socket.getaddrinfo(
                host, None, socket.AF_INET, socket.SOCK_STREAM
            )
            if result[4] and result[4][0]
        })
    except OSError:
        return []


def _is_local_ipv4(address: str) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((address, 0))
        return True
    except OSError:
        return False


def _probe_writable_directory(path: Path) -> float:
    """Verify create, flush, rename, read and delete permissions."""
    started = time.perf_counter()
    path.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    partial = path / f".jx-{token}.tmp"
    verified = path / f".jx-{token}.ok"
    payload = b"JXHandover deployment write test\n"
    try:
        with partial.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial, verified)
        if verified.read_bytes() != payload:
            raise OSError("测试文件写回内容不一致。")
    finally:
        partial.unlink(missing_ok=True)
        verified.unlink(missing_ok=True)
    return round((time.perf_counter() - started) * 1000, 1)


def _health_payload(timeout: float = 0.8) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(LOCAL_HEALTH_URL, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if (
                response.status == 200
                and payload.get("status") == "ok"
                and payload.get("service") == "jx-handover"
                and payload.get("mode") == "server"
                and int(payload.get("port", 0)) == PORT
            ):
                return payload
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        pass
    return None


def _port_is_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", PORT)) == 0


def _load_runner_state() -> dict:
    try:
        value = json.loads(PID_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            import os
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_uint32()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def _runner_is_starting() -> bool:
    state = _load_runner_state()
    try:
        return _pid_is_alive(int(state.get("pid", 0)))
    except (ValueError, TypeError):
        return False


def _atomic_stop_request() -> dict:
    state = _load_runner_state()
    request = {
        "instance_id": str(state.get("instance_id") or ""),
        "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "requested_by": "server-controller",
        "action": "graceful-stop",
    }
    STOP_REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STOP_REQUEST_PATH.with_suffix(".request.tmp")
    temporary.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STOP_REQUEST_PATH)
    return request


def _read_runner_error() -> str:
    try:
        return ERROR_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _acquire_controller_mutex() -> int | None:
    if sys.platform != "win32":
        return 1
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.CreateMutexW(None, False, CONTROLLER_MUTEX)
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return int(handle)


def _release_controller_mutex(handle: int | None) -> None:
    if sys.platform == "win32" and handle:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(ctypes.c_void_p(handle))


class ServerController:
    def __init__(self, root: tk.Tk, mutex_handle: int | None) -> None:
        self.root = root
        self.mutex_handle = mutex_handle
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.state = "未启动"
        self.restart_after_stop = False
        self.restore_starting = False
        self.migration_in_progress = False
        self.update_in_progress = False
        self.recovery_in_progress = False
        self.last_error = ""
        self.settings, self.secrets_value = load_server_settings()
        self.status_var = tk.StringVar(value=self.state)
        self.detail_var = tk.StringVar(value="正在检查服务器运行状态……")
        self.address_var = tk.StringVar(value=public_url(self.settings) or "尚未设置访问地址")
        self.public_host_var = tk.StringVar(value=str(self.settings.get("public_host") or ""))
        self.data_root_var = tk.StringVar(value=str(configured_data_root(self.settings)))
        self.qwen_url_var = tk.StringVar(value=str(self.settings.get("qwen_base_url") or ""))
        self.qwen_key_var = tk.StringVar(value=str(self.secrets_value.get("qwen_api_key") or ""))
        self.access_code_var = tk.StringVar(value=str(self.secrets_value.get("access_code") or ""))
        self.admin_names_var = tk.StringVar(value=str(self.settings.get("admin_names") or ""))
        self.nas_backup_var = tk.StringVar(value=str(self.settings.get("nas_backup_dir") or ""))
        self.auth_required_var = tk.BooleanVar(value=bool(self.settings.get("auth_required", True)))
        self.auto_open_var = tk.BooleanVar(value=bool(self.settings.get("auto_open_browser", True)))
        self._configure_logging()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(150, self._initial_refresh)
        self.root.after(200, self._drain_events)
        self.root.after(2500, self._periodic_refresh)

    def _configure_logging(self) -> None:
        log_path = ROOT / "logs" / f"server-controller-{datetime.now():%Y-%m-%d}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            encoding="utf-8",
            force=True,
        )

    def _build_ui(self) -> None:
        self.root.title(f"交接班服务器控制器 V{APP_VERSION}")
        self.root.geometry("920x780")
        self.root.minsize(840, 720)
        self.root.configure(background="#f3f6fa")
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("TButton", font=("Microsoft YaHei UI", 9), padding=(10, 7))
        style.configure("TCheckbutton", font=("Microsoft YaHei UI", 9))
        style.configure("TNotebook.Tab", font=("Microsoft YaHei UI", 10), padding=(14, 8))

        header = tk.Frame(self.root, bg="#173b61", padx=24, pady=17)
        header.pack(fill="x")
        tk.Label(
            header,
            text="江西片区智能交接班 · 局域网服务器",
            font=("Microsoft YaHei UI", 19, "bold"),
            fg="white",
            bg="#173b61",
        ).pack(anchor="w")
        tk.Label(
            header,
            text="管理员控制器可随时关闭，后台服务器会继续运行",
            font=("Microsoft YaHei UI", 9),
            fg="#c9d9e8",
            bg="#173b61",
        ).pack(anchor="w", pady=(3, 0))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=20, pady=18)
        run_tab = tk.Frame(notebook, bg="#f3f6fa")
        settings_tab = tk.Frame(notebook, bg="#f3f6fa")
        notebook.add(run_tab, text="运行管理")
        notebook.add(settings_tab, text="服务器设置")
        self._build_run_tab(run_tab)
        self._build_settings_tab(settings_tab)

    def _build_run_tab(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg="white", highlightthickness=1, highlightbackground="#d9e3ed")
        card.pack(fill="x", pady=(4, 16))
        self.status_label = tk.Label(
            card,
            textvariable=self.status_var,
            font=("Microsoft YaHei UI", 17, "bold"),
            fg="white",
            bg=STATE_COLORS[self.state],
            padx=18,
            pady=10,
        )
        self.status_label.pack(side="left", padx=16, pady=16)
        detail = tk.Frame(card, bg="white")
        detail.pack(side="left", fill="both", expand=True, padx=(0, 16), pady=12)
        tk.Label(
            detail,
            textvariable=self.detail_var,
            font=("Microsoft YaHei UI", 10),
            fg="#304a63",
            bg="white",
            justify="left",
            wraplength=640,
        ).pack(anchor="w")
        tk.Label(
            detail,
            textvariable=self.address_var,
            font=("Microsoft YaHei UI", 10, "bold"),
            fg="#1769aa",
            bg="white",
        ).pack(anchor="w", pady=(5, 0))

        buttons = tk.Frame(parent, bg="#f3f6fa")
        buttons.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(buttons, text="启动服务器", command=self.start)
        self.start_button.pack(side="left", padx=(0, 7))
        self.open_button = ttk.Button(buttons, text="打开系统", command=self.open_system)
        self.open_button.pack(side="left", padx=7)
        self.stop_button = ttk.Button(buttons, text="安全停止", command=self.stop)
        self.stop_button.pack(side="left", padx=7)
        self.restart_button = ttk.Button(buttons, text="重启服务器", command=self.restart)
        self.restart_button.pack(side="left", padx=7)

        tools = tk.LabelFrame(
            parent,
            text="部署与维护",
            bg="#f3f6fa",
            fg="#304a63",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=12,
            pady=12,
        )
        tools.pack(fill="x", pady=(4, 14))
        ttk.Button(tools, text="打开正式数据目录", command=self.open_data_root).grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        ttk.Button(tools, text="打开运行日志", command=lambda: self._open_path(RUNNER_LOG_PATH.parent)).grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        ttk.Button(tools, text="复制错误信息", command=self.copy_error).grid(row=0, column=2, padx=5, pady=5, sticky="ew")
        ttk.Button(tools, text="生成共享盘网页入口", command=self.generate_shortcut).grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        ttk.Button(tools, text="安装开机自动运行", command=self.install_autostart).grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        ttk.Button(tools, text="卸载开机自动运行", command=self.uninstall_autostart).grid(row=1, column=2, padx=5, pady=5, sticky="ew")
        self.migration_button = ttk.Button(
            tools, text="迁移 V0.3 单机数据", command=self.migrate_v030
        )
        self.migration_button.grid(row=2, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        self.relocation_button = ttk.Button(
            tools, text="迁移正式数据目录", command=self.relocate_data_root
        )
        self.relocation_button.grid(row=2, column=0, padx=5, pady=5, sticky="ew")
        ttk.Button(
            tools, text="打开完整备份目录", command=self.open_full_backups
        ).grid(row=3, column=0, padx=5, pady=5, sticky="ew")
        ttk.Button(
            tools, text="查看 / 取消待恢复", command=self.manage_pending_restore
        ).grid(row=3, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        self.update_button = ttk.Button(
            tools, text="校验并准备新版本（不覆盖旧版）", command=self.prepare_update
        )
        self.update_button.grid(
            row=4, column=0, columnspan=3, padx=5, pady=5, sticky="ew"
        )
        self.recovery_button = ttk.Button(
            tools,
            text="从 NAS 导入完整备份并安排恢复",
            command=self.import_nas_backup,
        )
        self.recovery_button.grid(
            row=5, column=0, columnspan=3, padx=5, pady=5, sticky="ew"
        )
        for column in range(3):
            tools.grid_columnconfigure(column, weight=1)

        info = tk.Frame(parent, bg="#e7f1fb", padx=14, pady=12)
        info.pack(fill="both", expand=True)
        tk.Label(
            info,
            text="运行原则",
            font=("Microsoft YaHei UI", 10, "bold"),
            fg="#214b72",
            bg="#e7f1fb",
        ).pack(anchor="w")
        tk.Label(
            info,
            text=(
                "• 后台服务器与本控制器是两个独立进程，关闭控制器不会影响值班人员。\n"
                "• “安全停止”只写入目标实例的 stop.request，绝不按端口或 PID 强杀进程。\n"
                "• 正式数据库必须保存在服务器本地；NAS 只接收已完成并校验的备份副本。\n"
                "• 完整备份同时保留数据库、导入原件和历史 Word；恢复只在安全重启时执行。\n"
                "• 配置修改需要重启服务器后生效，Qwen Key 不会发送到浏览器。"
            ),
            font=("Microsoft YaHei UI", 9),
            fg="#3f607d",
            bg="#e7f1fb",
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

    def _build_settings_tab(self, parent: tk.Frame) -> None:
        form = tk.Frame(parent, bg="white", padx=22, pady=18, highlightthickness=1, highlightbackground="#d9e3ed")
        form.pack(fill="both", expand=True, pady=4)

        def row(label: str, variable: tk.StringVar, index: int, *, show: str | None = None) -> ttk.Entry:
            tk.Label(
                form,
                text=label,
                font=("Microsoft YaHei UI", 9),
                fg="#334e68",
                bg="white",
            ).grid(row=index, column=0, sticky="w", padx=(0, 12), pady=7)
            entry = ttk.Entry(form, textvariable=variable, show=show or "")
            entry.grid(row=index, column=1, sticky="ew", pady=7)
            return entry

        tk.Label(
            form,
            text="局域网 IP 或主机名",
            font=("Microsoft YaHei UI", 9),
            fg="#334e68",
            bg="white",
        ).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=7)
        host_line = tk.Frame(form, bg="white")
        host_line.grid(row=0, column=1, sticky="ew", pady=7)
        self.host_combo = ttk.Combobox(
            host_line,
            textvariable=self.public_host_var,
            values=detect_public_hosts(),
        )
        self.host_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(
            host_line, text="重新检测", command=self.refresh_ip_candidates
        ).pack(side="left", padx=(8, 0))

        row("Qwen API 地址", self.qwen_url_var, 1)
        row("Qwen 模型", tk.StringVar(value=DEFAULT_MODEL), 2).configure(state="readonly")
        row("Qwen API Key", self.qwen_key_var, 3, show="●")
        row("系统访问口令", self.access_code_var, 4, show="●")
        row("管理员姓名（逗号分隔）", self.admin_names_var, 5)

        tk.Label(
            form,
            text="正式数据目录（服务器本地）",
            font=("Microsoft YaHei UI", 9),
            fg="#334e68",
            bg="white",
        ).grid(row=6, column=0, sticky="w", padx=(0, 12), pady=7)
        data_line = tk.Frame(form, bg="white")
        data_line.grid(row=6, column=1, sticky="ew", pady=7)
        ttk.Entry(data_line, textvariable=self.data_root_var).pack(side="left", fill="x", expand=True)
        ttk.Button(data_line, text="选择…", command=self.select_data_dir).pack(side="left", padx=(8, 0))

        tk.Label(
            form,
            text="NAS/云盘备份目录",
            font=("Microsoft YaHei UI", 9),
            fg="#334e68",
            bg="white",
        ).grid(row=7, column=0, sticky="w", padx=(0, 12), pady=7)
        nas_line = tk.Frame(form, bg="white")
        nas_line.grid(row=7, column=1, sticky="ew", pady=7)
        ttk.Entry(nas_line, textvariable=self.nas_backup_var).pack(side="left", fill="x", expand=True)
        ttk.Button(nas_line, text="选择…", command=self.select_nas_dir).pack(side="left", padx=(8, 0))

        ttk.Checkbutton(
            form,
            text="要求每位使用者选择姓名并输入访问口令",
            variable=self.auth_required_var,
        ).grid(row=8, column=1, sticky="w", pady=(10, 5))
        ttk.Checkbutton(
            form,
            text="启动成功后在服务器上自动打开浏览器",
            variable=self.auto_open_var,
        ).grid(row=9, column=1, sticky="w", pady=5)

        notice = tk.Label(
            form,
            text=(
                "Key 与访问口令使用 Windows DPAPI 机器级加密保存，并限制密钥文件为 SYSTEM 和本机管理员可读。\n"
                "正式数据目录只允许本机固定磁盘；\\\\共享服务器和映射网络盘请填写到 NAS/云盘备份目录。\n"
                "不要填写公网 IP；本系统仅面向可信局域网，不应把 8765 映射到互联网。"
            ),
            font=("Microsoft YaHei UI", 8),
            fg="#7a4b13",
            bg="#fff7e8",
            justify="left",
            padx=10,
            pady=9,
        )
        notice.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(12, 12))

        actions = tk.Frame(form, bg="white")
        actions.grid(row=11, column=1, sticky="e", pady=(5, 0))
        ttk.Button(actions, text="测试当前配置", command=self.test_configuration).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="保存服务器设置", command=self.save_settings).pack(side="left")
        form.grid_columnconfigure(1, weight=1)

    def refresh_ip_candidates(self) -> None:
        candidates = detect_public_hosts()
        self.host_combo.configure(values=candidates)
        current = self.public_host_var.get().strip()
        if candidates and (not current or current.startswith(("127.", "169.254."))):
            self.public_host_var.set(candidates[0])
        message = (
            "检测到本机局域网 IPv4：\n" + "\n".join(candidates)
            if candidates
            else "没有自动检测到可用 IPv4。请运行 ipconfig，并填写服务器网卡的 IPv4 地址。"
        )
        messagebox.showinfo("本机 IP 检测", message, parent=self.root)

    def select_data_dir(self) -> None:
        initial = self.data_root_var.get().strip()
        selected = filedialog.askdirectory(
            title="选择服务器本地正式数据目录（不能选择共享盘或映射网络盘）",
            initialdir=initial if initial and Path(initial).exists() else None,
            parent=self.root,
        )
        if selected:
            self.data_root_var.set(selected)

    def open_data_root(self) -> None:
        try:
            root = validate_local_data_root(self.data_root_var.get(), create=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("正式数据目录不可用", str(exc), parent=self.root)
            return
        self._open_path(root)

    def _restore_directory(self) -> Path:
        return configured_data_root(self.settings) / "snapshots" / "restore"

    def _pending_restore(self) -> dict | None:
        path = self._restore_directory() / "pending.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    def open_full_backups(self) -> None:
        self._open_path(
            configured_data_root(self.settings) / "snapshots" / "full_backups"
        )

    def manage_pending_restore(self) -> None:
        pending_path = self._restore_directory() / "pending.json"
        pending = self._pending_restore()
        if pending is None:
            result_path = self._restore_directory() / "last-result.json"
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                result = None
            if isinstance(result, dict):
                state = "完成" if result.get("state") == "completed" else "失败"
                messagebox.showinfo(
                    "当前没有待恢复任务",
                    f"上次恢复状态：{state}\n"
                    f"备份编号：{result.get('backup_id') or '—'}\n"
                    f"时间：{result.get('completed_at') or result.get('failed_at') or '—'}\n"
                    f"说明：{result.get('error') or '恢复前数据已另行完整备份。'}",
                    parent=self.root,
                )
            else:
                messagebox.showinfo(
                    "当前没有待恢复任务",
                    "请在网页右上角“系统管理”中选择已经校验的完整备份并安排恢复。",
                    parent=self.root,
                )
            return
        if messagebox.askyesno(
            "已有待恢复任务",
            f"备份编号：{pending.get('backup_id') or '—'}\n"
            f"安排人：{pending.get('requested_by') or '—'}\n"
            f"安排时间：{pending.get('requested_at') or '—'}\n\n"
            "点击“是”将取消这次待恢复任务。当前数据库和备份文件不会发生变化。",
            parent=self.root,
        ):
            try:
                pending_path.unlink(missing_ok=True)
                messagebox.showinfo(
                    "已取消待恢复",
                    "当前正式数据没有变化，完整备份仍然保留。",
                    parent=self.root,
                )
            except OSError as exc:
                messagebox.showerror("取消失败", str(exc), parent=self.root)

    def test_configuration(self) -> None:
        collected = self._validate_and_collect()
        if collected is None:
            return
        settings, _ = collected
        host = str(settings["public_host"])
        data_root = configured_data_root(settings)
        local_addresses = detect_public_hosts()
        resolved = _resolve_ipv4(host)
        checks: list[dict[str, object]] = []

        host_ok = any(_is_local_ipv4(address) for address in resolved)
        checks.append({
            "name": "局域网访问地址",
            "ok": host_ok,
            "detail": (
                f"{public_url(settings)}（解析为 {', '.join(resolved)}）"
                if resolved
                else f"{host} 当前无法解析为 IPv4"
            ),
        })

        try:
            local_ms = _probe_writable_directory(data_root)
            checks.append({
                "name": "正式数据目录",
                "ok": True,
                "detail": f"本机固定磁盘可写，可创建/改名/读取/删除（{local_ms} ms）：{data_root}",
            })
        except OSError as exc:
            checks.append({"name": "正式数据目录", "ok": False, "detail": str(exc)})

        nas_value = str(settings.get("nas_backup_dir") or "").strip()
        if nas_value:
            try:
                nas_ms = _probe_writable_directory(Path(nas_value))
                checks.append({
                    "name": "NAS/云盘备份目录",
                    "ok": True,
                    "detail": f"当前登录账户具备备份所需权限（{nas_ms} ms）：{nas_value}",
                })
            except OSError as exc:
                checks.append({
                    "name": "NAS/云盘备份目录",
                    "ok": False,
                    "detail": f"{nas_value}：{exc}",
                })
        else:
            checks.append({
                "name": "NAS/云盘备份目录",
                "ok": False,
                "warning": True,
                "detail": "尚未填写；系统仍可运行，但不会生成共享盘备份副本。",
            })

        payload = _health_payload(timeout=0.8)
        if payload:
            port_detail = "本系统已在 0.0.0.0:8765 正常运行。"
            port_ok = True
        elif _port_is_in_use():
            port_detail = "8765 已被其他程序占用。"
            port_ok = False
        else:
            port_detail = "8765 当前空闲，启动服务器后将监听所有局域网网卡。"
            port_ok = True
        checks.append({"name": "服务端口", "ok": port_ok, "detail": port_detail})

        report = {
            "tested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "version": APP_VERSION,
            "public_url": public_url(settings),
            "configured_host": host,
            "detected_local_ipv4": local_addresses,
            "data_root": str(data_root),
            "nas_backup_dir": nas_value,
            "checks": checks,
            "note": "NAS 权限由当前登录账户测试；SYSTEM 开机任务必须另行执行一次后台备份验证。",
        }
        report_dir = ROOT / "deployment-tests"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"部署配置测试_{datetime.now():%Y%m%d-%H%M%S}.json"
        temporary = report_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, report_path)

        lines = []
        for check in checks:
            marker = "通过" if check.get("ok") else ("提醒" if check.get("warning") else "失败")
            lines.append(f"[{marker}] {check['name']}：{check['detail']}")
        lines.extend([
            "",
            "注意：NAS 检测使用当前登录账户。安装 SYSTEM 开机自启后，仍需在网页管理员面板点击一次“立即备份”验证 SYSTEM 权限。",
            f"测试报告：{report_path}",
        ])
        failed = any(not item.get("ok") and not item.get("warning") for item in checks)
        if failed:
            messagebox.showwarning("配置测试发现问题", "\n".join(lines), parent=self.root)
        else:
            messagebox.showinfo("配置测试通过", "\n".join(lines), parent=self.root)

    def _set_state(self, state: str, detail: str) -> None:
        self.state = state
        self.status_var.set(state)
        self.detail_var.set(detail)
        self.status_label.configure(bg=STATE_COLORS[state])
        running = state == "运行中"
        busy = state in {"正在启动", "停止中"}
        self.start_button.configure(state="disabled" if running or busy else "normal")
        self.open_button.configure(state="normal" if running else "disabled")
        self.stop_button.configure(state="normal" if running or _runner_is_starting() else "disabled")
        self.restart_button.configure(state="normal" if running else "disabled")
        logging.info("State=%s detail=%s", state, detail)

    def _initial_refresh(self) -> None:
        payload = _health_payload()
        if payload:
            self._show_running(payload)
        elif _port_is_in_use():
            self._failure("8765 端口已被其他程序占用；控制器不会结束该进程。")
        elif _runner_is_starting():
            self._set_state("正在启动", "后台服务器进程已创建，正在等待健康检查……")
        else:
            self._set_state("未启动", "服务器尚未运行。请先保存设置，再点击“启动服务器”。")

    def _periodic_refresh(self) -> None:
        if self.state not in {"正在启动", "停止中"}:
            payload = _health_payload(timeout=0.5)
            if payload and self.state != "运行中":
                self._show_running(payload)
            elif not payload and self.state == "运行中":
                self._set_state("未启动", "服务器已停止或暂时不可访问。")
        self.root.after(2500, self._periodic_refresh)

    def _show_running(self, payload: dict) -> None:
        url = str(payload.get("public_url") or public_url(self.settings) or "")
        if url:
            self.address_var.set(url)
        self._set_state(
            "运行中",
            f"服务器 V{payload.get('version', APP_VERSION)} 正常运行，监听 0.0.0.0:{PORT}。",
        )

    def _validate_and_collect(self) -> tuple[dict, dict] | None:
        host = self.public_host_var.get().strip()
        if not host or not HOST_PATTERN.fullmatch(host) or host.startswith(("127.", "169.254.")):
            messagebox.showerror(
                "访问地址无效",
                "请填写服务器固定局域网 IP 或主机名，例如 192.168.14.60。",
                parent=self.root,
            )
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (
            address.version != 4
            or address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
            or not address.is_private
        ):
            messagebox.showerror(
                "访问地址无效",
                "请填写服务器网卡的固定局域网 IPv4，例如 192.168.14.60；不能填写 127.0.0.1、0.0.0.0 或 IPv6。",
                parent=self.root,
            )
            return None
        try:
            data_root = validate_local_data_root(self.data_root_var.get(), create=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("正式数据目录不可用", str(exc), parent=self.root)
            return None
        previous_data_root = configured_data_root(self.settings)
        if data_root != previous_data_root:
            if _health_payload() or _runner_is_starting():
                messagebox.showerror(
                    "请先安全停止服务器",
                    "更改正式数据目录前必须先安全停止服务器。",
                    parent=self.root,
                )
                return None
            previous_database = previous_data_root / "data" / "handover.db"
            if previous_database.exists() and previous_database.stat().st_size:
                messagebox.showerror(
                    "不能直接切换正式数据库",
                    f"原目录中已有正式数据库：\n{previous_database}\n\n"
                    "为避免出现两套数据，不能只改路径。请先保留当前设置并创建备份；"
                    "如需迁移到另一块本地磁盘，请在“运行管理”点击“迁移正式数据目录”。",
                    parent=self.root,
                )
                return None
        qwen_url = self.qwen_url_var.get().strip().rstrip("/")
        if not qwen_url.startswith("https://") or " " in qwen_url:
            messagebox.showerror("API 地址无效", "Qwen API 地址必须是 https:// 开头的完整地址。", parent=self.root)
            return None
        access_code = self.access_code_var.get().strip()
        if access_code and len(access_code) < 6:
            messagebox.showerror("访问口令过短", "访问口令至少需要 6 个字符。", parent=self.root)
            return None
        if self.auth_required_var.get() and not access_code:
            if not messagebox.askyesno(
                "尚未设置访问口令",
                "所有人仍需选择姓名，但当前没有访问口令。确认继续保存吗？",
                parent=self.root,
            ):
                return None
        admin_names = self.admin_names_var.get().replace("，", ",")
        admin_names = ",".join(dict.fromkeys(
            value.strip() for value in admin_names.split(",") if value.strip()
        ))
        settings = {
            "public_host": host,
            "data_root": str(data_root),
            "qwen_base_url": qwen_url,
            "qwen_model": DEFAULT_MODEL,
            "admin_names": admin_names,
            "nas_backup_dir": self.nas_backup_var.get().strip(),
            "auto_open_browser": bool(self.auto_open_var.get()),
            "auth_required": bool(self.auth_required_var.get()),
        }
        secrets_value = {
            "qwen_api_key": self.qwen_key_var.get().strip(),
            "access_code": access_code,
        }
        return settings, secrets_value

    def save_settings(self, *, notify: bool = True) -> bool:
        collected = self._validate_and_collect()
        if collected is None:
            return False
        settings, secrets_value = collected
        try:
            save_server_settings(settings, secrets_value)
            self.settings, self.secrets_value = settings, secrets_value
            self.data_root_var.set(str(configured_data_root(settings)))
            self.address_var.set(public_url(settings))
            logging.info(
                "Settings saved public_host=%s data_root=%s ai_configured=%s nas_configured=%s",
                settings["public_host"],
                settings["data_root"],
                bool(secrets_value["qwen_api_key"]),
                bool(settings["nas_backup_dir"]),
            )
        except Exception as exc:  # noqa: BLE001
            self._failure(f"保存设置失败：{exc}")
            messagebox.showerror(
                "无法保存设置",
                f"{exc}\n\n请用管理员身份运行控制器，并确认设置目录和正式数据目录位于本机 NTFS 固定磁盘。",
                parent=self.root,
            )
            return False
        if notify:
            suffix = "；请重启服务器使新配置生效。" if _health_payload() else "。"
            messagebox.showinfo("设置已保存", f"服务器设置已安全保存{suffix}", parent=self.root)
        return True

    def _runner_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            runner = Path(sys.executable).resolve().parent / "交接班服务器.exe"
            if not runner.exists():
                raise FileNotFoundError(f"发布目录中缺少 {runner.name}")
            return [str(runner)]
        return [sys.executable, str(Path(__file__).resolve().parent / "server_runner.py")]

    def start(self) -> None:
        if self.migration_in_progress or self.update_in_progress or self.recovery_in_progress:
            messagebox.showinfo(
                "维护操作进行中",
                "请等待数据迁移或升级包准备完成后再启动服务器。",
                parent=self.root,
            )
            return
        payload = _health_payload()
        if payload:
            self._show_running(payload)
            self.open_system()
            return
        if _port_is_in_use():
            self._failure("8765 端口已被其他程序占用；未结束任何进程。")
            return
        if not self.save_settings(notify=False):
            return
        pending = self._pending_restore()
        self.restore_starting = pending is not None
        if pending and not messagebox.askyesno(
            "启动时将执行数据恢复",
            f"已安排从完整备份恢复：\n{pending.get('backup_id') or '未知编号'}\n\n"
            "启动过程会先完整备份当前数据，再校验并恢复数据库、导入原件和历史 Word。"
            "数据量较大时可能需要几分钟，期间请勿关闭电脑。是否继续启动？",
            parent=self.root,
        ):
            self.restore_starting = False
            self._set_state("未启动", "已取消本次启动；待恢复任务仍保留。")
            return
        detail = (
            "正在校验备份、保存当前数据并执行恢复；完成后会自动启动网页服务……"
            if self.restore_starting
            else "正在启动独立后台服务器并等待健康检查……"
        )
        self._set_state("正在启动", detail)
        threading.Thread(target=self._start_worker, name="server-start", daemon=True).start()

    def _start_worker(self) -> None:
        try:
            command = self._runner_command()
            flags = 0
            if sys.platform == "win32":
                flags = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            subprocess.Popen(
                command,
                cwd=str(Path(command[0]).resolve().parent if getattr(sys, "frozen", False) else RESOURCE_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
            )
            timeout_seconds = 1800 if self.restore_starting else 60
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                payload = _health_payload(timeout=0.6)
                if payload:
                    self.events.put(("started", payload))
                    return
                if _port_is_in_use() and not _runner_is_starting():
                    break
                error = _read_runner_error()
                if error:
                    raise RuntimeError(error.splitlines()[0])
                time.sleep(0.35)
            error = _read_runner_error()
            raise RuntimeError(
                error.splitlines()[0]
                if error
                else f"{timeout_seconds} 秒内未通过健康检查，请打开运行日志。"
            )
        except Exception as exc:  # noqa: BLE001
            self.events.put(("failure", f"启动服务器失败：{exc}"))

    def stop(self) -> None:
        if not _health_payload() and not _runner_is_starting():
            self._set_state("未启动", "服务器已经停止。")
            return
        try:
            request = _atomic_stop_request()
            logging.info("Stop requested for %s", request.get("instance_id") or "current instance")
        except OSError as exc:
            self._failure(f"无法写入安全停止请求：{exc}")
            return
        self._set_state("停止中", "已提交优雅停止请求，正在等待当前请求处理完成……")
        threading.Thread(target=self._stop_worker, name="server-stop", daemon=True).start()

    def _stop_worker(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if not _health_payload(timeout=0.5) and not _runner_is_starting():
                self.events.put(("stopped", None))
                return
            time.sleep(0.4)
        self.events.put((
            "failure",
            "安全停止等待超时；没有强制结束服务器。请查看日志后再次尝试。",
        ))

    def restart(self) -> None:
        self.restart_after_stop = True
        self.stop()

    def open_system(self) -> None:
        if not _health_payload():
            messagebox.showinfo("服务器未运行", "请先启动服务器。", parent=self.root)
            return
        url = public_url(self.settings) or f"http://127.0.0.1:{PORT}"
        webbrowser.open(url)

    def select_nas_dir(self) -> None:
        selected = filedialog.askdirectory(title="选择 NAS 备份目录", parent=self.root)
        if selected:
            self.nas_backup_var.set(selected)

    def relocate_data_root(self) -> None:
        if self.update_in_progress or self.recovery_in_progress:
            messagebox.showinfo(
                "维护操作进行中", "请等待当前维护操作完成。", parent=self.root
            )
            return
        if _health_payload() or _runner_is_starting():
            messagebox.showwarning(
                "请先停止服务器",
                "迁移正式数据目录前必须先点击“安全停止”，确认状态为“未启动”。",
                parent=self.root,
            )
            return
        source_root = configured_data_root(self.settings)
        source_database = source_root / "data" / "handover.db"
        if not source_database.is_file():
            messagebox.showinfo(
                "尚无正式数据库",
                "当前还没有正式数据库，无需迁移。请直接在“服务器设置”选择本地正式数据目录并保存。",
                parent=self.root,
            )
            return
        selected = filedialog.askdirectory(
            title="选择一个空的服务器本地固定盘目录",
            parent=self.root,
        )
        if not selected:
            return
        try:
            target_root = validate_local_data_root(selected, create=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("新正式数据目录不可用", str(exc), parent=self.root)
            return
        if target_root == source_root:
            messagebox.showinfo("无需迁移", "新旧正式数据目录相同。", parent=self.root)
            return
        try:
            has_files = any(target_root.iterdir())
        except OSError as exc:
            messagebox.showerror("无法检查目标目录", str(exc), parent=self.root)
            return
        if has_files:
            messagebox.showerror(
                "目标目录必须为空",
                "请选择或新建一个空目录，避免把两套正式数据混在一起。",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "确认迁移正式数据目录",
            f"旧目录：\n{source_root}\n\n新目录：\n{target_root}\n\n"
            "系统将一致复制数据库、导入原件、历史 Word 和快照，完整校验后才切换设置；旧目录不会删除。是否继续？",
            parent=self.root,
        ):
            return
        self.migration_in_progress = True
        self.migration_button.configure(state="disabled")
        self.relocation_button.configure(state="disabled")
        self.update_button.configure(state="disabled")
        self.recovery_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.detail_var.set("正在复制并校验正式数据，请不要关闭控制器或启动服务器……")
        threading.Thread(
            target=self._relocation_worker,
            args=(source_root, target_root),
            name="server-data-relocation",
            daemon=True,
        ).start()

    def _relocation_worker(self, source_root: Path, target_root: Path) -> None:
        try:
            result = relocate_server_data(source_root, target_root)
            updated_settings = dict(self.settings)
            updated_settings["data_root"] = str(target_root)
            save_server_settings(updated_settings, self.secrets_value)
            result["settings"] = updated_settings
            self.events.put(("relocation_done", result))
        except Exception as exc:  # noqa: BLE001 - surfaced to administrator
            logging.exception("Server data-root relocation failed")
            self.events.put(("relocation_failure", str(exc)))

    def migrate_v030(self) -> None:
        if self.update_in_progress or self.recovery_in_progress:
            messagebox.showinfo(
                "维护操作进行中", "请等待当前维护操作完成。", parent=self.root
            )
            return
        if _health_payload() or _runner_is_starting():
            messagebox.showwarning(
                "请先停止服务器",
                "迁移旧版数据前必须先点击“安全停止”，确认状态为“未启动”。",
                parent=self.root,
            )
            return
        if not self.save_settings(notify=False):
            return
        target_root = configured_data_root(self.settings)
        selected = filedialog.askdirectory(
            title="选择旧版 JXHandover、runtime 或项目目录",
            parent=self.root,
        )
        if not selected:
            return
        target_exists = (target_root / "data" / "handover.db").exists()
        warning = (
            "当前服务器数据库会先生成一致备份，然后由所选 V0.3 数据替换。\n"
            if target_exists else "当前服务器还没有数据库，将导入所选 V0.3 数据。\n"
        )
        if not messagebox.askyesno(
            "确认迁移 V0.3 数据",
            f"{warning}\n同时迁移导入原件、历史 Word 和快照，并修正历史文档路径。"
            "迁移期间不要启动服务器。是否继续？",
            parent=self.root,
        ):
            return
        self.migration_in_progress = True
        self.migration_button.configure(state="disabled")
        self.relocation_button.configure(state="disabled")
        self.update_button.configure(state="disabled")
        self.recovery_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.detail_var.set("正在一致备份并迁移 V0.3 数据，请不要关闭控制器……")
        threading.Thread(
            target=self._migration_worker,
            args=(Path(selected), target_root),
            name="v030-data-migration",
            daemon=True,
        ).start()

    def _migration_worker(self, selected: Path, target_root: Path) -> None:
        try:
            result = migrate_v030_data(selected, target_root)
            self.events.put(("migration_done", result))
        except Exception as exc:  # noqa: BLE001 - surfaced to administrator
            logging.exception("V0.3 data migration failed")
            self.events.put(("migration_failure", str(exc)))

    def generate_shortcut(self) -> None:
        collected = self._validate_and_collect()
        if collected is None:
            return
        settings, _ = collected
        url = public_url(settings)
        initial = settings.get("nas_backup_dir") or ""
        selected = filedialog.askdirectory(
            title="选择共享盘中的快捷入口目录",
            initialdir=initial if initial and Path(initial).exists() else None,
            parent=self.root,
        )
        if not selected:
            return
        target = Path(selected) / "江西片区智能交接班系统.url"
        try:
            target.write_text(f"[InternetShortcut]\nURL={url}/\n", encoding="utf-8-sig")
            messagebox.showinfo(
                "网页入口已生成",
                f"已生成：\n{target}\n\n值班人员双击即可打开 {url}",
                parent=self.root,
            )
        except OSError as exc:
            self._failure(f"生成共享盘网页入口失败：{exc}")
            messagebox.showerror("生成失败", str(exc), parent=self.root)

    def prepare_update(self) -> None:
        if self.migration_in_progress or self.update_in_progress or self.recovery_in_progress:
            messagebox.showinfo(
                "维护操作进行中", "请等待当前维护操作完成。", parent=self.root
            )
            return
        selected = filedialog.askopenfilename(
            title="选择新版完整 ZIP（同目录必须有 .sha256）",
            filetypes=[("Windows 发布包", "*.zip")],
            parent=self.root,
        )
        if not selected:
            return
        current_directory = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else RESOURCE_ROOT
        )
        initial_parent = current_directory.parent
        install_parent = filedialog.askdirectory(
            title="选择版本安装总目录（新旧版本将并列保留）",
            initialdir=str(initial_parent),
            parent=self.root,
        )
        if not install_parent:
            return
        if not messagebox.askyesno(
            "准备新版本",
            f"升级包：\n{selected}\n\n版本安装总目录：\n{install_parent}\n\n"
            "系统将核对 SHA256、检查 ZIP 结构并解压到新目录，不覆盖或删除当前版本。"
            "准备完成后仍需先创建完整备份并安全停止旧版。是否继续？",
            parent=self.root,
        ):
            return
        self.update_in_progress = True
        self.update_button.configure(state="disabled")
        self.recovery_button.configure(state="disabled")
        self.migration_button.configure(state="disabled")
        self.relocation_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.detail_var.set("正在本机校验 SHA256 并准备新版本目录，请勿关闭控制器……")
        threading.Thread(
            target=self._prepare_update_worker,
            args=(Path(selected), Path(install_parent)),
            name="prepare-update",
            daemon=True,
        ).start()

    def _prepare_update_worker(self, package: Path, install_parent: Path) -> None:
        try:
            result = prepare_release_package(package, install_parent)
            self.events.put(("update_prepared", result))
        except Exception as exc:  # noqa: BLE001 - shown to administrator
            logging.exception("Release package preparation failed")
            self.events.put(("update_failure", str(exc)))

    def import_nas_backup(self) -> None:
        if self.migration_in_progress or self.update_in_progress or self.recovery_in_progress:
            messagebox.showinfo(
                "维护操作进行中", "请等待当前维护操作完成。", parent=self.root
            )
            return
        if _health_payload() or _runner_is_starting():
            messagebox.showwarning(
                "请先安全停止服务器",
                "从外部导入灾备恢复集前，请先安全停止服务器。普通本地恢复可在网页恢复中心安排。",
                parent=self.root,
            )
            return
        selected = filedialog.askopenfilename(
            title="选择 NAS 上的完整备份 ZIP（同目录必须有同名 JSON）",
            initialdir=(
                self.nas_backup_var.get().strip()
                if self.nas_backup_var.get().strip()
                and Path(self.nas_backup_var.get().strip()).exists()
                else None
            ),
            filetypes=[("交接班完整备份", "jx-handover-backup-*.zip"), ("ZIP", "*.zip")],
            parent=self.root,
        )
        if not selected:
            return
        try:
            data_root = validate_local_data_root(
                configured_data_root(self.settings), create=True
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("正式数据目录不可用", str(exc), parent=self.root)
            return
        if not messagebox.askyesno(
            "导入并安排灾备恢复",
            f"外部完整备份：\n{selected}\n\n"
            f"服务器本地正式数据目录：\n{data_root}\n\n"
            "系统将先在本机验证 ZIP、全部文件 SHA256 和 SQLite，复制到本地恢复中心，"
            "然后登记为下次启动恢复。不会覆盖同名不同内容的备份。是否继续？",
            parent=self.root,
        ):
            return
        self.recovery_in_progress = True
        self.recovery_button.configure(state="disabled")
        self.update_button.configure(state="disabled")
        self.migration_button.configure(state="disabled")
        self.relocation_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.detail_var.set("正在从 NAS 读取并全量校验完整备份，请勿关闭控制器……")
        threading.Thread(
            target=self._import_nas_backup_worker,
            args=(Path(selected), data_root),
            name="import-nas-backup",
            daemon=True,
        ).start()

    def _import_nas_backup_worker(self, package: Path, data_root: Path) -> None:
        try:
            imported = import_backup_bundle(package, data_root)
            request = schedule_imported_restore(
                data_root,
                imported,
                requested_by=os.environ.get("USERNAME", "server-controller"),
            )
            self.events.put(("recovery_imported", {"imported": imported, "request": request}))
        except Exception as exc:  # noqa: BLE001 - shown to administrator
            logging.exception("External backup import failed")
            self.events.put(("recovery_failure", str(exc)))

    def _packaged_script(self, filename: str) -> Path:
        candidates = [
            Path(sys.executable).resolve().parent / filename,
            Path(__file__).resolve().parent / "packaging" / filename,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"未找到部署脚本：{filename}")

    def _run_elevated_script(self, filename: str) -> None:
        try:
            script = self._packaged_script(filename)
            arguments = f'-NoProfile -ExecutionPolicy Bypass -File "{script}"'
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "powershell.exe", arguments, str(script.parent), 1
            )
            if int(result) <= 32:
                raise OSError(f"Windows ShellExecute 返回 {result}")
        except Exception as exc:  # noqa: BLE001
            self._failure(f"无法启动管理员脚本：{exc}")
            messagebox.showerror("操作未启动", str(exc), parent=self.root)

    def install_autostart(self) -> None:
        if self.migration_in_progress:
            messagebox.showinfo("正在迁移", "请等待数据迁移完成后再安装自动运行。", parent=self.root)
            return
        if not messagebox.askyesno(
            "安装开机自动运行",
            "将以 Windows SYSTEM 账户创建“开机时”计划任务，并放行域/专用网络 TCP 8765。\n\n"
            "注意：SYSTEM 默认没有 NAS 登录凭据，NAS 自动备份可能失败；本地备份不受影响。继续吗？",
            parent=self.root,
        ):
            return
        self._run_elevated_script("安装开机自动启动_系统账户.ps1")

    def uninstall_autostart(self) -> None:
        if messagebox.askyesno(
            "卸载开机自动运行",
            "将删除服务器计划任务，但不会删除数据库、设置、日志或备份。继续吗？",
            parent=self.root,
        ):
            self._run_elevated_script("卸载开机自动启动.ps1")

    def _open_path(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                import os
                os.startfile(str(path))
            else:
                webbrowser.open(path.as_uri())
        except OSError as exc:
            self._failure(f"无法打开目录：{exc}")

    def copy_error(self) -> None:
        self.last_error = self.last_error or _read_runner_error()
        value = self.last_error or "当前没有错误信息。"
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update_idletasks()
        self.detail_var.set("错误信息已复制到剪贴板。" if self.last_error else value)

    def _failure(self, detail: str) -> None:
        self.last_error = detail
        self._set_state("启动失败", detail.splitlines()[0])
        logging.error(detail)

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "started":
                    self._show_running(payload if isinstance(payload, dict) else {})
                    if self.restore_starting:
                        self.restore_starting = False
                        result_path = self._restore_directory() / "last-result.json"
                        try:
                            result = json.loads(result_path.read_text(encoding="utf-8"))
                        except (OSError, ValueError, TypeError):
                            result = {}
                        messagebox.showinfo(
                            "数据恢复完成",
                            "完整备份已恢复，服务器也已正常启动。\n\n"
                            f"恢复来源：{result.get('backup_id') or '—'}\n"
                            f"恢复前留底：{result.get('pre_restore_backup_id') or '—'}\n\n"
                            "请登录网页抽查班次、导入原件和历史 Word。",
                            parent=self.root,
                        )
                    if self.auto_open_var.get():
                        self.open_system()
                elif event == "stopped":
                    self._set_state("未启动", "服务器已安全停止，数据库和历史文件保持不变。")
                    if self.restart_after_stop:
                        self.restart_after_stop = False
                        self.root.after(500, self.start)
                elif event == "failure":
                    self.restart_after_stop = False
                    self.restore_starting = False
                    self._failure(str(payload))
                elif event == "migration_done":
                    self.migration_in_progress = False
                    self.migration_button.configure(state="normal")
                    self.relocation_button.configure(state="normal")
                    self.update_button.configure(state="normal")
                    self.recovery_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self._set_state(
                        "未启动",
                        "V0.3 数据迁移完成。启动服务器后会自动升级数据库结构。",
                    )
                    result = payload if isinstance(payload, dict) else {}
                    copied = result.get("copied_files") or {}
                    messagebox.showinfo(
                        "迁移完成",
                        "V0.3 数据已迁移并通过完整性检查。\n\n"
                        f"导入原件：{copied.get('imports', 0)} 个\n"
                        f"历史 Word：{copied.get('generated', 0)} 个\n"
                        f"快照文件：{copied.get('snapshots', 0)} 个\n"
                        f"修正历史文档路径：{result.get('rewritten_document_paths', 0)} 条\n\n"
                        "现在可以启动服务器。",
                        parent=self.root,
                    )
                elif event == "migration_failure":
                    self.migration_in_progress = False
                    self.migration_button.configure(state="normal")
                    self.relocation_button.configure(state="normal")
                    self.update_button.configure(state="normal")
                    self.recovery_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self._failure(f"V0.3 数据迁移失败：{payload}")
                    messagebox.showerror(
                        "迁移失败",
                        f"{payload}\n\n当前服务器数据库如原先存在，迁移前备份仍保留。",
                        parent=self.root,
                    )
                elif event == "relocation_done":
                    self.migration_in_progress = False
                    self.migration_button.configure(state="normal")
                    self.relocation_button.configure(state="normal")
                    self.update_button.configure(state="normal")
                    self.recovery_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    result = payload if isinstance(payload, dict) else {}
                    settings = result.get("settings") or {}
                    self.settings = settings
                    self.data_root_var.set(str(settings.get("data_root") or ""))
                    self._set_state(
                        "未启动",
                        "正式数据目录已复制、校验并切换；旧目录仍完整保留。",
                    )
                    messagebox.showinfo(
                        "正式数据目录迁移完成",
                        f"新目录：\n{result.get('target_root', '')}\n\n"
                        f"已复制：{result.get('copied_files', 0)} 个文件，"
                        f"{result.get('copied_bytes', 0)} 字节\n"
                        f"迁移清单：{result.get('manifest_path', '')}\n\n"
                        "请启动服务器完成登录、导入、Word 和备份检查。确认新目录稳定后，再按单位数据管理要求处理旧目录。",
                        parent=self.root,
                    )
                elif event == "relocation_failure":
                    self.migration_in_progress = False
                    self.migration_button.configure(state="normal")
                    self.relocation_button.configure(state="normal")
                    self.update_button.configure(state="normal")
                    self.recovery_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self._failure(f"正式数据目录迁移失败：{payload}")
                    messagebox.showerror(
                        "正式数据目录迁移失败",
                        f"{payload}\n\n控制器仍使用旧正式数据目录，旧数据库未删除。",
                        parent=self.root,
                    )
                elif event == "update_prepared":
                    self.update_in_progress = False
                    self.update_button.configure(state="normal")
                    self.recovery_button.configure(state="normal")
                    self.migration_button.configure(state="normal")
                    self.relocation_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    result = payload if isinstance(payload, dict) else {}
                    self.detail_var.set("新版本已完成校验并安全解压；当前运行版本未改变。")
                    messagebox.showinfo(
                        "新版本准备完成",
                        f"版本：V{result.get('version') or '未知'}\n"
                        f"目录：\n{result.get('install_path') or ''}\n\n"
                        "正式切换顺序：\n"
                        "1. 在网页“系统管理”创建完整备份；\n"
                        "2. 用当前控制器安全停止服务器并关闭控制器；\n"
                        "3. 打开新目录中的“服务器控制器.exe”并启动；\n"
                        "4. 验证失败时停止新版，再从旧目录启动即可回滚。",
                        parent=self.root,
                    )
                    install_path = str(result.get("install_path") or "").strip()
                    if install_path:
                        self._open_path(Path(install_path))
                elif event == "update_failure":
                    self.update_in_progress = False
                    self.update_button.configure(state="normal")
                    self.recovery_button.configure(state="normal")
                    self.migration_button.configure(state="normal")
                    self.relocation_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self._failure(f"升级包准备失败：{payload}")
                    messagebox.showerror(
                        "升级包未准备",
                        f"{payload}\n\n当前版本、数据库和旧版本目录均未改变。",
                        parent=self.root,
                    )
                elif event == "recovery_imported":
                    self.recovery_in_progress = False
                    self.recovery_button.configure(state="normal")
                    self.update_button.configure(state="normal")
                    self.migration_button.configure(state="normal")
                    self.relocation_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    result = payload if isinstance(payload, dict) else {}
                    imported = result.get("imported") or {}
                    self._set_state(
                        "未启动",
                        "外部完整备份已复制到服务器本地并通过全量校验，等待启动恢复。",
                    )
                    messagebox.showinfo(
                        "灾备恢复已准备",
                        f"备份编号：{imported.get('backup_id') or '—'}\n"
                        f"本地副本：\n{imported.get('local_bundle_path') or ''}\n\n"
                        "现在点击“启动服务器”。控制器会再次确认，服务器将在网页开放前完成恢复。",
                        parent=self.root,
                    )
                elif event == "recovery_failure":
                    self.recovery_in_progress = False
                    self.recovery_button.configure(state="normal")
                    self.update_button.configure(state="normal")
                    self.migration_button.configure(state="normal")
                    self.relocation_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self._failure(f"外部完整备份导入失败：{payload}")
                    messagebox.showerror(
                        "灾备恢复未安排",
                        f"{payload}\n\n当前正式数据库没有被替换；请检查 ZIP 与同名 JSON 是否成对且未被修改。",
                        parent=self.root,
                    )
        except queue.Empty:
            pass
        self.root.after(200, self._drain_events)

    def _close(self) -> None:
        if self.migration_in_progress or self.update_in_progress or self.recovery_in_progress:
            messagebox.showwarning(
                "维护操作尚未完成",
                "请等待数据迁移或升级包准备完成后再关闭控制器。",
                parent=self.root,
            )
            return
        # The shared service deliberately survives controller closure.
        _release_controller_mutex(self.mutex_handle)
        self.mutex_handle = None
        self.root.destroy()


def main() -> None:
    handle = _acquire_controller_mutex()
    if handle is None:
        duplicate = tk.Tk()
        duplicate.withdraw()
        messagebox.showinfo("控制器已打开", "服务器控制器已经在当前桌面会话中运行。")
        duplicate.destroy()
        return
    root = tk.Tk()
    controller: ServerController | None = None
    try:
        controller = ServerController(root, handle)
        root.mainloop()
    finally:
        _release_controller_mutex(controller.mutex_handle if controller else handle)


if __name__ == "__main__":
    main()
