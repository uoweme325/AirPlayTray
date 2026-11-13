import asyncio
import hashlib
import importlib.metadata as md
from ipaddress import IPv4Address
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from pyatv.conf import AppleTV, ManualService
from pyatv.const import DeviceModel, Protocol
from pyatv.interface import DeviceInfo
from pyatv.protocols.raop import RaopStream, audio_source

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
sys.path.insert(0, str(SOURCE))

import airplay_system_audio as tool  # noqa: E402
import app_paths  # noqa: E402
from build_tools.collect_licenses import collect_licenses  # noqa: E402
import settings  # noqa: E402
import tray_app as tray  # noqa: E402


def make_device(identifier, name, model=DeviceModel.HomePodMini, raw_model="AudioAccessory5,1"):
    devinfo = {DeviceInfo.MODEL: model}
    if raw_model:
        devinfo[DeviceInfo.RAW_MODEL] = raw_model
    conf = AppleTV(IPv4Address("192.0.2.10"), name, device_info=DeviceInfo(devinfo))
    conf.add_service(ManualService(identifier, Protocol.RAOP, 7000, {}))
    return conf


class AppPathsTests(unittest.TestCase):
    def test_source_mode_paths(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertFalse(app_paths.is_frozen())
            self.assertEqual(app_paths.resource_dir(), SOURCE)
            self.assertEqual(app_paths.data_dir(), SOURCE)
            self.assertEqual(app_paths.default_config_path(), SOURCE / "config.json")
            self.assertTrue(app_paths.asset_path("app.ico").exists())

    def test_frozen_mode_uses_data_dir_beside_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "AirPlayTray.exe"
            internal = root / "_internal"
            ico = internal / "assets" / "app.ico"
            ico.parent.mkdir(parents=True, exist_ok=True)
            ico.write_bytes(b"ico")
            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "executable", str(exe)), \
                 patch.object(sys, "_MEIPASS", str(internal), create=True):
                self.assertTrue(app_paths.is_frozen())
                self.assertEqual(app_paths.resource_dir(), internal.resolve())
                self.assertEqual(app_paths.data_dir(), root.resolve() / "data")
                self.assertEqual(app_paths.default_config_path(), root.resolve() / "data" / "config.json")
                self.assertEqual(app_paths.log_path(), root.resolve() / "data" / "tray.log")
                self.assertEqual(app_paths.asset_path("app.ico"), ico.resolve())


class SettingsV02Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "config.json"

    def test_tray_allows_missing_or_corrupt_config_without_overwriting(self):
        args_missing = tray.build_args(["--config", str(self.cfg)], allow_missing=True)
        self.assertIsNone(args_missing.id)
        self.assertIsNone(args_missing.name)
        self.assertEqual(args_missing.config_error, "missing")
        self.assertFalse(self.cfg.exists())

        self.cfg.write_text("{corrupt", encoding="utf-8")
        args_corrupt = tray.build_args(["--config", str(self.cfg)], allow_missing=True)
        self.assertIsNone(args_corrupt.id)
        self.assertIsNone(args_corrupt.name)
        self.assertIsNotNone(args_corrupt.config_error)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), "{corrupt")

    def test_save_device_selection_preserves_extra_fields_and_backs_up_corrupt(self):
        self.cfg.write_text(json.dumps({"device_name": "Old", "custom_note": "keep"}), encoding="utf-8")
        settings.save_device_selection(self.cfg, "AA:BB:CC:DD:EE:11", "Bedroom HomePod")
        saved = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(saved["device_id"], "AA:BB:CC:DD:EE:11")
        self.assertEqual(saved["device_name"], "Bedroom HomePod")
        self.assertEqual(saved["custom_note"], "keep")

        self.cfg.write_text("{broken", encoding="utf-8")
        settings.save_device_selection(self.cfg, "AA:BB:CC:DD:EE:22", "Desk Mini")
        self.assertEqual(Path(str(self.cfg) + ".bak").read_text(encoding="utf-8"), "{broken")
        saved2 = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(saved2["device_id"], "AA:BB:CC:DD:EE:22")


class HomePodSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "config.json"

    def test_is_homepod_device_never_guesses_by_name(self):
        self.assertTrue(tool.is_homepod_device(make_device("1", "Living Room", DeviceModel.HomePod, "AudioAccessory1,1")))
        self.assertTrue(tool.is_homepod_device(make_device("2", "Mini", DeviceModel.HomePodMini, "AudioAccessory5,1")))
        self.assertTrue(tool.is_homepod_device(make_device("3", "Gen2", DeviceModel.HomePodGen2, "AudioAccessory6,1")))
        self.assertTrue(tool.is_homepod_device(make_device("4", "Future", DeviceModel.Unknown, "AudioAccessory7,1")))
        self.assertFalse(tool.is_homepod_device(make_device("5", "HomePod TV Fake", DeviceModel.Gen4K, "AppleTV11,1")))
        self.assertFalse(tool.is_homepod_device(make_device("6", "HomePod", DeviceModel.Unknown, "Mac14,2")))

    def test_pick_auto_homepod_rules(self):
        hp1 = tray.SpeakerInfo.from_device(make_device("AA:BB:CC:DD:EE:01", "Study Mini"))
        hp2 = tray.SpeakerInfo.from_device(make_device("AA:BB:CC:DD:EE:02", "Living Room", DeviceModel.HomePod, "AudioAccessory1,1"))
        tv = tray.SpeakerInfo.from_device(make_device("AA:BB:CC:DD:EE:03", "HomePod-like TV", DeviceModel.Gen4K, "AppleTV11,1"))

        args = tray.build_args(["--config", str(self.cfg)], allow_missing=True)
        self.assertEqual(tray.pick_auto_homepod([hp1, tv], str(self.cfg), args), hp1)
        self.assertIsNone(tray.pick_auto_homepod([hp1, hp2], str(self.cfg), args))
        self.assertIsNone(tray.pick_auto_homepod([tv], str(self.cfg), args))

        # Existing config must prevent auto-switching even when saved device is offline
        self.cfg.write_text(json.dumps({"device_name": "Offline Speaker"}), encoding="utf-8")
        args_existing = tray.build_args(["--config", str(self.cfg)], allow_missing=True)
        self.assertIsNone(tray.pick_auto_homepod([hp1], str(self.cfg), args_existing))

    def test_duplicate_speaker_names_get_short_id_suffix(self):
        d1 = tray.SpeakerInfo("AA:BB:CC:DD:EE:01", "Living Room", "192.0.2.1", ("AA:BB:CC:DD:EE:01",), True)
        d2 = tray.SpeakerInfo("AA:BB:CC:DD:EE:02", "Living Room", "192.0.2.2", ("AA:BB:CC:DD:EE:02",), True)
        d3 = tray.SpeakerInfo("AA:BB:CC:DD:EE:03", "Desk", "192.0.2.3", ("AA:BB:CC:DD:EE:03",), True)
        labels = dict((dev.id, lbl) for dev, lbl in tray.format_speaker_labels([d1, d2, d3]))
        self.assertEqual(labels[d1.id], "Living Room (DD:EE:01)")
        self.assertEqual(labels[d2.id], "Living Room (DD:EE:02)")
        self.assertEqual(labels[d3.id], "Desk")


class StatusAndControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_audio_on_active_triggers_only_on_first_read_while_open(self):
        fired = []
        source = tool.LiveSystemAudio(44100, 2, 2, on_active=lambda: fired.append("streaming"))
        pcm = np.ones(512 * 2, dtype="<i2").tobytes()
        source.feed(pcm, -10.0)
        self.assertEqual(fired, [])  # feeding alone must not claim streaming

        await source.readframes(352)
        await source.readframes(352)
        self.assertEqual(fired, ["streaming"])
        await source.close()

    async def test_closed_empty_audio_sources_never_trigger_streaming(self):
        fired = []
        src = tool.LiveSystemAudio(44100, 2, 2, on_active=lambda: fired.append("sys"))
        src.close_feed()
        self.assertEqual(await src.readframes(352), b"")
        self.assertEqual(fired, [])
        await src.close()

    async def test_pipe_open_source_does_not_emit_streaming_before_send_audio(self):
        events = []
        pipe = tool.LiveWavPipe(44100, 2, 2, prebuffer=0.01)
        pipe.feed(np.ones(2048 * 2, dtype="<i2").tobytes(), -12.0)
        pipe.close_feed()

        opened = await audio_source.open_source(pipe, 44100, 2, 2)
        try:
            await opened.get_metadata()
        finally:
            await opened.close()
            pipe.close()
        self.assertEqual(events, [])

        test_case = self

        class FailingClient:
            async def send_audio(self, src, metadata, **kwargs):
                raise RuntimeError("rtsp setup failed")

        class EmptyClient:
            async def send_audio(self, src, metadata, **kwargs):
                test_case.assertEqual(await src.readframes(352), b"")

        class FakeClient:
            async def send_audio(self, src, metadata, **kwargs):
                frame1 = await src.readframes(352)
                frame2 = await src.readframes(352)
                test_case.assertTrue(len(frame1) > 0 and len(frame2) > 0)

        clients = [FailingClient(), EmptyClient(), FakeClient()]

        class FakeManager:
            async def setup(self, service):
                return clients.pop(0), object()

        class FakeAudioSource:
            def __init__(self, frames):
                self._frames = list(frames)

            async def readframes(self, nframes):
                return self._frames.pop(0) if self._frames else b""

        fake_raop = MagicMock(spec=RaopStream)
        fake_raop.playback_manager = FakeManager()
        fake_atv = MagicMock()
        fake_atv.stream._interfaces = {Protocol.RAOP: fake_raop}  # noqa: SLF001

        tool._attach_pipe_active_hook(  # noqa: SLF001
            fake_atv, "Study Mini", lambda s, d="": events.append((s, d))
        )

        # 1. Setup alone or failure before readframes must not emit streaming
        client1, _ = await fake_raop.playback_manager.setup(None)
        self.assertEqual(events, [])
        with self.assertRaises(RuntimeError):
            await client1.send_audio(FakeAudioSource([b"\x01\x02"]), None)
        self.assertEqual(events, [])

        # 2. Empty readframes must not emit streaming
        client2, _ = await fake_raop.playback_manager.setup(None)
        await client2.send_audio(FakeAudioSource([]), None)
        self.assertEqual(events, [])

        # 3. Non-empty readframes emits streaming exactly once across reads
        client3, _ = await fake_raop.playback_manager.setup(None)
        await client3.send_audio(FakeAudioSource([b"\x01\x02" * 352, b"\x03\x04" * 352]), None)
        self.assertEqual(events, [("streaming", "Study Mini")])

    async def test_main_async_emits_connecting_retrying_idle_stopped(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Path(tmp.name) / "config.json"
        cfg.write_text(json.dumps({"device_name": "Target", "device_id": "ID-1"}), encoding="utf-8")
        args = tray.build_args(["--config", str(cfg), "--retry-delay", "0.001"])
        dev = make_device("ID-1", "Target")
        events = []
        scans = []

        async def fake_scan(loop, **kwargs):
            scans.append(1)
            return [] if len(scans) <= 3 else [dev]

        stream_runs = []

        async def fake_run_stream(loop, conf, a, stop_ev, status_callback=None):
            stream_runs.append(1)
            status_callback("connecting", conf.name)
            status_callback("streaming", conf.name)
            return "idle" if len(stream_runs) == 1 else "stopped"

        async def fake_wait_sound(a, stop_ev):
            return True

        with patch.object(tool.pyatv, "scan", fake_scan), \
             patch.object(tool, "run_stream", fake_run_stream), \
             patch.object(tool, "wait_for_sound", fake_wait_sound):
            rc = await tool.main_async(args, status_callback=lambda s, d="": events.append(s))

        self.assertEqual(rc, 0)
        self.assertIn("retrying", events)
        self.assertIn("streaming", events)
        self.assertIn("idle", events)
        self.assertEqual(events[-1], "stopped")

    def test_icon_states_and_caching(self):
        self.assertEqual(tray.icon_key_for_status("streaming", True), "blue")
        self.assertEqual(tray.icon_key_for_status("streaming", False), "blue")
        self.assertEqual(tray.icon_key_for_status("idle", False), "teal")
        self.assertEqual(tray.icon_key_for_status("connecting", True), "teal")
        self.assertEqual(tray.icon_key_for_status("connecting", False), "teal_dim")
        self.assertEqual(tray.icon_key_for_status("retrying", False), "teal_dim")
        self.assertEqual(tray.icon_key_for_status("stopping", True), "teal")
        self.assertEqual(tray.icon_key_for_status("stopping", False), "teal_dim")
        self.assertEqual(tray.icon_key_for_status("stopped", True), "grey")
        self.assertEqual(tray.icon_key_for_status("error", True), "grey")
        self.assertIs(tray.make_icon("blue"), tray.make_icon("blue"))
        self.assertIs(tray.make_icon("teal"), tray.make_icon("teal"))

    def test_stop_stays_stopping_until_worker_cleanup_finishes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Path(tmp.name) / "config.json"
        cfg.write_text(json.dumps({"device_name": "First", "device_id": "ID-1"}), encoding="utf-8")
        args = tray.build_args(["--config", str(cfg)])
        controller = tray.Controller(args, lang="en")

        started = threading.Event()
        stop_received = threading.Event()
        allow_cleanup_finish = threading.Event()

        async def slow_cleanup_main(run_args, stop_event, status_callback=None):
            status_callback("streaming", "First")
            started.set()
            await stop_event.wait()
            stop_received.set()
            # Late status callback during teardown must not revert stopping status
            status_callback("retrying", "First")
            while not allow_cleanup_finish.is_set():
                await asyncio.sleep(0.01)
            return 0

        with patch.object(tool, "acquire_single_instance_lock", side_effect=lambda: MagicMock()), \
             patch.object(tool, "main_async", slow_cleanup_main), \
             patch.object(tray, "log_path", return_value=Path(tmp.name) / "tray.log"), \
             patch.object(tray, "PID_PATH", str(Path(tmp.name) / "tray.pid")):
            self.assertTrue(controller.start())
            self.assertTrue(started.wait(2.0))
            self.assertEqual(controller.status, "streaming")

            self.assertTrue(controller.stop(wait_timeout=0.0))
            self.assertTrue(stop_received.wait(2.0))
            self.assertEqual(controller.status, "stopping")
            self.assertNotEqual(controller.icon_state(blink_on=False), "grey")
            self.assertIn("Stopping", controller.tooltip_title())
            self.assertTrue(controller.running)

            allow_cleanup_finish.set()
            controller.thread.join(timeout=2.0)

        self.assertFalse(controller.running)
        self.assertEqual(controller.status, "stopped")
        self.assertEqual(controller.icon_state(), "grey")

    def test_controller_generation_guard_and_serial_speaker_switch(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Path(tmp.name) / "config.json"
        cfg.write_text(json.dumps({"device_name": "First", "device_id": "ID-1"}), encoding="utf-8")
        args = tray.build_args(["--config", str(cfg)])
        controller = tray.Controller(args, lang="en")

        order = []
        first_started = threading.Event()
        second_started = threading.Event()

        async def fake_main_async(run_args, stop_event, status_callback=None):
            target = run_args.id
            order.append(("start", target))
            if target == "ID-1":
                status_callback("streaming", "First")
                first_started.set()
                await stop_event.wait()
                order.append(("end", target))
                status_callback("retrying", "First")
            else:
                status_callback("streaming", "Second")
                second_started.set()
                await stop_event.wait()
                order.append(("end", target))
            return 0

        new_dev = tray.SpeakerInfo("ID-2", "Second", "192.0.2.20", ("ID-2",), True)
        with patch.object(tool, "acquire_single_instance_lock", side_effect=lambda: MagicMock()), \
             patch.object(tool, "main_async", fake_main_async), \
             patch.object(tray, "log_path", return_value=Path(tmp.name) / "tray.log"), \
             patch.object(tray, "PID_PATH", str(Path(tmp.name) / "tray.pid")):
            self.assertTrue(controller.start())
            self.assertTrue(first_started.wait(2.0))
            self.assertEqual(controller.status, "streaming")

            self.assertTrue(controller.select_speaker_sync(new_dev))
            self.assertTrue(second_started.wait(2.0))
            self.assertEqual(controller.status, "streaming")
            self.assertFalse(controller.switching)
            controller.stop(wait_timeout=2.0)

        saved = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(saved["device_id"], "ID-2")
        self.assertTrue(order.index(("end", "ID-1")) < order.index(("start", "ID-2")))


class SelfCheckAndPackagingTests(unittest.TestCase):
    def test_windowed_stdio_fallback_handles_none_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None), \
                 patch.object(tray, "log_path", return_value=Path(tmp) / "tray.log"):
                tray.ensure_windowed_stdio()
                try:
                    self.assertIsNotNone(sys.stdout)
                    self.assertIsNotNone(sys.stderr)
                    print("hello windowed")
                finally:
                    if hasattr(sys.stdout, "close"):
                        sys.stdout.close()
                    if hasattr(sys.stderr, "close") and sys.stderr is not sys.stdout:
                        sys.stderr.close()

    def test_windowed_startup_reports_data_dir_collision_via_notify(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocking_file = root / "data"
            blocking_file.write_text("occupied", encoding="ascii")

            def blocked_mkdir():
                blocking_file.mkdir(parents=True, exist_ok=True)
                return blocking_file

            notices = []
            with patch.object(sys, "stdout", None), \
                 patch.object(sys, "stderr", None), \
                 patch.object(tray, "acquire_tray_mutex", return_value=object()), \
                 patch.object(tray, "ensure_writable_data_dir", side_effect=blocked_mkdir), \
                 patch.object(tray, "data_dir", return_value=blocking_file), \
                 patch.object(tray, "log_path", return_value=blocking_file / "tray.log"), \
                 patch.object(tray, "pid_path", return_value=blocking_file / "tray.pid"), \
                 patch.object(tray, "notify", side_effect=lambda msg, title="": notices.append((msg, title))):
                rc = tray.main(["--config", str(blocking_file / "config.json")])

            self.assertEqual(rc, 1)
            self.assertEqual(len(notices), 1)
            self.assertIn(str(blocking_file), notices[0][0])

    def test_self_check_offline_skips_mutex_and_network_and_fails_when_unwritable(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir(parents=True, exist_ok=True)
            with patch.object(tray, "acquire_tray_mutex") as mutex, \
                 patch.object(tool.pyatv, "scan") as scan, \
                 patch.object(tray, "ensure_writable_data_dir", return_value=data):
                rc = tray.main(["--self-check"])
            self.assertEqual(rc, 0)
            mutex.assert_not_called()
            scan.assert_not_called()

            blocking_file = Path(tmp) / "blocked-data"
            blocking_file.write_text("file", encoding="ascii")
            with patch.object(tray, "ensure_writable_data_dir", side_effect=FileExistsError("cannot create")):
                rc_fail = tray.main(["--self-check"])
            self.assertEqual(rc_fail, 1)

    def test_collect_licenses_preserves_numpy_root_and_subcomponent_licenses(self):
        numpy_dist = md.distribution("numpy")
        root_rel = Path("numpy-%s.dist-info/LICENSE.txt" % numpy_dist.version)
        sub_rel = Path("numpy/_core/include/numpy/random/LICENSE.txt")
        orig_root = Path(numpy_dist.locate_file(root_rel))
        orig_sub = Path(numpy_dist.locate_file(sub_rel))
        self.assertTrue(orig_root.is_file(), "expected NumPy dist-info root LICENSE.txt")
        self.assertTrue(orig_sub.is_file(), "expected NumPy random subcomponent LICENSE.txt")

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "licenses"
            count = collect_licenses(SOURCE / "requirements-lock.txt", out_dir)
            copied_files = [p for p in out_dir.rglob("*") if p.is_file()]
            self.assertEqual(count, len(copied_files))
            self.assertGreater(count, 40)

            np_dir = out_dir / ("numpy-%s" % numpy_dist.version)
            copied_root = np_dir / "LICENSE.txt"
            copied_sub = np_dir / "numpy" / "_core" / "include" / "numpy" / "random" / "LICENSE.txt"
            self.assertTrue(copied_root.is_file())
            self.assertTrue(copied_sub.is_file())
            self.assertEqual(
                hashlib.sha256(copied_root.read_bytes()).hexdigest(),
                hashlib.sha256(orig_root.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                hashlib.sha256(copied_sub.read_bytes()).hexdigest(),
                hashlib.sha256(orig_sub.read_bytes()).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
