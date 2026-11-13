"""把 Windows 整机声音实时推到 AirPlay 音箱（HomePod / HomePod mini）。

原理：用 WASAPI 环回抓默认播放设备的声音，经 AirPlay 2（HAP 临时配对）实时推送。
电脑安静一段时间就自动释放音箱，一有声音自动接回去。
"""
import argparse
import asyncio
import contextlib
import inspect
import io
import logging
import math
import os
import threading
import time
import warnings

import numpy as np
import soundcard as sc
import pyatv
from pyatv.const import DeviceModel
from pyatv.protocols.airplay.auth import extract_credentials
from pyatv.protocols.airplay.utils import pct_to_dbfs
from pyatv.protocols.raop import RaopStream, audio_source
from pyatv.support.metadata import EMPTY_METADATA
from app_paths import ensure_writable_data_dir, lock_path, pid_path
from settings import DEFAULT_CONFIG, configure_device

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_RATE = 44100
CHANNELS = 2
BLOCK = 512
LOCK_FILE = str(lock_path())
PID_FILE = str(pid_path())
RETRANSMIT_STATS = {"count": 0}
HOMEPOD_MODELS = frozenset({
    DeviceModel.HomePod,
    DeviceModel.HomePodMini,
    DeviceModel.HomePodGen2,
})


def apply_latency_tuning(latency_seconds):
    """把"告诉音箱的播放延迟"和"音箱自己的缓冲区间"一起调小。

    pyatv 默认按 1.5 秒延迟播报（22050 + 采样率），AirPlay 1.5 秒的拖后腿就是这么来的。
    """
    from pyatv.protocols.raop.protocols import StreamContext
    from pyatv.protocols.raop.stream_client import ControlClient
    from pyatv.support.rtsp import RtspSession

    target = int(max(latency_seconds, 0.25) * SAMPLE_RATE)
    upper = target + max(SAMPLE_RATE // 4, target // 2)

    if not getattr(StreamContext.reset, "_airplay_tuned", False):
        original_reset = StreamContext.reset

        def reset(self):
            original_reset(self)
            self.latency = target

        reset._airplay_tuned = True
        StreamContext.reset = reset

    if not getattr(RtspSession.setup, "_airplay_tuned", False):
        original_setup = RtspSession.setup

        async def setup(self, headers=None, body=None):
            if isinstance(body, dict) and isinstance(body.get("streams"), list):
                for stream in body["streams"]:
                    if isinstance(stream, dict) and "latencyMax" in stream:
                        stream["latencyMin"] = target
                        stream["latencyMax"] = upper
            return await original_setup(self, headers=headers, body=body)

        setup._airplay_tuned = True
        RtspSession.setup = setup

    if not getattr(ControlClient._retransmit_lost_packets, "_airplay_tuned", False):
        original_retransmit = ControlClient._retransmit_lost_packets

        def retransmit(self, request, addr):
            RETRANSMIT_STATS["count"] += 1
            return original_retransmit(self, request, addr)

        retransmit._airplay_tuned = True
        ControlClient._retransmit_lost_packets = retransmit

    return target / SAMPLE_RATE


def loopback_device():
    """默认播放设备对应的环回输入。"""
    speaker = sc.default_speaker()
    return sc.get_microphone(id=str(speaker.name), include_loopback=True)


def acquire_single_instance_lock():
    """防止双击好几次、起了多个推流互相抢音箱。"""
    try:
        import msvcrt
    except ImportError:
        return True
    ensure_writable_data_dir()
    handle = open(LOCK_FILE, "a+")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def chunk_to_pcm(chunk) -> bytes:
    return (np.clip(chunk, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def to_big_endian(pcm: bytes) -> bytes:
    """AirPlay 的 PCM 是大端序；Windows 抓到的声音是小端序，必须翻过来。"""
    return np.frombuffer(pcm, dtype="<i2").astype(">i2").tobytes()


def peak_dbfs(chunk) -> float:
    if chunk is None or len(chunk) == 0:
        return -120.0
    peak = float(np.max(np.abs(chunk)))
    return 20 * math.log10(peak) if peak > 0 else -120.0


def wav_header(sample_rate, channels, sample_size, data_size=0x7FFFFFFF):
    """RIFF/WAVE 头，数据长度写成"几乎无限"，用于持续直播。"""
    import struct

    block_align = channels * sample_size
    return (
        b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                                sample_rate * block_align, block_align, sample_size * 8)
        + b"data" + struct.pack("<I", data_size)
    )


class LiveWavPipe(io.BufferedIOBase):
    """把实时抓到的声音伪装成一条永不结束的 WAV 流，交给 pyatv 官方接口。

    官方那条路（stream_file）实测稳定，所以这里复用它的全部处理流程。
    """

    def __init__(self, sample_rate, channels, sample_size, prebuffer=0.3,
                 dump_path=None):
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_size = sample_size
        self._frame_bytes = channels * sample_size
        self._header = wav_header(sample_rate, channels, sample_size)
        self._header_pos = 0
        self._buf = bytearray()
        self._lock = threading.Condition()
        self._closed = False
        self._block = 8192
        self._prime = max(int(prebuffer * sample_rate), BLOCK) * self._frame_bytes
        self._max_buf = max(int(max(prebuffer * 2.5, 0.35) * sample_rate), BLOCK * 4) * self._frame_bytes
        self._started = False
        self.frames_fed = 0
        self.peak_dbfs = -120.0
        self.window_peak = -120.0
        self.underruns = 0
        self.last_loud = time.monotonic()
        self._threshold = -55.0
        self._dump = None
        if dump_path:
            import wave

            self._dump = wave.open(dump_path, "wb")
            self._dump.setnchannels(channels)
            self._dump.setsampwidth(sample_size)
            self._dump.setframerate(sample_rate)

    # --- 生产端 ---
    def feed(self, pcm: bytes, level_db: float) -> None:
        if not pcm:
            return
        if self._dump is not None:
            self._dump.writeframes(pcm)
        self.frames_fed += len(pcm) // self._frame_bytes
        if level_db > self._threshold:
            self.last_loud = time.monotonic()
        self.peak_dbfs = max(self.peak_dbfs, level_db)
        self.window_peak = max(self.window_peak, level_db)
        with self._lock:
            self._buf.extend(pcm)
            if len(self._buf) > self._max_buf:
                drop_bytes = ((len(self._buf) - self._prime) // self._frame_bytes) * self._frame_bytes
                if drop_bytes > 0:
                    del self._buf[:drop_bytes]
            if not self._started and len(self._buf) >= self._prime:
                self._started = True
            self._lock.notify_all()

    def close_feed(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()

    def take_window_peak(self) -> float:
        value, self.window_peak = self.window_peak, -120.0
        return value

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_loud

    # --- 消费端（pyatv / miniaudio 在工作线程里调用） ---
    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def read(self, size=-1):
        out = bytearray()
        if self._header_pos < len(self._header):
            take = len(self._header) - self._header_pos
            if size and size > 0:
                take = min(take, size)
            out += self._header[self._header_pos:self._header_pos + take]
            self._header_pos += take
            if size and size > 0 and len(out) >= size:
                return bytes(out)

        want = self._block if not size or size < 0 else size - len(out)
        with self._lock:
            if not self._started:
                self._lock.wait_for(
                    lambda: self._closed or len(self._buf) >= self._prime,
                    timeout=0.5,
                )
                self._started = True
            self._lock.wait_for(
                lambda: self._closed or len(self._buf) >= min(want, self._block),
                timeout=1.0,
            )
            take = min(want, len(self._buf))
            out += bytes(self._buf[:take])
            del self._buf[:take]
        if take == 0 and self._closed:
            return bytes(out) if out else b""
        if take == 0:
            self.underruns += 1
            out += b"\x00" * want
        return bytes(out)

    def close(self) -> None:
        self.close_feed()
        if self._dump is not None:
            self._dump.close()
            self._dump = None
        super().close()


class LiveSystemAudio(audio_source.AudioSource):
    """实时 PCM 音源：抓音线程往里塞，pyatv 从里面取。"""

    def __init__(self, sample_rate, channels, sample_size, prebuffer=0.25,
                 silence_threshold=-55.0, dump_path=None, on_active=None):
        self._rate = sample_rate
        self._channels = channels
        self._size = sample_size
        self._frame_bytes = channels * sample_size
        self._buf = bytearray()
        self._lock = threading.Condition()
        self._closed = False
        self._on_active = on_active
        self._active_notified = False
        pre_frames = max(int(prebuffer * sample_rate), 352 * 2)
        self._prebuffer_bytes = pre_frames * self._frame_bytes
        self._low_water_bytes = max(pre_frames // 2, 352) * self._frame_bytes
        self._high_water_bytes = int(pre_frames * 1.6) * self._frame_bytes
        self._max_buf_bytes = max(int(pre_frames * 2.5), int(0.35 * sample_rate)) * self._frame_bytes
        self._started = False
        self._last_frame = b"\x00" * self._frame_bytes
        self._threshold = silence_threshold
        self.last_loud = time.monotonic()
        self.underruns = 0
        self.peak_dbfs = -120.0
        self.window_peak = -120.0
        self.frames_fed = 0
        self._dump = None
        if dump_path:
            import wave

            self._dump = wave.open(dump_path, "wb")
            self._dump.setnchannels(channels)
            self._dump.setsampwidth(sample_size)
            self._dump.setframerate(sample_rate)

    # --- 生产端：抓音线程 ---
    def feed(self, pcm: bytes, level_db: float) -> None:
        if not pcm:
            return
        if self._dump is not None:
            self._dump.writeframes(pcm)
        pcm = to_big_endian(pcm)
        fb = self._frame_bytes
        self.frames_fed += len(pcm) // fb
        if level_db > self._threshold:
            self.last_loud = time.monotonic()
        if level_db > self.peak_dbfs:
            self.peak_dbfs = level_db
        if level_db > self.window_peak:
            self.window_peak = level_db
        with self._lock:
            if len(pcm) >= fb:
                self._last_frame = pcm[-fb:]
            buf_len = len(self._buf)
            if self._started:
                # 弹性时钟微调：补偿声卡晶振与系统 monotonic 时钟的微小频偏（每次仅 1 帧 ≈ 0.02ms）
                if buf_len < self._low_water_bytes and len(pcm) >= fb:
                    pcm = pcm + self._last_frame
                elif buf_len > self._high_water_bytes and len(pcm) > fb:
                    pcm = pcm[:-fb]
            self._buf.extend(pcm)
            # 水位硬上限：若因系统瞬时卡顿积压过多旧音频，丢弃多余旧帧防止延迟永久累加
            if len(self._buf) > self._max_buf_bytes:
                drop_bytes = ((len(self._buf) - self._prebuffer_bytes) // fb) * fb
                if drop_bytes > 0:
                    del self._buf[:drop_bytes]
            if not self._started and len(self._buf) >= self._prebuffer_bytes:
                self._started = True
            self._lock.notify_all()

    def close_feed(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_loud

    def take_window_peak(self) -> float:
        """Return the peak level since the last call (for the status line)."""
        value = self.window_peak
        self.window_peak = -120.0
        return value

    # --- 消费端：pyatv ---
    async def readframes(self, nframes: int) -> bytes:
        want = nframes * self._frame_bytes
        return await asyncio.get_event_loop().run_in_executor(None, self._read, want)

    def _read(self, want: int) -> bytes:
        fb = self._frame_bytes
        notify_cb = None
        with self._lock:
            if not self._started:
                self._lock.wait_for(
                    lambda: self._closed or len(self._buf) >= self._prebuffer_bytes,
                    timeout=0.5,
                )
                # 首包对齐到目标预缓冲水位，避免把 RTSP 握手期间多录的音频变成额外延迟
                target_initial = self._prebuffer_bytes + want
                if len(self._buf) > target_initial:
                    drop_bytes = ((len(self._buf) - target_initial) // fb) * fb
                    if drop_bytes > 0:
                        del self._buf[:drop_bytes]
                self._started = True
            elif len(self._buf) < want and not self._closed:
                # 仅短暂等待至多 4ms，绝不长时间阻塞 pyatv 发包循环导致 RTP 时间戳落后
                self._lock.wait_for(
                    lambda: self._closed or len(self._buf) >= want,
                    timeout=0.004,
                )
            if self._closed and len(self._buf) == 0:
                return b""
            take = (min(want, len(self._buf)) // fb) * fb
            data = bytes(self._buf[:take])
            del self._buf[:take]
            if len(data) < want and not self._closed:
                self.underruns += 1
                missing_frames = (want - len(data)) // fb
                data += self._last_frame * missing_frames
                # 欠载时回充半个低水位缓冲，防止下一包连续欠载触发追帧日志
                cushion_frames = max((self._low_water_bytes // fb) // 2, 1)
                self._buf.extend(self._last_frame * cushion_frames)
            if not self._closed and not self._active_notified and self._on_active is not None:
                self._active_notified = True
                notify_cb = self._on_active
        if notify_cb is not None:
            with contextlib.suppress(Exception):
                notify_cb()
        return data

    async def get_metadata(self):
        return EMPTY_METADATA

    async def close(self) -> None:
        self.close_feed()
        if self._dump is not None:
            self._dump.close()
            self._dump = None

    @property
    def sample_rate(self) -> int:
        return self._rate

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def sample_size(self) -> int:
        return self._size

    @property
    def duration(self) -> int:
        return 0


class SineSource(audio_source.AudioSource):
    """自检用：直接生成单音（不经过抓音环节）。"""

    def __init__(self, sample_rate, channels, sample_size, freq=523.25, seconds=4.0):
        self._rate = sample_rate
        self._channels = channels
        self._size = sample_size
        self._freq = freq
        self._left = int(seconds * sample_rate)
        self._index = 0

    async def readframes(self, nframes: int) -> bytes:
        if self._left <= 0:
            return b""
        count = min(nframes, self._left)
        idx = np.arange(self._index, self._index + count, dtype=np.float64)
        wave_data = (0.25 * np.sin(2 * np.pi * self._freq * idx / self._rate) * 32767).astype("<i2")
        self._index += count
        self._left -= count
        interleaved = np.repeat(wave_data, self._channels)
        return interleaved.tobytes()

    async def get_metadata(self):
        return EMPTY_METADATA

    @property
    def sample_rate(self) -> int:
        return self._rate

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def sample_size(self) -> int:
        return self._size

    @property
    def duration(self) -> int:
        return int(self._left / self._rate)


class BeepSource(audio_source.AudioSource):
    """自检用：发出"嘟-嘟-嘟"三声短音（有停顿，方便分辨是否被正确还原）。"""

    def __init__(self, sample_rate, channels, sample_size, beeps=3, on=0.45,
                 off=0.45, freq=880.0, amplitude=0.3):
        self._rate = sample_rate
        self._channels = channels
        self._size = sample_size
        self._freq = freq
        self._amp = amplitude
        self._on = int(on * sample_rate)
        self._period = int((on + off) * sample_rate)
        self._total = self._period * beeps
        self._pos = 0

    async def readframes(self, nframes: int) -> bytes:
        if self._pos >= self._total:
            return b""
        count = min(nframes, self._total - self._pos)
        idx = np.arange(self._pos, self._pos + count, dtype=np.float64)
        phase = idx % self._period
        tone = self._amp * np.sin(2 * np.pi * self._freq * idx / self._rate)
        edge = np.minimum(phase, self._period - phase) / (self._rate * 0.01)
        data = np.where(phase < self._on, tone * np.clip(edge, 0.0, 1.0), 0.0)
        self._pos += count
        pcm = (data * 32767.0).astype("<i2")
        return np.repeat(pcm, self._channels).astype(">i2").tobytes()

    async def get_metadata(self):
        return EMPTY_METADATA

    @property
    def sample_rate(self) -> int:
        return self._rate

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def sample_size(self) -> int:
        return self._size

    @property
    def duration(self) -> int:
        return int((self._total - self._pos) / self._rate)


def start_capture(source: LiveSystemAudio, stop: threading.Event):
    """启动环回抓音线程。"""
    loopback = loopback_device()

    def run():
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with loopback.recorder(
                    samplerate=source.sample_rate, channels=source.channels, blocksize=BLOCK
                ) as rec:
                    while not stop.is_set():
                        chunk = rec.record(numframes=BLOCK)
                        source.feed(chunk_to_pcm(chunk), peak_dbfs(chunk))
        except Exception as exc:  # noqa: BLE001
            if not stop.is_set():
                print("[抓音异常] 声卡环回中断（%r），准备重连…" % exc)
        finally:
            source.close_feed()

    thread = threading.Thread(target=run, daemon=True, name="audio-capture")
    thread.start()
    return thread, loopback


async def wait_for_sound(args, stop_event):
    """待机时盯着默认播放设备，一有声音就返回。"""
    loop = asyncio.get_event_loop()
    activity = asyncio.Event()
    thread_stop = threading.Event()
    threshold = 10 ** (args.silence_threshold / 20.0)

    def watch():
        last_note = time.monotonic()
        running_peak = -120.0
        while not thread_stop.is_set():
            try:
                loopback = loopback_device()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    with loopback.recorder(
                        samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK
                    ) as rec:
                        while not thread_stop.is_set():
                            chunk = rec.record(numframes=BLOCK)
                            level = peak_dbfs(chunk)
                            running_peak = max(running_peak, level)
                            if args.report and time.monotonic() - last_note > 15:
                                last_note = time.monotonic()
                                print("[待机] 正在监听声音（近 15 秒峰值 %.1f dBFS）" % running_peak)
                                running_peak = -120.0
                            if chunk is not None and len(chunk) and float(np.max(np.abs(chunk))) > threshold:
                                loop.call_soon_threadsafe(activity.set)
                                return
            except Exception:  # noqa: BLE001
                if thread_stop.is_set():
                    return
                time.sleep(1.0)

    threading.Thread(target=watch, daemon=True, name="sound-watch").start()
    while not stop_event.is_set() and not activity.is_set():
        await asyncio.sleep(0.1)
    thread_stop.set()
    return activity.is_set()


def is_homepod_device(conf) -> bool:
    """Return True only when pyatv metadata clearly identifies a HomePod."""
    info = getattr(conf, "device_info", None)
    if info is None:
        return False
    if getattr(info, "model", None) in HOMEPOD_MODELS:
        return True
    raw_model = getattr(info, "raw_model", None) or ""
    return isinstance(raw_model, str) and raw_model.startswith("AudioAccessory")


async def scan_devices(loop, timeout=3.0):
    """Scan local AirPlay devices in deterministic name/identifier order."""
    found = await pyatv.scan(loop, timeout=timeout)
    return sorted(
        found,
        key=lambda dev: (
            str(getattr(dev, "name", "")).lower(),
            str(getattr(dev, "identifier", "")),
        ),
    )


def _emit_status(status_callback, state, detail=""):
    if status_callback is None:
        return
    with contextlib.suppress(Exception):
        status_callback(state, detail)


def _accepts_kwarg(func, kwarg_name: str) -> bool:
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return kwarg_name in params or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


async def find_device(loop, identifier=None, name=None):
    """按标识或名字找设备；传入 identifier 时可立即返回（~70ms），无需死等 3 秒。"""
    found = await pyatv.scan(loop, timeout=3, identifier=identifier if identifier else None)
    if not found and identifier:
        found = await pyatv.scan(loop, timeout=3)
    if not found:
        found = await pyatv.scan(loop, timeout=8)
    if identifier:
        for dev in found:
            if dev.identifier.lower() == identifier.lower():
                return dev
        for dev in found:
            if any(cid.lower() == identifier.lower() for cid in dev.all_identifiers):
                return dev
        return None
    if name:
        for dev in found:
            if name.lower() in dev.name.lower():
                return dev
        return None
    return found[0] if found else None


def _attach_pipe_active_hook(atv, conf_name, status_callback):
    """Emit ``streaming`` on the first frame read after ``client.send_audio`` begins."""
    if status_callback is None:
        return
    interfaces = getattr(getattr(atv, "stream", None), "_interfaces", None)
    if not isinstance(interfaces, dict):
        return
    raop = next((iface for iface in interfaces.values() if isinstance(iface, RaopStream)), None)
    if raop is None:
        return
    manager = raop.playback_manager
    orig_setup = manager.setup

    async def setup_with_active(*setup_args, **setup_kwargs):
        client, context = await orig_setup(*setup_args, **setup_kwargs)
        orig_send_audio = client.send_audio

        async def send_audio_with_active(audio_src, *send_args, **send_kwargs):
            orig_readframes = audio_src.readframes
            fired = False

            async def watched_readframes(nframes):
                nonlocal fired
                data = await orig_readframes(nframes)
                if not fired and data:
                    fired = True
                    _emit_status(status_callback, "streaming", conf_name)
                return data

            audio_src.readframes = watched_readframes
            return await orig_send_audio(audio_src, *send_args, **send_kwargs)

        client.send_audio = send_audio_with_active
        return client, context

    manager.setup = setup_with_active


async def stream_pipe_session(loop, conf, args, stop_event, status_callback=None):
    """走 pyatv 官方 stream_file：把实时抓音伪装成一条永不结束的 WAV 流。"""
    _emit_status(status_callback, "connecting", conf.name)
    atv = await pyatv.connect(conf, loop)
    _attach_pipe_active_hook(atv, conf.name, status_callback)
    pipe = LiveWavPipe(
        SAMPLE_RATE,
        CHANNELS,
        2,
        prebuffer=args.prebuffer,
        dump_path=args.dump or None,
    )
    capture_stop = threading.Event()
    reason = "stopped"
    try:
        _, loopback = start_capture(pipe, capture_stop)
        print("[开始] 已接上 %s（抓音：%s，官方通道）" % (conf.name, loopback.name))

        task = asyncio.ensure_future(atv.stream.stream_file(pipe))
        deadline = time.monotonic() + args.seconds if args.seconds else None
        last_report = time.monotonic()
        while not task.done():
            stop_now = stop_event.is_set()
            time_up = bool(deadline) and time.monotonic() >= deadline
            idle = bool(args.idle_timeout) and pipe.idle_seconds() > args.idle_timeout
            if stop_now or time_up or idle:
                reason = "idle" if idle else "stopped"
                pipe.close_feed()
                break
            await asyncio.sleep(0.2)
            if args.report and time.monotonic() - last_report >= args.report:
                last_report = time.monotonic()
                window = pipe.take_window_peak()
                print("[状态] 已推送 %.0f 秒，近 %g 秒峰值 %.1f dBFS（累计 %.1f），欠载 %d 次"
                      % (pipe.frames_fed / SAMPLE_RATE, args.report, window,
                         pipe.peak_dbfs, pipe.underruns))
                if window < -45.0:
                    print("[注意] 这段时间几乎抓不到声音（峰值 %.1f dBFS）" % window)
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if (not stop_event.is_set()
                and not (deadline and time.monotonic() >= deadline)
                and reason != "idle"):
            raise RuntimeError("官方通道推流意外断开")
    finally:
        capture_stop.set()
        with contextlib.suppress(Exception):
            pipe.close()
        atv.close()
    return reason


async def run_stream(loop, conf, args, stop_event, synthetic_beeps=False, status_callback=None):
    """按 --engine 选择通道：pipe = pyatv 官方，direct = 自建实时通道。"""
    if args.engine == "pipe" and not synthetic_beeps:
        return await stream_pipe_session(loop, conf, args, stop_event, status_callback=status_callback)
    return await stream_session(
        loop, conf, args, stop_event, synthetic_beeps=synthetic_beeps, status_callback=status_callback
    )


async def stream_session(loop, conf, args, stop_event, synthetic_beeps=False, status_callback=None):
    """一次会话：连接 → 抓音 → 推送。返回 'stopped' 或 'idle'。"""
    if not synthetic_beeps:
        _emit_status(status_callback, "connecting", conf.name)
    atv = await pyatv.connect(conf, loop)
    source = None
    reason = "stopped"
    capture_stop = threading.Event()
    try:
        raop = next(
            iface
            for iface in atv.stream._interfaces.values()  # noqa: SLF001
            if isinstance(iface, RaopStream)
        )
        manager = raop.playback_manager
        core = manager.core
        manager.acquire()
        try:
            client, context = await manager.setup(core.service)
            context.credentials = extract_credentials(core.service)
            context.password = core.service.password
            client.listener = raop.listener
            await client.initialize(core.service.properties)

            RETRANSMIT_STATS["count"] = 0
            if synthetic_beeps:
                source = BeepSource(
                    context.sample_rate,
                    context.channels,
                    context.bytes_per_channel,
                    beeps=int(synthetic_beeps),
                )
            else:
                source = LiveSystemAudio(
                    context.sample_rate,
                    context.channels,
                    context.bytes_per_channel,
                    prebuffer=args.prebuffer,
                    silence_threshold=args.silence_threshold,
                    dump_path=args.dump or None,
                    on_active=lambda: _emit_status(status_callback, "streaming", conf.name),
                )
                if args.dump:
                    print("[记录] 正在把推给音箱的原始音频写到 %s" % args.dump)
                # 提前在设置音量与 RTSP 握手前启动抓音，让预缓冲在握手期间自然填满
                _, loopback = start_capture(source, capture_stop)

            # AirPlay 2 的音频流音量必须由发送端明确设置：不设，音箱就当静音处理。
            # 官方文件通道也是这么做的（先发 SET_PARAMETER volume，再 RECORD）。
            if args.volume is not None:
                target_db = pct_to_dbfs(args.volume)
            elif isinstance(client.info.get("initialVolume"), float):
                target_db = client.info["initialVolume"]
            else:
                target_db = pct_to_dbfs(33.0)
            target_db = round(target_db, 2)
            await client.set_volume(target_db)
            print("[音量] 已把这一路的音量设为 %.2f dB" % target_db)

            if synthetic_beeps:
                print("[开始] 自检音：%d 声（不经过抓音）" % int(synthetic_beeps))
            else:
                print("[开始] 已接上 %s（抓音：%s，%d Hz，播放延迟 %.2f 秒）"
                      % (conf.name, loopback.name, context.sample_rate, args.latency))

            task = asyncio.ensure_future(client.send_audio(source, EMPTY_METADATA))
            deadline = time.monotonic() + args.seconds if args.seconds else None
            last_report = time.monotonic()
            while not task.done():
                if stop_event.is_set():
                    client.stop()
                    break
                if deadline and time.monotonic() >= deadline:
                    client.stop()
                    break
                if (args.idle_timeout and not synthetic_beeps
                        and source.idle_seconds() > args.idle_timeout):
                    reason = "idle"
                    client.stop()
                    break
                await asyncio.sleep(0.2)
                if (args.report and not synthetic_beeps
                        and time.monotonic() - last_report >= args.report):
                    last_report = time.monotonic()
                    window = source.take_window_peak()
                    print("[状态] 已推送 %.0f 秒，近 %g 秒峰值 %.1f dBFS（累计 %.1f），欠载 %d 次"
                          % (source.frames_fed / context.sample_rate,
                             args.report, window, source.peak_dbfs, source.underruns)
                          + ("，断包补传 %d 次" % RETRANSMIT_STATS["count"]))
                    if window < -45.0:
                        print("[注意] 这段时间几乎抓不到声音（峰值 %.1f dBFS）——"
                              "播放器可能把声音送到了别的输出设备，或者正在静音播放。"
                              % window)
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if (not synthetic_beeps
                    and not stop_event.is_set()
                    and not (deadline and time.monotonic() >= deadline)
                    and reason != "idle"):
                raise RuntimeError("音频推流通道意外中断")
        finally:
            capture_stop.set()
            with contextlib.suppress(Exception):
                await manager.teardown()
    finally:
        if source:
            with contextlib.suppress(Exception):
                await source.close()
        atv.close()
    return reason


async def wait_for_device(loop, args, stop_event, status_callback=None):
    """启动时等音箱上线；停止操作也能取消正在进行的扫描。"""
    attempts = 0
    target_label = args.name or args.id or ""
    _emit_status(status_callback, "connecting", target_label)
    while not stop_event.is_set():
        scan = asyncio.create_task(find_device(loop, args.id, args.name))
        stopped = asyncio.create_task(stop_event.wait())
        try:
            done, _ = await asyncio.wait((scan, stopped), return_when=asyncio.FIRST_COMPLETED)
            if stopped in done:
                return None
            conf = await scan
            if conf is not None:
                return conf
        except Exception as exc:  # noqa: BLE001
            print("[发现设备异常] %r" % exc)
        finally:
            scan.cancel()
            stopped.cancel()
            await asyncio.gather(scan, stopped, return_exceptions=True)
        attempts += 1
        if args.max_retries and attempts > args.max_retries:
            print("[错误] 没找到音箱，已达到重试上限。用 --list 检查设备。")
            return None
        _emit_status(status_callback, "retrying", target_label)
        print("[等待音箱] 没找到音箱，%g 秒后重试…" % args.retry_delay)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=args.retry_delay)
    return None


async def main_async(args, external_stop=None, status_callback=None):
    loop = asyncio.get_event_loop()
    applied = apply_latency_tuning(args.latency)
    stop_event = external_stop if external_stop is not None else asyncio.Event()
    if status_callback is not None and _accepts_kwarg(wait_for_device, "status_callback"):
        conf = await wait_for_device(loop, args, stop_event, status_callback=status_callback)
    else:
        conf = await wait_for_device(loop, args, stop_event)
    if conf is None:
        if stop_event.is_set():
            _emit_status(status_callback, "stopped", "")
            return 0
        _emit_status(status_callback, "error", "not_found")
        return 2

    print("[目标] %s (%s)" % (conf.name, conf.address))
    print("[延迟] 按 %.2f 秒推送（想更稳就调大 --latency，想更跟手就调小）" % applied)
    if args.idle_timeout:
        print("[待机] 安静超过 %g 秒会先释放音箱，有声音再自动接回" % args.idle_timeout)

    if args.selftest:
        return await selftest(loop, conf, args)
    if args.selftest_live:
        print("[自检] 实时通道单独放 %d 声短音" % args.selftest_live)
        await stream_session(loop, conf, args, stop_event, synthetic_beeps=args.selftest_live)
        print("[自检结束]")
        return 0

    session_attempts = 0
    while not stop_event.is_set():
        try:
            if status_callback is not None and _accepts_kwarg(run_stream, "status_callback"):
                reason = await run_stream(
                    loop, conf, args, stop_event, status_callback=status_callback
                )
            else:
                reason = await run_stream(loop, conf, args, stop_event)
            session_attempts = 0
        except KeyboardInterrupt:
            break
        except Exception as exc:  # noqa: BLE001
            if stop_event.is_set():
                break
            session_attempts += 1
            if args.max_retries and session_attempts > args.max_retries:
                print("[错误] 连续失败 %d 次，停止：%r" % (session_attempts, exc))
                _emit_status(status_callback, "error", repr(exc))
                return 3
            _emit_status(status_callback, "retrying", conf.name)
            print("[重连] 会话中断（%r），%g 秒后重试…" % (exc, args.retry_delay))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=args.retry_delay)
            if stop_event.is_set():
                break
            conf = await find_device(loop, args.id, args.name) or conf
            continue

        if reason != "idle":
            break
        _emit_status(status_callback, "idle", conf.name)
        print("[待机] 安静了，先放开音箱")
        got_sound = await wait_for_sound(args, stop_event)
        if not got_sound or stop_event.is_set():
            break
        _emit_status(status_callback, "connecting", conf.name)
        print("[唤醒] 检测到声音，接回音箱…")
        conf = await find_device(loop, args.id, args.name) or conf

    print("[退出] 已停止推流")
    _emit_status(status_callback, "stopped", "")
    return 0


def write_tone_wav(path, seconds=4.0, freq=523.25, amplitude=0.25):
    """写一个干净的单音 WAV，给官方文件通道做对比用。"""
    return write_beep_wav(path, beeps=1, on=seconds, off=0.0, freq=freq,
                          amplitude=amplitude)


def write_beep_wav(path, beeps=3, on=0.45, off=0.45, freq=880.0, amplitude=0.3):
    """写一个干净的单音 WAV，给官方文件通道做对比用。"""
    import wave

    with wave.open(path, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        on_samples = int(on * SAMPLE_RATE)
        period = int((on + off) * SAMPLE_RATE)
        total = period * beeps
        idx = np.arange(total, dtype=np.float64)
        phase = idx % period
        tone = amplitude * np.sin(2 * np.pi * freq * idx / SAMPLE_RATE)
        edge = np.minimum(phase, period - phase) / (SAMPLE_RATE * 0.01)
        data = np.where(phase < on_samples, tone * np.clip(edge, 0.0, 1.0), 0.0)
        out.writeframes(np.repeat((data * 32767).astype("<i2"), 2).tobytes())
    return path


async def selftest(loop, conf, args):
    """通过文件通道和实时通道分别播放测试音，用来判断哪条通道能把声音正确还原。"""
    path = os.path.join(str(ensure_writable_data_dir()), "selftest-tone.wav")
    write_tone_wav(path, seconds=1.2, freq=880.0, amplitude=0.3)
    stop_event = asyncio.Event()

    print("[自检 1/2] pyatv 官方文件通道：一声长音（嘟——）")
    atv = await pyatv.connect(conf, loop)
    try:
        await atv.stream.stream_file(path)
    finally:
        atv.close()
    await asyncio.sleep(2.5)

    print("[自检 2/2] 实时通道：两声（嘟-嘟）")
    await stream_session(loop, conf, args, stop_event, synthetic_beeps=2)
    print("[自检结束]")
    return 0


def list_devices():
    loop = asyncio.new_event_loop()
    try:
        found = loop.run_until_complete(pyatv.scan(loop, timeout=6))
    finally:
        loop.close()
    if not found:
        print("局域网里没扫到设备")
        return 1
    for dev in found:
        print("%-24s %-16s %s" % (dev.name, dev.address, dev.identifier))
    return 0


def cli():
    parser = argparse.ArgumentParser(
        description="把电脑整机声音实时推到 AirPlay 音箱（HomePod / HomePod mini）"
    )
    parser.add_argument("--id", default=None, help="设备标识，优先于名称")
    parser.add_argument("--name", default=None, help="按设备名的一部分匹配，例如 客厅音箱")
    parser.add_argument("--list", action="store_true", help="列出局域网里的 AirPlay 设备")
    parser.add_argument("--volume", type=float, default=None, help="音量百分比 0-100（可选）")
    parser.add_argument("--latency", type=float, default=0.3,
                        help="播放延迟秒数（默认 0.3，最小 0.25，卡顿就调大）")
    parser.add_argument("--prebuffer", type=float, default=0.12, help="启动前缓冲秒数")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="本机设备配置文件")
    parser.add_argument("--engine", choices=("pipe", "direct"), default="direct",
                        help="direct=实时通道（默认），pipe=伪装成 WAV 流（实验性）")
    parser.add_argument("--idle-timeout", type=float, default=60.0,
                        help="安静多少秒后释放音箱（0 = 不释放）")
    parser.add_argument("--silence-threshold", type=float, default=-55.0,
                        help="判定出声的音量门限 dBFS")
    parser.add_argument("--report", type=float, default=60.0,
                        help="每隔多少秒打印一次状态（0 = 不打印）")
    parser.add_argument("--seconds", type=float, default=0,
                        help="只推这么多秒后停止（0 = 一直推，用来测试）")
    parser.add_argument("--retry-delay", type=float, default=3.0, help="重连间隔秒数")
    parser.add_argument("--max-retries", type=int, default=0, help="最多连续失败几次（0 = 不限）")
    parser.add_argument("--debug", action="store_true", help="把详细日志写到脚本目录")
    parser.add_argument("--dump", default="", help="把推给音箱的原始音频存成 WAV（排查用）")
    parser.add_argument("--selftest", action="store_true",
                        help="自检：依次用官方通道和实时通道各放一段单音，用来排查声音异常")
    parser.add_argument("--selftest-live", type=int, default=0,
                        help="只走实时通道放 N 声短音（排查用）")
    args = parser.parse_args()
    configure_device(args, parser)

    if args.list:
        return list_devices()

    lock = acquire_single_instance_lock()
    if lock is None:
        print("已经有一个推流在跑了（不用再开一个）。")
        print("想停掉它，去把那个窗口关掉，或者按 Ctrl+C。")
        time.sleep(3)
        return 0
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(os.path.abspath(PID_FILE)), exist_ok=True)
        with open(PID_FILE, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            filename=os.path.join(str(ensure_writable_data_dir()), "airplay-debug.log"),
            filemode="w",
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[退出] 已停止推流")
        return 0
    finally:
        with contextlib.suppress(OSError):
            os.remove(PID_FILE)


if __name__ == "__main__":
    raise SystemExit(cli())
