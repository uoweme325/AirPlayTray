import asyncio
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airplay_system_audio as tool
import tray_app as tray
from settings import save_volume_selection


class VolumeSettingsTests(unittest.TestCase):
    def test_invalid_saved_volume_keeps_tray_alive_in_error_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config.json'
            for value in ('bad', -1, 101, True, float('nan'), float('inf'), 10**400):
                with self.subTest(value=value):
                    cfg.write_text(json.dumps({'device_id': 'TEST', 'volume': value}), encoding='utf-8')
                    original = cfg.read_bytes()
                    args = tray.build_args(['--config', str(cfg)], allow_missing=True)
                    self.assertIsNotNone(args.config_error)
                    self.assertIsNone(args.id)
                    self.assertIsNone(args.name)
                    self.assertIsNone(args.volume)
                    controller = tray.Controller(args)
                    self.assertEqual(controller.status, 'error')
                    self.assertFalse(controller.start())
                    self.assertEqual(cfg.read_bytes(), original)

    def test_volume_save_does_not_overwrite_corrupt_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            for content in ('{broken', '[1,2]'):
                path.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    save_volume_selection(path, 50)
                self.assertEqual(path.read_text(), content)

    def test_saved_volume_and_cli_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps({'device_id': 'TEST', 'volume': 25}), encoding='utf-8-sig')
            args = ['--config', str(path)]
            self.assertEqual(tray.build_args(args).volume, 25)
            self.assertEqual(tray.build_args(args + ['--name', 'Other']).volume, 25)
            self.assertEqual(tray.build_args(args + ['--volume', '0']).volume, 0)
            path.write_text(json.dumps({'device_id': 'TEST'}), encoding='utf-8')
            self.assertIsNone(tray.build_args(args).volume)

    def test_invalid_config_and_cli_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            for value in (-1, 101, float('nan'), float('inf'), True, '25'):
                path.write_text(json.dumps({'device_id': 'TEST', 'volume': value}), encoding='utf-8')
                with self.subTest(config=value), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    tray.build_args(['--config', str(path)])
            for value in ('-1', '101', 'nan', 'inf'):
                for allow_missing in (False, True):
                    with self.subTest(cli=value, tray=allow_missing), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        tray.build_args(['--name', 'TEST', '--volume', value], allow_missing=allow_missing)


class Client:
    """Only the network boundary is replaced; volume and persistence are real."""
    def __init__(self):
        self.info = {'initialVolume': -20.1}
        self.calls = []
        self.fail = False

    async def set_volume(self, value):
        if self.fail:
            raise OSError('network unavailable')
        self.calls.append(value)


class LiveVolumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / 'config.json'
        self.cfg.write_text(json.dumps({'device_id': 'TEST', 'custom': 'keep'}), encoding='utf-8')
        self.args = tray.build_args(['--config', str(self.cfg)])
        self.events = []
        self.volume = tool.LiveVolume(self.args, lambda value, applied: self.events.append((value, applied)))

    async def test_initial_volume_is_not_persisted_unless_user_selects(self):
        client = Client()
        await self.volume.attach(client)
        self.assertAlmostEqual(self.events[-1][0], 33)
        self.assertTrue(self.events[-1][1])
        self.assertIsNone(self.args.volume)
        self.assertNotIn('volume', json.loads(self.cfg.read_text()))
        self.assertEqual(client.calls, [-20.1])

    async def test_live_change_mute_and_restore_preserve_settings(self):
        client = Client()
        await self.volume.attach(client)
        for value, db in ((75, -7.5), (0, -144), (50, -15)):
            await self.volume.change(value)
            self.assertEqual(client.calls[-1], db)
            self.assertEqual(self.events[-1], (value, True))
        self.assertIs(self.volume.client, client)
        saved = json.loads(self.cfg.read_text())
        self.assertEqual(saved, {'device_id': 'TEST', 'custom': 'keep', 'volume': 50})
        self.assertEqual(tray.build_args(['--config', str(self.cfg)]).volume, 50)

    async def test_standby_selection_is_saved_and_applied_to_next_session(self):
        first, second = Client(), Client()
        await self.volume.attach(first)
        await self.volume.detach()
        await self.volume.change(25)
        self.assertEqual(first.calls, [-20.1])
        self.assertEqual(self.events[-1], (25, False))
        await self.volume.attach(second)
        self.assertEqual(second.calls, [-22.5])
        self.assertEqual(self.events[-1], (25, True))

    async def test_network_failure_does_not_change_selection_or_saved_config(self):
        client = Client()
        await self.volume.attach(client)
        before = self.cfg.read_bytes()
        events = list(self.events)
        client.fail = True
        with self.assertRaises(OSError):
            await self.volume.change(75)
        self.assertEqual(self.cfg.read_bytes(), before)
        self.assertIsNone(self.args.volume)
        self.assertEqual(self.events, events)

    async def test_save_failure_does_not_hide_successful_network_application(self):
        client = Client()
        await self.volume.attach(client)
        with patch.object(tool, 'save_volume_selection', side_effect=PermissionError('read only')):
            with self.assertRaises(PermissionError):
                await self.volume.change(50)
        self.assertEqual(client.calls[-1], -15)
        self.assertEqual(self.events[-1], (50, True))
        self.assertNotIn('volume', json.loads(self.cfg.read_text()))

    async def test_changes_and_detach_are_serialized(self):
        client = Client()
        await self.volume.attach(client)
        entered, release = asyncio.Event(), asyncio.Event()
        original = client.set_volume

        async def slow_set(value):
            entered.set()
            await release.wait()
            await original(value)

        client.set_volume = slow_set
        change = asyncio.create_task(self.volume.change(50))
        await entered.wait()
        detach = asyncio.create_task(self.volume.detach())
        await asyncio.sleep(0)
        self.assertFalse(detach.done())
        release.set()
        await asyncio.gather(change, detach)
        self.assertIsNone(self.volume.client)
        self.assertEqual(self.events[-1], (50, False))


class ControllerVolumeTests(unittest.TestCase):
    def test_tray_startup_constructs_bilingual_volume_menu_without_audio(self):
        for lang, label, invalid in (('zh', '音量', False), ('en', 'Volume', False), ('zh', '音量', True), ('en', 'Volume', True)):
            with self.subTest(lang=lang, invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                cfg = folder / 'config.json'
                cfg.write_text(json.dumps({'device_id': 'TEST', 'volume': 'bad' if invalid else None}), encoding='utf-8')
                captured = []
                streams = []

                class TrayIcon:
                    HAS_NOTIFICATION = False

                    def __init__(self, name, image, title, menu):
                        self.menu = menu

                    def run(self):
                        streams.append(sys.stdout)
                        items = list(self.menu)
                        volume = items[2]
                        captured.append(volume.text)
                        captured.extend(i.text for i in volume.submenu)
                        items[-1](self)

                    def stop(self):
                        pass

                with patch.object(tray, 'detect_ui_language', return_value=lang), patch.object(tray, 'acquire_tray_mutex', return_value=object()), patch.object(tray, 'ensure_writable_data_dir', return_value=folder), patch.object(tray, 'log_path', return_value=folder / 'tray.log'), patch.object(tray, 'pid_path', return_value=folder / 'pid'), patch.object(tray, 'log'), patch.object(tray.Controller, 'start', return_value=False) as start, patch.object(tray.Controller, 'refresh_devices_async'), patch.object(tray.pystray, 'Icon', TrayIcon), patch.object(sys, 'stdout', io.StringIO()), patch.object(sys, 'stderr', io.StringIO()):
                    original_stdout = sys.stdout
                    self.assertEqual(tray.main(['--config', str(cfg)]), 0)
                    self.assertIs(sys.stdout, original_stdout)
                    if invalid:
                        start.assert_not_called()
                self.assertEqual(captured[0], label)
                self.assertIn('0%', captured)
                self.assertIn('100%', captured)
                self.assertTrue(streams[0]._handle.closed)

    def test_stop_cancels_inflight_volume_without_holding_session_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config.json'
            cfg.write_text('{"device_id":"TEST"}', encoding='utf-8')
            controller = tray.Controller(tray.build_args(['--config', str(cfg)]))
            errors = []
            controller.on_volume_error = errors.append
            ready, changing, finished = threading.Event(), threading.Event(), threading.Event()
            client = Client()

            async def slow_set(value):
                changing.set()
                await asyncio.Event().wait()

            async def network_session(args, stop, status_callback=None):
                await args.volume_control.attach(client)
                client.set_volume = slow_set
                ready.set()
                try:
                    await stop.wait()
                finally:
                    await args.volume_control.detach()
                return 0

            controller._on_ui_change = lambda menu=False: finished.set() if not controller.volume_busy else None
            with patch.object(tool, 'main_async', network_session), patch.object(tool, 'acquire_single_instance_lock', return_value=MagicMock()), patch.object(tray, 'PID_PATH', str(Path(tmp) / 'pid')), patch.object(tray, 'log') as logged:
                self.assertTrue(controller.start())
                try:
                    self.assertTrue(ready.wait(2))
                    finished.clear()
                    controller.request_volume(value=75)
                    self.assertTrue(changing.wait(2))
                    self.assertTrue(controller.stop(wait_timeout=2))
                    self.assertTrue(finished.wait(2))
                    self.assertFalse(controller.volume_busy)
                    self.assertFalse(controller.running)
                    self.assertNotIn('volume', json.loads(cfg.read_text()))
                    self.assertEqual(errors, [])
                    self.assertTrue(any('已取消' in str(call) for call in logged.call_args_list))
                    self.assertFalse(any('音量操作失败' in str(call) for call in logged.call_args_list))
                finally:
                    controller.stop(wait_timeout=2)

    def test_live_request_stays_in_same_session_and_stopped_selection_is_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config.json'
            cfg.write_text('{"device_id":"TEST"}', encoding='utf-8')
            controller = tray.Controller(tray.build_args(['--config', str(cfg)]), lang='en')
            started, changed = threading.Event(), threading.Event()
            client = Client()
            runs = []

            async def network_session(args, stop, status_callback=None):
                runs.append(1)
                await args.volume_control.attach(client)
                status_callback('streaming', 'TEST')
                started.set()
                try:
                    await stop.wait()
                finally:
                    await args.volume_control.detach()
                return 0

            def notify(menu=False):
                if not controller.volume_busy:
                    changed.set()

            controller._on_ui_change = notify
            with patch.object(tool, 'main_async', network_session), patch.object(tool, 'acquire_single_instance_lock', return_value=MagicMock()), patch.object(tray, 'PID_PATH', str(Path(tmp) / 'pid')), patch.object(tray, 'log'):
                self.assertTrue(controller.start())
                try:
                    self.assertTrue(started.wait(2))
                    thread = controller.thread
                    changed.clear()
                    self.assertTrue(controller.request_volume(value=50))
                    self.assertTrue(changed.wait(2))
                    self.assertFalse(controller.volume_busy)
                    self.assertEqual(controller.volume_value, 50)
                    self.assertIn('Current: 50%', controller.volume_text())
                    self.assertIs(controller.thread, thread)
                    self.assertEqual(len(runs), 1)
                    self.assertEqual(client.calls[-1], -15)
                    changed.clear()
                    controller.request_volume(delta=5)
                    self.assertTrue(changed.wait(2))
                    self.assertEqual(controller.volume_value, 55)
                finally:
                    controller.stop(wait_timeout=3)
                changed.clear()
                self.assertTrue(controller.request_volume(value=0))
                self.assertTrue(changed.wait(2))
                self.assertEqual(controller.volume_value, 0)
                self.assertFalse(controller.volume_applied)
                self.assertIn('next connection', controller.volume_text())

    def test_pipe_menu_requests_do_not_modify_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config.json'
            controller = tray.Controller(tray.build_args(['--name', 'TEST', '--config', str(cfg), '--engine', 'pipe']))
            self.assertFalse(controller.request_volume(value=50))
            self.assertFalse(cfg.exists())

    def test_empty_exception_message_includes_exception_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config.json'
            controller = tray.Controller(tray.build_args(['--name', 'TEST', '--config', str(cfg)]), lang='en')
            errors, finished = [], threading.Event()
            controller.on_volume_error = errors.append
            controller._on_ui_change = lambda menu=False: finished.set() if not controller.volume_busy else None
            with patch.object(tray, 'save_volume_selection', side_effect=RuntimeError()), patch.object(tray, 'log'):
                controller.request_volume(value=50)
                self.assertTrue(finished.wait(2))
            self.assertEqual(len(errors), 1)
            self.assertIn('RuntimeError', errors[0])

    def test_fractional_initial_volume_is_rounded_only_for_display(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = tray.Controller(tray.build_args(['--name', 'TEST', '--config', str(Path(tmp) / 'config.json')]), lang='en')
            value = tool.dbfs_to_pct(-18.5)
            controller.volume_value = value
            controller.volume_applied = True
            self.assertEqual(controller.volume_text(), 'Current: 38.3%')
            self.assertEqual(controller.volume_value, value)


if __name__ == '__main__':
    unittest.main()
