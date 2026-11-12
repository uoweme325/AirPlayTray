"""任务栏（通知区域）图标：右键开关推流。"""
import argparse
import asyncio
import contextlib
import ctypes
import os
import subprocess
import sys
import threading
import time

import pystray
from PIL import Image, ImageDraw

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import airplay_system_audio as tool  # noqa: E402
from settings import DEFAULT_CONFIG, configure_device

LOG_PATH = os.path.join(SCRIPT_DIR, "airplay-tray.log")
PID_PATH = os.path.join(SCRIPT_DIR, "airplay-system-audio.pid")
MUTEX_NAME = "Local\\AirPlayPCTrayIcon"
MAX_LOG_BYTES = 2 * 1024 * 1024
KEEP_LOG_BYTES = 256 * 1024


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


def acquire_tray_mutex():
    """只允许一个托盘实例；已被占用时返回 None。"""
    ERROR_ALREADY_EXISTS = 183
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        return None
    return handle


def notify(message, title="AirPlay 推流"):
    with contextlib.suppress(Exception):
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)


def log(message: str) -> None:
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))


def make_icon(active: bool) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    colour = (46, 134, 222, 255) if active else (130, 130, 140, 255)
    draw.ellipse((4, 4, 60, 60), fill=colour)
    # 简单的喇叭图形
    draw.polygon([(22, 26), (30, 26), (40, 16), (40, 48), (30, 38), (22, 38)],
                 fill=(255, 255, 255, 255))
    if active:
        draw.arc((34, 20, 52, 44), start=-60, end=60, fill=(255, 255, 255, 255), width=3)
        draw.arc((38, 14, 60, 50), start=-55, end=55, fill=(255, 255, 255, 255), width=3)
    return img


class Controller:
    """在后台线程里跑推流，托盘菜单控制开关。"""

    def __init__(self, args):
        self.args = args
        self.thread = None
        self.loop = None
        self.stop_event = None
        self.lock = None
        self.state = "已停止"
        self._op_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        with self._op_lock:
            old_thread = self.thread
            if old_thread is not None and old_thread.is_alive():
                if self.state == "停止中…":
                    old_thread.join(timeout=3.0)
                if old_thread.is_alive():
                    return
            self.lock = tool.acquire_single_instance_lock()
            if self.lock is None:
                self.state = "已停止（有另一个实例在跑）"
                log("已有另一个推流实例在运行，托盘不再重复推流")
                return
            self.state = "启动中…"

            def runner():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                stop_event = asyncio.Event()
                self.loop = loop
                self.stop_event = stop_event
                try:
                    log("--- 开始推流（engine=%s）---" % self.args.engine)
                    with contextlib.suppress(OSError):
                        with open(PID_PATH, "w", encoding="ascii") as handle:
                            handle.write(str(os.getpid()))
                    self.state = "推流中"
                    result = loop.run_until_complete(tool.main_async(self.args, stop_event))
                    self.state = "已停止" if result == 0 else "出错（返回码 %s）" % result
                except Exception as exc:  # noqa: BLE001
                    self.state = "出错"
                    log("推流异常：%r" % exc)
                finally:
                    if self.lock is not None:
                        with contextlib.suppress(OSError):
                            self.lock.close()
                        self.lock = None
                    with contextlib.suppress(Exception):
                        loop.close()
                    log("--- 推流结束 ---")
                    if self.loop is loop:
                        self.loop = None
                    if self.stop_event is stop_event:
                        self.stop_event = None
                    if self.thread is threading.current_thread():
                        self.thread = None

            self.thread = threading.Thread(target=runner, daemon=True, name="airplay-stream")
            self.thread.start()

    def stop(self, wait_timeout: float = 0.0):
        thread = self.thread
        loop = self.loop
        stop_event = self.stop_event
        if thread is None or not thread.is_alive() or loop is None or stop_event is None:
            return
        self.state = "停止中…"
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(stop_event.set)
        if wait_timeout > 0:
            thread.join(timeout=wait_timeout)


def build_args(argv=None):
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
    args = parser.parse_args(argv)
    return configure_device(args, parser)


def main(argv=None):
    args = build_args(argv)
    if args.list:
        return tool.list_devices()
    if acquire_tray_mutex() is None:
        # 用线程弹提示，最多等 5 秒，避免用户没点确定就卡住
        threading.Thread(
            target=notify,
            args=("AirPlay 推流已经在后台运行了。\n\n"
                  "请看任务栏右下角的喇叭图标（可能在 ^ 折叠区里），右键它可以开关推流。",),
            daemon=True,
        ).start()
        time.sleep(5)
        return 0
    tool.apply_latency_tuning(args.latency)

    # 没有控制台窗口，把推流过程的输出写进带自动轮转的日志文件
    stream = BoundedLogStream(LOG_PATH)
    sys.stdout = stream
    sys.stderr = stream
    with contextlib.suppress(OSError):
        with open(PID_PATH, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
    log("托盘程序启动")
    controller = Controller(args)

    def on_toggle(icon, item):
        if controller.running and controller.state != "停止中…":
            controller.stop(wait_timeout=1.5)
        else:
            controller.start()
        icon.icon = make_icon(controller.running)
        icon.title = "AirPlay 推流：%s" % controller.state

    def open_log(icon, item):
        trim_log_if_needed(LOG_PATH)
        with contextlib.suppress(OSError):
            subprocess.Popen(["notepad.exe", LOG_PATH])

    def on_quit(icon, item):
        controller.stop(wait_timeout=4.0)
        with contextlib.suppress(OSError):
            os.remove(PID_PATH)
        log("托盘程序退出")
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: "停止推流" if controller.running else "开始推流",
                         on_toggle, default=True),
        pystray.MenuItem("查看日志", open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit),
    )
    icon = pystray.Icon("airplay-pc-audio", make_icon(False),
                        "AirPlay 推流：已启动，正在连接…", menu)

    controller.start()

    def refresh_title():
        while True:
            time.sleep(1.0)
            with contextlib.suppress(Exception):
                icon.icon = make_icon(controller.running)
                icon.title = "AirPlay 推流：%s" % controller.state

    threading.Thread(target=refresh_title, daemon=True, name="tray-refresh").start()
    icon.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
