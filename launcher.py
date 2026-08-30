"""Windowed Windows controller for 江西片区智能交接班 V0.3.0."""
from __future__ import annotations

import ctypes
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import queue
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_VERSION = "0.3.0"
HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"
MUTEX_NAME = "Local\\JXHandover-v0.3-controller"
ERROR_ALREADY_EXISTS = 183


def _resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()


RESOURCE_ROOT = _resource_root()
BACKEND_ROOT = RESOURCE_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import config  # noqa: E402


STATE_COLORS = {
    "未启动": "#64748b",
    "正在启动": "#d97706",
    "运行中": "#16803c",
    "停止中": "#d97706",
    "启动失败": "#c62828",
}


def acquire_single_instance() -> int | None:
    if os.name != "nt":
        return 1
    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle or kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return int(handle)


def release_single_instance(handle: int | None) -> None:
    if os.name == "nt" and handle:
        ctypes.windll.kernel32.CloseHandle(handle)


def health_payload(timeout: float = 0.8) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{URL}/api/health", timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            if (
                payload.get("service") == "jx-handover"
                and int(payload.get("port", 0)) == PORT
                and payload.get("status") == "ok"
            ):
                return payload
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return None


def port_is_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        return connection.connect_ex((HOST, PORT)) == 0


def _legacy_root(path: Path) -> Path | None:
    candidates = (path, path / "runtime")
    for candidate in candidates:
        if (
            (candidate / "data" / "handover.db").exists()
            or (candidate / "handover.db").exists()
            or (candidate / "imports").is_dir()
            or (candidate / "generated").is_dir()
        ):
            return candidate.resolve()
    return None


def _copy_tree_without_overwriting(source: Path, target: Path) -> None:
    if not source.is_dir():
        return
    for file_path in source.rglob("*"):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(file_path, destination)


def migrate_legacy_data(source: Path, *, replace_database: bool = False) -> dict:
    """Back up then copy a V0.1/V0.2 runtime into LocalAppData."""
    source_root = _legacy_root(source)
    if source_root is None:
        raise ValueError("所选目录中没有找到旧版 data、imports 或 generated 数据。")
    if source_root == config.USER_DATA_ROOT.resolve():
        raise ValueError("所选目录就是当前 V0.3.0 数据目录，无需迁移。")

    legacy_db = source_root / "data" / "handover.db"
    if not legacy_db.exists():
        legacy_db = source_root / "handover.db"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = config.SNAPSHOT_DIR / "legacy_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    legacy_backup = None
    if legacy_db.exists():
        legacy_backup = backup_dir / f"handover_legacy_original_{timestamp}.db"
        shutil.copy2(legacy_db, legacy_backup)

    current_backup = None
    if config.DATABASE_PATH.exists() and replace_database:
        current_backup_dir = config.SNAPSHOT_DIR / "database_backups"
        current_backup_dir.mkdir(parents=True, exist_ok=True)
        current_backup = current_backup_dir / f"handover_before_manual_import_{timestamp}.db"
        shutil.copy2(config.DATABASE_PATH, current_backup)

    if legacy_db.exists() and (replace_database or not config.DATABASE_PATH.exists()):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_db, config.DATABASE_PATH)
    _copy_tree_without_overwriting(source_root / "imports", config.IMPORT_DIR)
    _copy_tree_without_overwriting(source_root / "generated", config.GENERATED_DIR)
    _copy_tree_without_overwriting(source_root / "snapshots", config.SNAPSHOT_DIR)

    result = {
        "source": str(source_root),
        "legacy_database_backup": str(legacy_backup) if legacy_backup else None,
        "replaced_database_backup": str(current_backup) if current_backup else None,
        "migrated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    receipt = config.SNAPSHOT_DIR / f"legacy_migration_{timestamp}.json"
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


class Controller:
    def __init__(self, root: tk.Tk, mutex_handle: int | None) -> None:
        self.root = root
        self.mutex_handle = mutex_handle
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.server = None
        self.server_thread: threading.Thread | None = None
        self.owns_server = False
        self.restart_after_stop = False
        self.exit_after_stop = False
        self.last_error = ""
        self.state = "未启动"
        self.settings_path = config.USER_DATA_ROOT / "settings.json"
        self.log_path = config.LOG_DIR / f"controller-{datetime.now():%Y-%m-%d}.log"
        self._configure_logging()
        self.auto_open = tk.BooleanVar(value=self._load_auto_open())
        self.status_var = tk.StringVar(value=self.state)
        self.detail_var = tk.StringVar(value="控制器准备就绪，将自动启动系统。")
        self.log_var = tk.StringVar(value=f"日志：{self.log_path}")
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(120, self._first_run)
        self.root.after(200, self._drain_events)

    def _configure_logging(self) -> None:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=self.log_path,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            encoding="utf-8",
            force=True,
        )
        logging.info("Controller V%s initialized at %s", APP_VERSION, URL)

    def _load_auto_open(self) -> bool:
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return bool(settings.get("auto_open_browser", True))
        except (OSError, ValueError, TypeError):
            return True

    def _save_settings(self) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(
                json.dumps(
                    {"auto_open_browser": bool(self.auto_open.get())},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            logging.exception("Unable to save settings")

    def _build_ui(self) -> None:
        self.root.title(f"交接班系统 V{APP_VERSION}")
        self.root.geometry("760x510")
        self.root.minsize(700, 470)
        self.root.configure(background="#f4f7fb")

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 8))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TCheckbutton", background="#f4f7fb", font=("Microsoft YaHei UI", 10))

        outer = tk.Frame(self.root, bg="#f4f7fb", padx=26, pady=22)
        outer.pack(fill="both", expand=True)
        tk.Label(
            outer,
            text="江西片区智能交接班系统",
            font=("Microsoft YaHei UI", 20, "bold"),
            fg="#15375b",
            bg="#f4f7fb",
        ).pack(anchor="w")
        tk.Label(
            outer,
            text=f"本机安全运行 · {URL} · V{APP_VERSION}",
            font=("Microsoft YaHei UI", 10),
            fg="#60758c",
            bg="#f4f7fb",
        ).pack(anchor="w", pady=(2, 16))

        status_card = tk.Frame(outer, bg="white", bd=0, highlightthickness=1, highlightbackground="#dbe5ef")
        status_card.pack(fill="x")
        self.status_label = tk.Label(
            status_card,
            textvariable=self.status_var,
            font=("Microsoft YaHei UI", 17, "bold"),
            fg="white",
            bg=STATE_COLORS[self.state],
            padx=18,
            pady=9,
        )
        self.status_label.pack(side="left", padx=16, pady=16)
        tk.Label(
            status_card,
            textvariable=self.detail_var,
            font=("Microsoft YaHei UI", 10),
            fg="#334b62",
            bg="white",
            justify="left",
            wraplength=500,
        ).pack(side="left", fill="x", expand=True, padx=(0, 16), pady=16)

        primary = tk.Frame(outer, bg="#f4f7fb")
        primary.pack(fill="x", pady=(18, 10))
        self.start_button = ttk.Button(primary, text="启动系统", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left", padx=(0, 8))
        self.open_button = ttk.Button(primary, text="打开系统", command=self.open_web)
        self.open_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(primary, text="停止系统", command=self.stop)
        self.stop_button.pack(side="left", padx=8)
        self.restart_button = ttk.Button(primary, text="重启系统", command=self.restart)
        self.restart_button.pack(side="left", padx=8)

        secondary = tk.Frame(outer, bg="#f4f7fb")
        secondary.pack(fill="x", pady=(4, 10))
        ttk.Button(secondary, text="打开数据目录", command=lambda: self._open_path(config.USER_DATA_ROOT)).pack(side="left", padx=(0, 8))
        ttk.Button(secondary, text="打开日志", command=lambda: self._open_path(config.LOG_DIR)).pack(side="left", padx=8)
        ttk.Button(secondary, text="复制错误信息", command=self.copy_error).pack(side="left", padx=8)
        ttk.Button(secondary, text="选择旧版数据目录", command=self.select_legacy_directory).pack(side="left", padx=8)

        ttk.Checkbutton(
            outer,
            text="启动成功后自动打开浏览器（系统会记住此选择）",
            variable=self.auto_open,
            command=self._save_settings,
        ).pack(anchor="w", pady=(6, 12))

        info = tk.Frame(outer, bg="#e9f2fb", padx=14, pady=11)
        info.pack(fill="both", expand=True)
        tk.Label(
            info,
            text="使用说明",
            font=("Microsoft YaHei UI", 10, "bold"),
            fg="#244d73",
            bg="#e9f2fb",
        ).pack(anchor="w")
        tk.Label(
            info,
            text="最小化此窗口不会停止服务。关闭窗口时会询问是否停止本控制器启动的服务。\n"
                 "若 8765 被其他程序占用，控制器只会提示，不会结束对方进程。",
            font=("Microsoft YaHei UI", 9),
            fg="#45657f",
            bg="#e9f2fb",
            justify="left",
        ).pack(anchor="w", pady=(4, 8))
        tk.Label(
            info,
            textvariable=self.log_var,
            font=("Microsoft YaHei UI", 8),
            fg="#60758c",
            bg="#e9f2fb",
            wraplength=650,
            justify="left",
        ).pack(anchor="w")
        self._refresh_buttons()

    def _set_state(self, state: str, detail: str) -> None:
        self.state = state
        self.status_var.set(state)
        self.detail_var.set(detail)
        self.status_label.configure(bg=STATE_COLORS[state])
        self._refresh_buttons()
        logging.info("State=%s detail=%s", state, detail)

    def _refresh_buttons(self) -> None:
        busy = self.state in {"正在启动", "停止中"}
        running = self.state == "运行中"
        self.start_button.configure(state="disabled" if busy or running else "normal")
        self.open_button.configure(state="normal" if running else "disabled")
        self.stop_button.configure(state="normal" if running and self.owns_server else "disabled")
        self.restart_button.configure(state="normal" if running and self.owns_server else "disabled")

    def _find_legacy_candidate(self) -> Path | None:
        executable_dir = Path(sys.executable).resolve().parent
        candidates = (
            executable_dir / "runtime",
            executable_dir.parent / "runtime",
            Path.cwd() / "runtime",
            Path.cwd(),
        )
        target = config.USER_DATA_ROOT.resolve()
        for candidate in candidates:
            root = _legacy_root(candidate)
            if root is not None and root != target:
                return root
        return None

    def _first_run(self) -> None:
        if config.RUNNING_FROZEN and not config.DATABASE_PATH.exists():
            candidate = self._find_legacy_candidate()
            if candidate is not None:
                try:
                    result = migrate_legacy_data(candidate)
                    self.detail_var.set(f"已从旧版迁移数据：{result['source']}")
                except Exception:  # noqa: BLE001
                    logging.exception("Automatic legacy migration failed")
            else:
                choose = messagebox.askyesno(
                    "旧版数据迁移",
                    "未自动找到旧版数据。\n\n如果这是升级安装，请选择旧版项目或 runtime 目录；"
                    "如果是首次使用，请点“否”。",
                    parent=self.root,
                )
                if choose:
                    self.select_legacy_directory(first_run=True)
        self.start()

    def select_legacy_directory(self, first_run: bool = False) -> None:
        if self.owns_server:
            messagebox.showinfo("请先停止", "请先停止本控制器启动的系统，再迁移旧版数据。", parent=self.root)
            return
        selected = filedialog.askdirectory(title="选择旧版项目或 runtime 数据目录", parent=self.root)
        if not selected:
            return
        replace = config.DATABASE_PATH.exists()
        if replace and not first_run:
            proceed = messagebox.askyesno(
                "确认导入旧版数据",
                "当前 V0.3.0 已有数据库。继续前会先备份当前数据库，再用所选旧版数据库替换。\n\n是否继续？",
                parent=self.root,
            )
            if not proceed:
                return
        try:
            try:
                from app.db import engine

                engine.dispose()
            except Exception:  # noqa: BLE001
                pass
            result = migrate_legacy_data(Path(selected), replace_database=replace)
            self.detail_var.set(f"旧版数据已迁移：{result['source']}")
            messagebox.showinfo(
                "迁移完成",
                "旧数据库、导入原件和历史 Word 已迁移；原数据库备份已保存在 snapshots。",
                parent=self.root,
            )
        except Exception as exc:  # noqa: BLE001
            self._record_error("旧版数据迁移失败", exc)
            messagebox.showerror("迁移失败", str(exc), parent=self.root)

    def start(self) -> None:
        if self.state in {"正在启动", "停止中"} or self.owns_server:
            return
        existing = health_payload()
        if existing:
            self.owns_server = False
            self._set_state("运行中", f"系统已运行（V{existing.get('version', '未知')}），已连接现有实例。")
            self.open_web()
            return
        if port_is_in_use():
            error = (
                f"端口 {HOST}:{PORT} 已被其他程序占用。\n"
                "请关闭占用该端口的程序后再点“启动系统”；控制器不会强制结束其他进程。"
            )
            self.last_error = error
            self._set_state("启动失败", error)
            return
        self._set_state("正在启动", "正在检查数据库、迁移版本并启动服务……")
        threading.Thread(target=self._start_worker, name="controller-start", daemon=True).start()

    def _start_worker(self) -> None:
        try:
            from app.bootstrap import initialize_application_data
            import uvicorn

            migration = initialize_application_data()
            logging.info("Database initialization: %s", migration)
            if health_payload():
                self.events.put(("already_running", None))
                return
            if port_is_in_use():
                raise RuntimeError(
                    f"端口 {HOST}:{PORT} 在启动过程中被其他程序占用；未结束任何进程。"
                )
            uvicorn_config = uvicorn.Config(
                "app.main:app",
                host=HOST,
                port=PORT,
                log_config=None,
                access_log=True,
            )
            self.server = uvicorn.Server(uvicorn_config)
            self.server_thread = threading.Thread(
                target=self._run_server,
                name="jx-handover-web",
                daemon=True,
            )
            self.server_thread.start()
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                payload = health_payload(timeout=0.5)
                if payload:
                    self.events.put(("started", payload))
                    return
                if self.server_thread and not self.server_thread.is_alive():
                    raise RuntimeError("服务进程提前退出，请打开日志查看详细原因。")
                time.sleep(0.25)
            if self.server is not None:
                self.server.should_exit = True
            raise RuntimeError("启动超时：25 秒内未通过健康检查。")
        except Exception as exc:  # noqa: BLE001
            detail = self._format_exception("系统启动失败", exc)
            self.events.put(("failure", detail))

    def _run_server(self) -> None:
        try:
            assert self.server is not None
            self.server.run()
        except Exception as exc:  # noqa: BLE001
            self.events.put(("failure", self._format_exception("服务异常退出", exc)))
        finally:
            self.events.put(("server_exited", None))

    def stop(self) -> None:
        if not self.owns_server or self.server is None:
            if health_payload():
                messagebox.showinfo(
                    "不能停止其他实例",
                    "当前服务不是由这个控制器启动的。为保护数据，控制器不会结束该实例。",
                    parent=self.root,
                )
            return
        self._set_state("停止中", "正在安全停止本控制器启动的服务……")
        self.server.should_exit = True
        threading.Thread(target=self._wait_for_stop, name="controller-stop", daemon=True).start()

    def _wait_for_stop(self) -> None:
        thread = self.server_thread
        if thread is not None:
            thread.join(timeout=20)
        if thread is not None and thread.is_alive():
            self.events.put(("failure", "停止超时：服务仍在退出中，未强制结束进程。"))
        else:
            self.events.put(("stopped", None))

    def restart(self) -> None:
        if not self.owns_server:
            return
        self.restart_after_stop = True
        self.stop()

    def open_web(self) -> None:
        if health_payload():
            webbrowser.open(URL)
        else:
            messagebox.showinfo("系统尚未运行", "请先点“启动系统”。", parent=self.root)

    def _open_path(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError as exc:
            self._record_error("无法打开目录", exc)

    def copy_error(self) -> None:
        text = self.last_error or "当前没有错误信息。"
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self.detail_var.set("错误信息已复制到剪贴板。" if self.last_error else text)

    def _format_exception(self, prefix: str, exc: Exception) -> str:
        details = f"{prefix}：{exc}\n\n{traceback.format_exc()}"
        logging.error(details)
        return details

    def _record_error(self, prefix: str, exc: Exception) -> None:
        self.last_error = self._format_exception(prefix, exc)
        self._set_state("启动失败", f"{prefix}：{exc}")

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "started":
                    self.owns_server = True
                    version = (payload or {}).get("version", APP_VERSION)  # type: ignore[union-attr]
                    self._set_state("运行中", f"系统运行正常（V{version}），访问地址：{URL}")
                    if self.auto_open.get():
                        webbrowser.open(URL)
                elif event == "already_running":
                    self.owns_server = False
                    self._set_state("运行中", "系统已运行，已连接现有实例。")
                    if self.auto_open.get():
                        webbrowser.open(URL)
                elif event == "failure":
                    self.last_error = str(payload)
                    summary = self.last_error.splitlines()[0]
                    self.owns_server = False
                    self._set_state("启动失败", summary)
                elif event == "server_exited":
                    if self.owns_server and self.state not in {"停止中", "未启动"}:
                        self.owns_server = False
                        self._set_state("启动失败", "服务意外退出，请点“打开日志”查看原因。")
                elif event == "stopped":
                    self.owns_server = False
                    self.server = None
                    self.server_thread = None
                    self._set_state("未启动", "系统已安全停止，数据仍保存在本机数据目录。")
                    if self.exit_after_stop:
                        self._destroy()
                        return
                    if self.restart_after_stop:
                        self.restart_after_stop = False
                        self.root.after(250, self.start)
        except queue.Empty:
            pass
        self.root.after(200, self._drain_events)

    def on_close(self) -> None:
        self._save_settings()
        if self.owns_server:
            should_stop = messagebox.askyesno(
                "停止系统并退出",
                "关闭控制器会停止本控制器启动的交接班系统。\n\n"
                "如果只想暂时隐藏，请点击窗口右上角的最小化按钮。\n\n"
                "确认停止系统并退出吗？",
                parent=self.root,
            )
            if not should_stop:
                return
            self.exit_after_stop = True
            self.stop()
            return
        self._destroy()

    def _destroy(self) -> None:
        release_single_instance(self.mutex_handle)
        self.mutex_handle = None
        self.root.destroy()


def main() -> None:
    mutex_handle = acquire_single_instance()
    if mutex_handle is None:
        duplicate = tk.Tk()
        duplicate.withdraw()
        messagebox.showinfo("系统已打开", "交接班系统控制器已经在运行，将为你打开网页。")
        webbrowser.open(URL)
        duplicate.destroy()
        return
    root = tk.Tk()
    controller: Controller | None = None
    try:
        controller = Controller(root, mutex_handle)
        root.mainloop()
    finally:
        release_single_instance(controller.mutex_handle if controller else mutex_handle)


if __name__ == "__main__":
    main()
