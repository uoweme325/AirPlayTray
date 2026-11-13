"""Windows system tray controller for streaming system audio to AirPlay speakers."""

import argparse
import asyncio
import contextlib
from dataclasses import dataclass
import ctypes
import io
import locale
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
from typing import Callable, List, Optional, Tuple

import pystray
from PIL import Image, ImageDraw

from app_paths import asset_path, data_dir, ensure_writable_data_dir, log_path, pid_path, resource_dir
import airplay_system_audio as tool
from settings import DEFAULT_CONFIG, configure_device, save_device_selection, save_volume_selection

SCRIPT_DIR = str(resource_dir())
LOG_PATH = str(log_path())
PID_PATH = str(pid_path())
MUTEX_NAME = "Local\\AirPlayPCTrayIcon"
MAX_LOG_BYTES = 2 * 1024 * 1024
KEEP_LOG_BYTES = 256 * 1024

_UI_STRINGS = {
    "zh": {
        "app_name": "AirPlay 推流",
        "start": "开始推流",
        "stop": "停止推流",
        "select_speaker": "选择音箱",
        "rescan": "重新扫描音箱",
        "scanning": "正在扫描局域网音箱…",
        "no_speakers": "未发现可用 AirPlay 音箱",
        "switching_item": "正在切换音箱…",
        "open_log": "查看日志",
        "quit": "退出",
        "stopped": "已停止",
        "stopping": "停止中…",
        "unconfigured": "未选择音箱（请在右键菜单中选择）",
        "connecting": "连接中：{target}",
        "streaming": "推流中：{target}",
        "idle": "待机监听：{target}",
        "retrying": "重连中：{target}",
        "error": "异常：{detail}",
        "switching": "正在切换音箱：{target}",
        "data_dir_error": (
            "无法初始化数据或日志目录（{path}）：{error}\n\n"
            "请将程序解压到可写目录，或清理同名占用文件后重试。"
        ),
        "duplicate_instance": (
            "AirPlay 推流已经在后台运行了。\n\n"
            "请查看任务栏右下角的喇叭图标（可能在 ^ 折叠区里），"
            "右键它可以开关推流或选择音箱。"
        ),
        "auto_selected": "已自动连接到 HomePod：{name}",
        "pick_speaker_tip": "请在托盘右键菜单“选择音箱”中挑选目标设备。",
        "config_error_tip": "配置文件格式有误，请从右键菜单“选择音箱”重新指定，或检查 config.json。",
        "startup_init": "正在初始化…",
        "volume": "音量",
        "volume_current": "当前：{value}%",
        "volume_saved": "已保存：{value}%（下次连接应用）",
        "volume_initial": "使用音箱初始音量",
        "volume_up": "增加 5%",
        "volume_down": "减少 5%",
        "volume_busy": "正在调节音量…",
        "volume_failed": "音量操作未完全成功：{error}",
        "volume_pipe": "音量（pipe 通道暂不支持）",
    },
    "en": {
        "app_name": "AirPlayTray",
        "start": "Start Streaming",
        "stop": "Stop Streaming",
        "select_speaker": "Select Speaker",
        "rescan": "Rescan Speakers",
        "scanning": "Scanning for speakers...",
        "no_speakers": "No AirPlay speakers found",
        "switching_item": "Switching speaker...",
        "open_log": "Open Log",
        "quit": "Quit",
        "stopped": "Stopped",
        "stopping": "Stopping...",
        "unconfigured": "No speaker selected (choose in menu)",
        "connecting": "Connecting: {target}",
        "streaming": "Streaming: {target}",
        "idle": "Idle: {target}",
        "retrying": "Retrying: {target}",
        "error": "Error: {detail}",
        "switching": "Switching: {target}",
        "data_dir_error": (
            "Failed to initialize writable data/log directory ({path}): {error}\n\n"
            "Extract AirPlayTray to a writable folder or remove any conflicting file named 'data' and retry."
        ),
        "duplicate_instance": (
            "AirPlayTray is already running in the background.\n\n"
            "Check the speaker icon in the system tray (or ^ overflow area) "
            "and right-click it to control streaming or choose a speaker."
        ),
        "auto_selected": "Auto-selected HomePod: {name}",
        "pick_speaker_tip": "Right-click the tray icon and choose Select Speaker.",
        "config_error_tip": "Invalid config.json; pick a speaker in Select Speaker or inspect the log.",
        "startup_init": "Initializing...",
        "volume": "Volume",
        "volume_current": "Current: {value}%",
        "volume_saved": "Saved: {value}% (next connection)",
        "volume_initial": "Use receiver initial volume",
        "volume_up": "Increase 5%",
        "volume_down": "Decrease 5%",
        "volume_busy": "Adjusting volume...",
        "volume_failed": "Volume operation did not fully succeed: {error}",
        "volume_pipe": "Volume (unavailable with pipe)",
    },
}

_ICON_PALETTES = {
    "blue": ((0, 120, 215, 255), 2),
    "teal": ((0, 160, 170, 255), 1),
    "teal_dim": ((0, 95, 110, 255), 1),
    "grey": ((130, 134, 140, 255), 0),
}
_ICON_CACHE = {}


def detect_ui_language() -> str:
    """Detect Simplified Chinese vs English UI language for tray labels."""
    override = os.environ.get("AIRPLAYTRAY_LANG", "").strip().lower()
    if override.startswith("zh"):
        return "zh"
    if override.startswith("en"):
        return "en"
    with contextlib.suppress(Exception):
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if (lang_id & 0x3FF) == 0x04:
            return "zh"
        return "en"
    loc = (locale.getdefaultlocale()[0] or "").lower()
    return "zh" if loc.startswith("zh") else "en"


def ui_text(lang: str, key: str, **kwargs) -> str:
    """Look up localized tray string with English fallback."""
    table = _UI_STRINGS.get(lang, _UI_STRINGS["en"])
    template = table.get(key) or _UI_STRINGS["en"].get(key, key)
    return template.format(**kwargs) if kwargs else template


def trim_log_if_needed(path: str = LOG_PATH) -> None:
    """限制日志文件体积，避免长期运行膨胀导致记事本卡死。"""
    with contextlib.suppress(OSError):
        if not os.path.exists(path) or os.path.getsize(path) <= MAX_LOG_BYTES:
            return
        with open(path, "rb") as f:
            f.seek(-KEEP_LOG_BYTES, os.SEEK_END)
            tail = f.read()
        nl = tail.find(b"\n")
        if nl != -1:
            tail = tail[nl + 1:]
        with open(path, "wb") as f:
            f.write(b"--- [log rotated] ---\r\n" + tail)


class BoundedLogStream:
    """线程安全的日志输出流，带自动体积裁剪。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._writes = 0
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        trim_log_if_needed(self.path)
        self._handle = open(self.path, "a", encoding="utf-8", buffering=1)

    def write(self, text: str) -> int:
        if not text:
            return 0
        with self._lock:
            self._writes += 1
            if self._writes >= 2000:
                self._writes = 0
                with contextlib.suppress(OSError):
                    if os.path.getsize(self.path) > MAX_LOG_BYTES:
                        self._handle.flush()
                        self._handle.close()
                        trim_log_if_needed(self.path)
                        self._handle = open(self.path, "a", encoding="utf-8", buffering=1)
            return self._handle.write(text)

    def flush(self) -> None:
        with self._lock, contextlib.suppress(OSError):
            self._handle.flush()

    def close(self) -> None:
        with self._lock, contextlib.suppress(OSError):
            self._handle.flush()
            self._handle.close()


def ensure_windowed_stdio() -> None:
    """Attach safe stdout/stderr when launched with ``pythonw.exe`` or ``console=False``."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    fallback = None
    with contextlib.suppress(OSError):
        fallback = BoundedLogStream(str(log_path()))
    if fallback is None:
        fallback = io.StringIO()
    if sys.stdout is None:
        sys.stdout = fallback
    if sys.stderr is None:
        sys.stderr = fallback


def acquire_tray_mutex():
    """只允许一个托盘实例；已被占用时返回 None。"""
    ERROR_ALREADY_EXISTS = 183
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        return None
    return handle


def notify(message: str, title: str = "AirPlay 推流") -> None:
    with contextlib.suppress(Exception):
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)


def log(message: str, path: Optional[str] = None) -> None:
    target = path or str(log_path())
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))


def _render_icon(state_key: str) -> Image.Image:
    colour, waves = _ICON_PALETTES[state_key]
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=colour)
    draw.polygon(
        [(20, 26), (29, 26), (39, 16), (39, 48), (29, 38), (20, 38)],
        fill=(255, 255, 255, 255),
    )
    if waves >= 1:
        draw.arc((34, 20, 52, 44), start=-58, end=58, fill=(255, 255, 255, 255), width=3)
    if waves >= 2:
        draw.arc((38, 14, 60, 50), start=-55, end=55, fill=(255, 255, 255, 255), width=3)
    return img


def make_icon(active) -> Image.Image:
    """Return a cached tray icon Image for ``bool`` or ``blue|teal|teal_dim|grey``."""
    if isinstance(active, bool):
        key = "blue" if active else "grey"
    elif active in _ICON_PALETTES:
        key = active
    else:
        key = "grey"
    cached = _ICON_CACHE.get(key)
    if cached is None:
        cached = _render_icon(key)
        _ICON_CACHE[key] = cached
    return cached


def icon_key_for_status(status: str, blink_on: bool = True) -> str:
    """Map controller status + blink phase to one of the cached icon keys."""
    if status == "streaming":
        return "blue"
    if status == "idle":
        return "teal"
    if status in ("connecting", "retrying", "stopping"):
        return "teal" if blink_on else "teal_dim"
    return "grey"


@dataclass(frozen=True)
class SpeakerInfo:
    """Discovered AirPlay speaker summary displayed in tray menus."""

    id: str
    name: str
    address: str
    all_identifiers: Tuple[str, ...]
    is_homepod: bool

    @classmethod
    def from_device(cls, conf) -> "SpeakerInfo":
        ident = str(getattr(conf, "identifier", "") or "")
        raw_ids = getattr(conf, "all_identifiers", None) or ([ident] if ident else [])
        return cls(
            id=ident,
            name=str(getattr(conf, "name", "") or ident),
            address=str(getattr(conf, "address", "") or ""),
            all_identifiers=tuple(str(x) for x in raw_ids if x),
            is_homepod=bool(tool.is_homepod_device(conf)),
        )


def _short_id(identifier: str) -> str:
    cleaned = identifier.strip()
    if len(cleaned) <= 8:
        return cleaned
    return cleaned[-8:]


def format_speaker_labels(devices: List[SpeakerInfo]) -> List[Tuple[SpeakerInfo, str]]:
    """Format submenu labels, adding a short ID suffix when names collide."""
    counts = {}
    for dev in devices:
        counts[dev.name] = counts.get(dev.name, 0) + 1
    labels = []
    for dev in devices:
        label = dev.name
        if counts.get(dev.name, 0) > 1 and dev.id:
            label = "%s (%s)" % (dev.name, _short_id(dev.id))
        labels.append((dev, label))
    return labels


def matches_target(dev: SpeakerInfo, target_id: Optional[str], target_name: Optional[str]) -> bool:
    """Return True if discovered speaker matches the active configured target."""
    if target_id:
        needle = target_id.lower()
        return dev.id.lower() == needle or any(cid.lower() == needle for cid in dev.all_identifiers)
    if target_name:
        return target_name.lower() in dev.name.lower()
    return False


def pick_auto_homepod(
    devices: List[SpeakerInfo],
    config_path: str,
    args,
) -> Optional[SpeakerInfo]:
    """Auto-select only when unconfigured, no config file exists, and 1 HomePod is found."""
    if args.id or args.name:
        return None
    if getattr(args, "config_error", None) not in (None, "missing"):
        return None
    if Path(config_path).exists():
        return None
    homepods = [dev for dev in devices if dev.is_homepod]
    if len(homepods) == 1:
        return homepods[0]
    return None


class Controller:
    """Manage background AirPlay streaming, LAN scanning, and serial speaker switching."""

    def __init__(self, args, lang: str = "zh", on_ui_change: Optional[Callable[[bool], None]] = None):
        self.args = args
        self.lang = lang
        self._on_ui_change = on_ui_change
        self.thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.stop_event: Optional[asyncio.Event] = None
        self.lock = None

        self._op_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._switch_lock = threading.Lock()
        self._generation = 0
        self._active_gen = 0
        self._stop_requested_gen = 0

        self.status = "error" if self._has_fatal_config_error() else "stopped"
        self.detail = args.config_error if self._has_fatal_config_error() else ""
        self.switching = False
        self.scanning = False
        self.scan_error: Optional[str] = None
        self.devices: List[SpeakerInfo] = []
        self.volume_value = args.volume
        self.volume_applied = False
        self.volume_busy = False
        self.volume_control = None
        self._volume_future = None
        self.on_volume_error = None

    def volume_text(self):
        with self._state_lock:
            if self.volume_busy:
                return ui_text(self.lang, "volume_busy")
            if self.volume_value is None:
                return ui_text(self.lang, "volume_initial")
            key = "volume_current" if self.volume_applied else "volume_saved"
            return ui_text(self.lang, key, value="%g" % self.volume_value)

    def request_volume(self, value=None, delta=None):
        """Keep network operations and config writes off the menu thread."""
        with self._state_lock:
            if self.args.engine != "direct" or self.volume_busy or self.switching or self.status == "stopping":
                return False
            if delta is not None:
                if self.volume_value is None:
                    return False
                value = max(0, min(100, self.volume_value + delta))
            self.volume_busy = True
        self._notify_ui(menu=True)

        def worker():
            try:
                with self._op_lock:
                    with self._state_lock:
                        loop, control = self.loop, self.volume_control
                        if self.status == "stopping":
                            raise RuntimeError("stream is stopping; try again after it stops")
                        if loop is not None and control is not None:
                            self._volume_future = asyncio.run_coroutine_threadsafe(control.change(value), loop)
                            future = self._volume_future
                        else:
                            future = None
                    if future is not None:
                        future.result()
                    else:
                        save_volume_selection(self.args.config, value)
                        self.args.volume = value
                        with self._state_lock:
                            self.volume_value = value
                            self.volume_applied = False
                    log("音量选择：%g%%" % value)
            except Exception as exc:  # noqa: BLE001
                log("音量操作失败：%r" % exc)
                if self.on_volume_error is not None:
                    self.on_volume_error(ui_text(self.lang, "volume_failed", error=str(exc)))
            finally:
                with self._state_lock:
                    self._volume_future = None
                    self.volume_busy = False
                self._notify_ui(menu=True)

        threading.Thread(target=worker, daemon=True, name="volume-change").start()
        return True

    def _has_fatal_config_error(self) -> bool:
        err = getattr(self.args, "config_error", None)
        return err not in (None, "missing") and not (self.args.id or self.args.name)

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    @property
    def state(self) -> str:
        return self.status_text()

    def has_target(self) -> bool:
        return bool(self.args.id or self.args.name)

    def _notify_ui(self, menu: bool = False) -> None:
        if self._on_ui_change is not None:
            with contextlib.suppress(Exception):
                self._on_ui_change(menu)

    def status_text(self) -> str:
        with self._state_lock:
            status = self.status
            detail = self.detail
            switching = self.switching
            target = self.args.name or self.args.id or ""

        if switching:
            return ui_text(self.lang, "switching", target=detail or target)
        if status == "stopping":
            return ui_text(self.lang, "stopping")
        if status == "stopped":
            if not self.has_target() and not detail:
                return ui_text(self.lang, "unconfigured")
            return ui_text(self.lang, "stopped")
        if status == "connecting":
            return ui_text(self.lang, "connecting", target=detail or target)
        if status == "streaming":
            return ui_text(self.lang, "streaming", target=detail or target)
        if status == "idle":
            return ui_text(self.lang, "idle", target=detail or target)
        if status == "retrying":
            return ui_text(self.lang, "retrying", target=detail or target)
        if status == "error":
            return ui_text(self.lang, "error", detail=detail or "unknown")
        return ui_text(self.lang, "stopped")

    def tooltip_title(self) -> str:
        prefix = ui_text(self.lang, "app_name")
        title = "%s - %s" % (prefix, self.status_text())
        return title[:120]

    def icon_state(self, blink_on: bool = True) -> str:
        with self._state_lock:
            status = self.status
        return icon_key_for_status(status, blink_on=blink_on)

    def start(self) -> bool:
        with self._op_lock:
            if not self.has_target():
                with self._state_lock:
                    if self._has_fatal_config_error():
                        self.status = "error"
                        self.detail = self.args.config_error
                    else:
                        self.status = "stopped"
                        self.detail = ""
                self._notify_ui(menu=True)
                return False

            old_thread = self.thread
            if old_thread is not None and old_thread.is_alive():
                old_thread.join(timeout=3.0)
                if old_thread.is_alive():
                    return False

            lock = tool.acquire_single_instance_lock()
            if lock is None:
                with self._state_lock:
                    self._generation += 1
                    self.status = "error"
                    self.detail = "another streamer instance holds the lock"
                log("已有另一个推流实例在运行，托盘不再重复推流")
                self._notify_ui(menu=True)
                return False

            self.lock = lock
            with self._state_lock:
                self._generation += 1
                gen = self._generation
                self._active_gen = gen
                self._stop_requested_gen = 0
                self.status = "connecting"
                self.detail = self.args.name or self.args.id or ""

            self._notify_ui(menu=True)

            def status_cb(new_status: str, new_detail: str = "") -> None:
                with self._state_lock:
                    if gen != self._generation or self._stop_requested_gen >= gen:
                        return
                    self.status = new_status
                    self.detail = new_detail or ""
                self._notify_ui(menu=False)

            def runner() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                stop_event = asyncio.Event()
                def volume_cb(value, applied):
                    with self._state_lock:
                        if gen != self._generation:
                            return
                        self.volume_value = value
                        self.volume_applied = applied
                    self._notify_ui(menu=True)

                volume = tool.LiveVolume(self.args, volume_cb)
                with self._state_lock:
                    self.loop = loop
                    self.stop_event = stop_event
                    self.volume_control = volume
                    self.args.volume_control = volume
                result = 0
                try:
                    log("--- 开始推流（engine=%s, target=%s）---" % (
                        self.args.engine,
                        self.args.name or self.args.id,
                    ))
                    with contextlib.suppress(OSError):
                        os.makedirs(os.path.dirname(os.path.abspath(PID_PATH)), exist_ok=True)
                        with open(PID_PATH, "w", encoding="ascii") as handle:
                            handle.write(str(os.getpid()))
                    result = loop.run_until_complete(
                        tool.main_async(self.args, stop_event, status_callback=status_cb)
                    )
                except Exception as exc:  # noqa: BLE001
                    with self._state_lock:
                        if gen == self._generation:
                            self.status = "error"
                            self.detail = repr(exc)
                    log("推流异常：%r\n%s" % (exc, traceback.format_exc()))
                finally:
                    with self._state_lock:
                        self.loop = None
                        self.volume_control = None
                    # Let already-submitted volume callbacks create their tasks before draining.
                    loop.run_until_complete(asyncio.sleep(0))
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    if self.lock is not None:
                        with contextlib.suppress(OSError):
                            self.lock.close()
                        self.lock = None
                    with contextlib.suppress(Exception):
                        loop.close()
                    with self._state_lock:
                        if gen == self._generation:
                            if result in (0, None):
                                if self.status != "error":
                                    self.status = "stopped"
                                    self.detail = ""
                            elif self.status != "error":
                                self.status = "error"
                                self.detail = "exit %s" % result
                        if self._active_gen == gen:
                            self.loop = None
                            self.stop_event = None
                            self.volume_control = None
                            self.volume_applied = False
                            if self.thread is threading.current_thread():
                                self.thread = None
                    log("--- 推流结束 ---")
                    self._notify_ui(menu=True)

            self.thread = threading.Thread(target=runner, daemon=True, name="airplay-stream")
            self.thread.start()
            return True

    def stop(self, wait_timeout: float = 0.0) -> bool:
        with self._state_lock:
            thread = self.thread
            loop = self.loop
            stop_event = self.stop_event
            alive = thread is not None and thread.is_alive()
            if not alive:
                self._generation += 1
                if not self._has_fatal_config_error():
                    self.status = "stopped"
                    self.detail = ""
                self._notify_ui(menu=True)
                return True
            self._stop_requested_gen = self._active_gen
            self.status = "stopping"
            self.detail = ""
            volume_future = self._volume_future
        self._notify_ui(menu=True)
        if volume_future is not None:
            volume_future.cancel()
        if loop is not None and stop_event is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(stop_event.set)
        if wait_timeout > 0:
            thread.join(timeout=wait_timeout)
            return not thread.is_alive()
        return True

    def select_speaker_sync(self, dev: SpeakerInfo) -> bool:
        """Stop current session, save config, and start streaming to ``dev`` serially."""
        if not self._switch_lock.acquire(blocking=False):
            return False
        try:
            with self._state_lock:
                self.switching = True
                self.detail = dev.name
            self._notify_ui(menu=True)

            stopped = self.stop(wait_timeout=6.0)
            if not stopped:
                with self._state_lock:
                    self.status = "error"
                    self.detail = "previous session did not stop in time"
                log("切换音箱超时：旧推流会话未在 6 秒内结束")
                return False

            with self._op_lock:
                save_device_selection(self.args.config, dev.id, dev.name)
                self.args.id = dev.id or None
                self.args.name = dev.name or None
                self.args.config_error = None
            log("已选择并保存音箱：%s (%s)" % (dev.name, dev.id))
            return self.start()
        except Exception as exc:  # noqa: BLE001
            with self._state_lock:
                self.status = "error"
                self.detail = repr(exc)
            log("切换音箱失败：%r\n%s" % (exc, traceback.format_exc()))
            return False
        finally:
            with self._state_lock:
                self.switching = False
            self._notify_ui(menu=True)
            self._switch_lock.release()

    def select_speaker_async(self, dev: SpeakerInfo) -> None:
        threading.Thread(
            target=self.select_speaker_sync,
            args=(dev,),
            daemon=True,
            name="speaker-switch",
        ).start()

    def scan_devices_sync(self, allow_auto_select: bool = False, on_toast: Optional[Callable[[str], None]] = None) -> List[SpeakerInfo]:
        """Run one LAN AirPlay scan and optionally auto-select a lone HomePod."""
        with self._state_lock:
            if self.scanning:
                return list(self.devices)
            self.scanning = True
            self.scan_error = None
        self._notify_ui(menu=True)

        loop = asyncio.new_event_loop()
        try:
            raw = loop.run_until_complete(tool.scan_devices(loop, timeout=3.0))
            found = [SpeakerInfo.from_device(dev) for dev in raw]
        except Exception as exc:  # noqa: BLE001
            log("扫描局域网音箱失败：%r" % exc)
            with self._state_lock:
                self.scanning = False
                self.scan_error = repr(exc)
            self._notify_ui(menu=True)
            return list(self.devices)
        finally:
            with contextlib.suppress(Exception):
                loop.close()

        with self._state_lock:
            self.devices = found
            self.scanning = False

        if allow_auto_select and self._switch_lock.acquire(blocking=False):
            try:
                chosen = pick_auto_homepod(found, self.args.config, self.args)
                if chosen is not None:
                    save_device_selection(self.args.config, chosen.id, chosen.name)
                    self.args.id = chosen.id or None
                    self.args.name = chosen.name or None
                    self.args.config_error = None
                    log("首次启动自动选择唯一 HomePod：%s (%s)" % (chosen.name, chosen.id))
                    self.start()
                    if on_toast is not None:
                        with contextlib.suppress(Exception):
                            on_toast(ui_text(self.lang, "auto_selected", name=chosen.name))
                elif not self.has_target():
                    log("首次启动未自动选定唯一 HomePod（共发现 %d 台设备），等待用户从托盘选择" % len(found))
                    if on_toast is not None:
                        with contextlib.suppress(Exception):
                            if self._has_fatal_config_error():
                                on_toast(ui_text(self.lang, "config_error_tip"))
                            else:
                                on_toast(ui_text(self.lang, "pick_speaker_tip"))
            finally:
                self._switch_lock.release()

        self._notify_ui(menu=True)
        return list(found)

    def refresh_devices_async(
        self,
        allow_auto_select: bool = False,
        on_toast: Optional[Callable[[str], None]] = None,
    ) -> None:
        threading.Thread(
            target=self.scan_devices_sync,
            args=(allow_auto_select, on_toast),
            daemon=True,
            name="speaker-scan",
        ).start()


def build_args(argv=None, allow_missing: bool = False):
    parser = argparse.ArgumentParser(description="任务栏推流控制器")
    parser.add_argument("--id", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--volume", type=float, default=None)
    parser.add_argument("--latency", type=float, default=0.3)
    parser.add_argument("--prebuffer", type=float, default=0.12)
    parser.add_argument("--idle-timeout", type=float, default=60.0)
    parser.add_argument("--silence-threshold", type=float, default=-55.0)
    parser.add_argument("--report", type=float, default=0.0)
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--retry-delay", type=float, default=3.0)
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--engine", choices=("pipe", "direct"), default="direct")
    parser.add_argument("--dump", default="")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--selftest-live", type=int, default=0)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    return configure_device(args, parser, allow_missing=allow_missing)


def run_self_check() -> int:
    """Offline startup self-check for imports, bundled icon asset, and writable data dir."""
    try:
        import cryptography  # noqa: F401
        import miniaudio  # noqa: F401
        import numpy  # noqa: F401
        import pyatv  # noqa: F401
        import soundcard  # noqa: F401
        from PIL import Image as _PilImage  # noqa: F401

        ico = asset_path("app.ico")
        if not ico.exists():
            raise FileNotFoundError("missing asset: %s" % ico)

        writable = ensure_writable_data_dir()
        probe = writable / (".self-check-%d.tmp" % os.getpid())
        probe.write_text("ok", encoding="ascii")
        probe.unlink()

        from pyatv import const as pyatv_const
        pyatv_ver = "%s.%s.%s" % (
            getattr(pyatv_const, "MAJOR_VERSION", "?"),
            getattr(pyatv_const, "MINOR_VERSION", "?"),
            getattr(pyatv_const, "PATCH_VERSION", "?"),
        )
        print("SELF-CHECK OK: data_dir=%s pyatv=%s" % (writable, pyatv_ver))
        return 0
    except Exception as exc:  # noqa: BLE001
        print("SELF-CHECK FAILED: %r" % exc, file=sys.stderr)
        return 1


def main(argv=None) -> int:
    ensure_windowed_stdio()
    args = build_args(argv, allow_missing=True)
    if args.self_check:
        return run_self_check()
    if args.list:
        return tool.list_devices()

    lang = detect_ui_language()
    if acquire_tray_mutex() is None:
        threading.Thread(
            target=notify,
            args=(ui_text(lang, "duplicate_instance"), ui_text(lang, "app_name")),
            daemon=True,
        ).start()
        time.sleep(5)
        return 0

    data_ready = False
    try:
        ensure_writable_data_dir()
        log_file = str(log_path())
        pid_file = str(pid_path())

        # 没有控制台窗口，把推流过程的输出写进带自动轮转的日志文件
        stream = BoundedLogStream(log_file)
        sys.stdout = stream
        sys.stderr = stream
        with contextlib.suppress(OSError):
            with open(pid_file, "w", encoding="ascii") as handle:
                handle.write(str(os.getpid()))
        data_ready = True

        tool.apply_latency_tuning(args.latency)
        log("托盘程序启动 (lang=%s, config=%s)" % (lang, args.config))

        ui_wakeup = threading.Event()
        menu_dirty = threading.Event()
        stop_ui = threading.Event()
        icon_holder = []

        def on_ui_change(update_menu: bool = False) -> None:
            if update_menu:
                menu_dirty.set()
            ui_wakeup.set()

        controller = Controller(args, lang=lang, on_ui_change=on_ui_change)

        def send_toast(text: str) -> None:
            if not icon_holder:
                return
            icon = icon_holder[0]
            with contextlib.suppress(Exception):
                if getattr(icon, "HAS_NOTIFICATION", False):
                    icon.notify(text, ui_text(lang, "app_name"))

        def speaker_menu_items():
            with controller._state_lock:  # noqa: SLF001
                devices = list(controller.devices)
                scanning = controller.scanning
                switching = controller.switching
                cur_id = controller.args.id
                cur_name = controller.args.name

            items = []
            if switching:
                items.append(pystray.MenuItem(ui_text(lang, "switching_item"), None, enabled=False))
            if scanning and not devices:
                items.append(pystray.MenuItem(ui_text(lang, "scanning"), None, enabled=False))
            elif not devices:
                items.append(pystray.MenuItem(ui_text(lang, "no_speakers"), None, enabled=False))
            else:
                for dev, label in format_speaker_labels(devices):
                    def make_handler(speaker: SpeakerInfo):
                        def handler(icon_obj, item):
                            controller.select_speaker_async(speaker)
                        return handler

                    def make_checked(speaker: SpeakerInfo):
                        return lambda item: matches_target(speaker, controller.args.id, controller.args.name)

                    items.append(
                        pystray.MenuItem(
                            label,
                            make_handler(dev),
                            checked=make_checked(dev),
                            enabled=lambda item: not controller.switching,
                        )
                    )
            items.append(pystray.Menu.SEPARATOR)
            items.append(
                pystray.MenuItem(
                    lambda item: ui_text(lang, "scanning") if controller.scanning else ui_text(lang, "rescan"),
                    lambda icon_obj, item: controller.refresh_devices_async(allow_auto_select=False),
                    enabled=lambda item: not controller.scanning and not controller.switching,
                )
            )
            return items

        controller.on_volume_error = send_toast

        def volume_menu_items():
            items = [pystray.MenuItem(controller.volume_text(), None, enabled=False), pystray.Menu.SEPARATOR]

            def level_item(value):
                def handler(icon_obj, item):
                    controller.request_volume(value=value)

                return pystray.MenuItem("%d%%" % value, handler, checked=lambda item: controller.volume_value == value)

            items.extend(level_item(level) for level in (0, 25, 50, 75, 100))
            items.extend([
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(ui_text(lang, "volume_up"), lambda icon_obj, item: controller.request_volume(delta=5), enabled=lambda item: controller.volume_value is not None),
                pystray.MenuItem(ui_text(lang, "volume_down"), lambda icon_obj, item: controller.request_volume(delta=-5), enabled=lambda item: controller.volume_value is not None),
            ])
            return items

        def on_toggle(icon_obj, item) -> None:
            if controller.switching or controller.status == "stopping":
                return
            if controller.running:
                controller.stop(wait_timeout=1.5)
            elif controller.has_target():
                controller.start()
            else:
                controller.refresh_devices_async(allow_auto_select=False, on_toast=send_toast)
                send_toast(ui_text(lang, "pick_speaker_tip"))
            on_ui_change(update_menu=True)

        def open_log_file(icon_obj, item) -> None:
            trim_log_if_needed(log_file)
            with contextlib.suppress(OSError):
                subprocess.Popen(["notepad.exe", log_file])

        def on_quit(icon_obj, item) -> None:
            stop_ui.set()
            ui_wakeup.set()
            controller.stop(wait_timeout=4.0)
            with contextlib.suppress(OSError):
                os.remove(pid_file)
            log("托盘程序退出")
            icon_obj.stop()

        menu = pystray.Menu(
            pystray.MenuItem(
                lambda item: ui_text(lang, "stop") if controller.running else ui_text(lang, "start"),
                on_toggle,
                default=True,
                enabled=lambda item: not controller.switching and controller.status != "stopping",
            ),
            pystray.MenuItem(
                ui_text(lang, "select_speaker"),
                pystray.Menu(speaker_menu_items),
                enabled=lambda item: not controller.switching,
            ),
            pystray.MenuItem(
                ui_text(lang, "volume" if args.engine == "direct" else "volume_pipe"),
                pystray.Menu(volume_menu_items),
                enabled=lambda item: args.engine == "direct" and not controller.volume_busy and not controller.switching and controller.status != "stopping",
            ),
            pystray.MenuItem(ui_text(lang, "open_log"), open_log_file),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(ui_text(lang, "quit"), on_quit),
        )
        icon = pystray.Icon(
            "airplay-pc-audio",
            make_icon("grey"),
            "%s - %s" % (ui_text(lang, "app_name"), ui_text(lang, "startup_init")),
            menu,
        )
        icon_holder.append(icon)

        if controller.has_target():
            controller.start()
            controller.refresh_devices_async(allow_auto_select=False)
        else:
            controller.refresh_devices_async(
                allow_auto_select=not Path(args.config).exists(),
                on_toast=send_toast,
            )

        def refresh_ui() -> None:
            last_icon_key = None
            last_title = None
            blink_on = True
            while not stop_ui.is_set():
                ui_wakeup.wait(0.45)
                ui_wakeup.clear()
                if stop_ui.is_set():
                    break
                blink_on = not blink_on
                desired_key = controller.icon_state(blink_on=blink_on)
                desired_title = controller.tooltip_title()
                with contextlib.suppress(Exception):
                    if desired_key != last_icon_key:
                        icon.icon = make_icon(desired_key)
                        last_icon_key = desired_key
                    if desired_title != last_title:
                        icon.title = desired_title
                        last_title = desired_title
                    if menu_dirty.is_set():
                        menu_dirty.clear()
                        icon.update_menu()

        threading.Thread(target=refresh_ui, daemon=True, name="tray-refresh").start()
        icon.run()
        return 0
    except Exception as exc:  # noqa: BLE001
        log("托盘未捕获异常：%r\n%s" % (exc, traceback.format_exc()))
        if not data_ready:
            msg = ui_text(lang, "data_dir_error", path=str(data_dir()), error=exc)
            notify(msg, ui_text(lang, "app_name"))
        else:
            notify("Error: %s\nSee tray.log for details." % exc, ui_text(lang, "app_name"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
