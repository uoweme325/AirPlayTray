import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np

import airplay_system_audio as tool


def endpoint(id_, isloopback):
    return SimpleNamespace(id=id_, name="Audio device", isloopback=isloopback, recorder=Mock())


class LoopbackTests(unittest.TestCase):
    def setUp(self):
        self.render = endpoint("render-id", True)
        self.other_render = endpoint("other-render-id", True)
        self.microphone = endpoint("capture-id", False)
        self.backend = Mock()
        self.backend.default_speaker.return_value = SimpleNamespace(
            id=self.render.id, name=self.render.name
        )

    def test_matches_only_default_render_even_with_identical_names(self):
        for devices in ([self.render, self.other_render, self.microphone],
                        [self.microphone, self.other_render, self.render]):
            with self.subTest(devices=devices), patch.object(tool, "sc", self.backend):
                self.backend.all_microphones.return_value = devices
                self.assertIs(tool.loopback_device(), self.render)
        self.backend.all_microphones.assert_called_with(include_loopback=True)
        self.backend.get_microphone.assert_not_called()

    def test_reacquires_default_when_called_after_output_switch(self):
        self.backend.all_microphones.return_value = [self.render, self.other_render]
        with patch.object(tool, "sc", self.backend):
            self.assertIs(tool.loopback_device(), self.render)
            self.backend.default_speaker.return_value.id = self.other_render.id
            self.assertIs(tool.loopback_device(), self.other_render)

    def test_capture_fails_before_opening_an_unverified_endpoint(self):
        wrong_type = endpoint(self.render.id, False)
        duplicate = endpoint(self.render.id, True)
        for devices in ([], [self.microphone, self.other_render], [wrong_type],
                        [self.render, duplicate]):
            with self.subTest(devices=devices), patch.object(tool, "sc", self.backend):
                self.backend.all_microphones.return_value = devices
                with self.assertRaisesRegex(RuntimeError, "环回输入"):
                    tool.start_capture(Mock(), Mock())
                for device in devices:
                    device.recorder.assert_not_called()

    def test_no_default_does_not_try_to_enumerate_inputs(self):
        self.backend.default_speaker.return_value = None
        with patch.object(tool, "sc", self.backend):
            with self.assertRaisesRegex(RuntimeError, "默认播放设备"):
                tool.loopback_device()
        self.backend.all_microphones.assert_not_called()


class StandbyLoopbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_standby_opens_only_the_verified_render_recorder(self):
        render = endpoint("render-id", True)
        microphone = endpoint("capture-id", False)
        recorder = MagicMock()
        recorder.__enter__.return_value.record.return_value = np.ones((1, 2))
        render.recorder.return_value = recorder
        backend = Mock()
        backend.default_speaker.return_value = render
        backend.all_microphones.return_value = [render, microphone]

        args = SimpleNamespace(silence_threshold=-55, report=0)
        stop = asyncio.Event()
        deadline = asyncio.get_running_loop().call_later(2, stop.set)
        try:
            with patch.object(tool, "sc", backend):
                self.assertTrue(await tool.wait_for_sound(args, stop))
        finally:
            deadline.cancel()
        render.recorder.assert_called_once()
        microphone.recorder.assert_not_called()
