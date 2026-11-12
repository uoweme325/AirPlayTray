import asyncio
import contextlib
import io
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from ipaddress import IPv4Address

import numpy as np
from pyatv.conf import AppleTV, ManualService
from pyatv.const import Protocol

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
sys.path.insert(0, str(SOURCE))
import airplay_system_audio as tool
import tray_app as tray


def device(identifier, name):
    config = AppleTV(IPv4Address('192.0.2.1'), name)
    config.add_service(ManualService(identifier, Protocol.RAOP, 7000, {}))
    return config


class AudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_source_drains_exact_samples_and_ends(self):
        for count in (0, 1, 352, 512, 10000):
            with self.subTest(frames=count):
                source = tool.LiveSystemAudio(44100, 2, 2)
                pcm = np.arange(count * 2, dtype='<i2').tobytes()
                source.feed(pcm, -12)
                source.close_feed()
                blocks = []
                for _ in range(40):
                    block = await source.readframes(352)
                    if not block:
                        break
                    blocks.append(block)
                else:
                    self.fail('closed source did not reach EOF')
                self.assertEqual(b''.join(blocks), tool.to_big_endian(pcm))
                self.assertEqual(await source.readframes(352), b'')
                await source.close()

    async def test_open_underflow_still_supplies_packet(self):
        source = tool.LiveSystemAudio(44100, 2, 2)
        source._started = True
        self.assertEqual(len(await source.readframes(352)), 352 * 4)
        self.assertEqual(source.underruns, 1)
        await source.close()

    async def test_pcm_endianness(self):
        self.assertEqual(tool.to_big_endian(bytes.fromhex('0100ffff000100ff')), bytes.fromhex('0001ffff0100ff00'))


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.args = tray.build_args(['--id', 'AA:BB:CC:DD:EE:01', '--retry-delay', '0.001'])
        self.target = device('AA:BB:CC:DD:EE:01', 'Default speaker')

    async def test_initial_offline_then_recovers_and_streams(self):
        calls = []
        scans = []

        async def scan(loop, **kwargs):
            scans.append(kwargs)
            return [] if len(scans) <= 3 else [self.target]

        async def stream(loop, conf, args, stop):
            calls.append(conf)
            return 'stopped'

        with patch.object(tool.pyatv, 'scan', scan), patch.object(tool, 'run_stream', stream):
            self.assertEqual(await tool.main_async(self.args), 0)
        self.assertEqual(calls, [self.target])
        self.assertEqual(len(scans), 4)

    async def test_stop_during_discovery_cancels_scan(self):
        stop = asyncio.Event()
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def scan(loop, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(tool.pyatv, 'scan', scan):
            task = asyncio.create_task(tool.main_async(self.args, stop))
            await asyncio.wait_for(entered.wait(), 1)
            stop.set()
            self.assertEqual(await asyncio.wait_for(task, 1), 0)
        self.assertTrue(cancelled.is_set())

    async def test_stop_during_retry_delay_is_prompt(self):
        self.args.retry_delay = 60
        stop = asyncio.Event()
        scanned = asyncio.Event()

        async def scan(loop, **kwargs):
            scanned.set()
            return []

        with patch.object(tool.pyatv, 'scan', scan):
            task = asyncio.create_task(tool.main_async(self.args, stop))
            await scanned.wait()
            await asyncio.sleep(0.01)
            stop.set()
            self.assertEqual(await asyncio.wait_for(task, 1), 0)

    async def test_retry_limit(self):
        self.args.max_retries = 1
        calls = []

        async def scan(loop, **kwargs):
            calls.append(kwargs)
            return []

        with patch.object(tool.pyatv, 'scan', scan):
            self.assertEqual(await tool.main_async(self.args), 2)
        self.assertEqual(len(calls), 6)

    async def test_scan_error_then_recovery(self):
        attempts = []

        async def scan(loop, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError('network unavailable')
            return [self.target]

        with patch.object(tool.pyatv, 'scan', scan):
            self.assertIs(await tool.wait_for_device(asyncio.get_running_loop(), self.args, asyncio.Event()), self.target)

    async def test_name_selects_requested_speaker_and_explicit_id_wins(self):
        other = device('AA:BB:CC:DD:EE:FF', 'Other speaker')

        async def scan(loop, identifier=None, **kwargs):
            return [d for d in (self.target, other) if identifier is None or identifier in d.all_identifiers]

        with patch.object(tool.pyatv, 'scan', scan):
            args = tray.build_args(['--name', 'Other speaker'])
            self.assertIsNone(args.id)
            self.assertIs(await tool.find_device(asyncio.get_running_loop(), args.id, args.name), other)
            args = tray.build_args(['--id', 'AA:BB:CC:DD:EE:01', '--name', 'Other speaker'])
            self.assertIs(await tool.find_device(asyncio.get_running_loop(), args.id, args.name), self.target)
        self.assertEqual(tray.build_args(['--id', 'AA:BB:CC:DD:EE:01']).id, 'AA:BB:CC:DD:EE:01')


class EntrypointTests(unittest.TestCase):
    def test_cli_argument_priority_without_starting_audio(self):
        for argv, expected in ((['--id', 'AA:BB:CC:DD:EE:01'], 'AA:BB:CC:DD:EE:01'), (['--name', 'Other speaker'], None), (['--id', 'explicit', '--name', 'Other'], 'explicit')):
            captured = []

            async def main(args):
                captured.append(args)
                return 0

            with patch.object(sys, 'argv', ['airplay_system_audio.py'] + argv), patch.object(tool, 'acquire_single_instance_lock', return_value=True), patch.object(tool, 'PID_FILE', str(HERE / 'cli-test.pid')), patch.object(tool, 'main_async', main):
                self.assertEqual(tool.cli(), 0)
            self.assertEqual(captured[0].id, expected)

    def test_stop_script_scoping_and_failures_without_stopping_processes(self):
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            shutil.copy2(SOURCE / 'stop_background.ps1', Path(directory) / 'stop_background.ps1')
            for mode in ('success', 'enumeration_failure', 'stop_failure'):
                result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(HERE / 'test_stop.ps1'), '-SourceDirectory', directory, '-Mode', mode], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
