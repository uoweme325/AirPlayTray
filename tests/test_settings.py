import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airplay_system_audio as tool
import tray_app as tray


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'config.json'

    def config(self, value):
        self.path.write_text(json.dumps(value), encoding='utf-8-sig')
        return ['--config', str(self.path)]

    def assert_invalid(self, argv):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            tray.build_args(argv)
        self.assertEqual(error.exception.code, 2)

    def test_config_name(self):
        args = tray.build_args(self.config({'device_name': ' 客厅音箱 ', 'device_id': None}))
        self.assertEqual(args.name, '客厅音箱')
        self.assertIsNone(args.id)

    def test_config_identifier(self):
        args = tray.build_args(self.config({'device_id': 'AA:BB:CC:DD:EE:01'}))
        self.assertEqual(args.id, 'AA:BB:CC:DD:EE:01')

    def test_explicit_name_overrides_config_identifier(self):
        args = tray.build_args(self.config({'device_id': 'AA:BB:CC:DD:EE:01'}) + ['--name', 'Other'])
        self.assertEqual(args.name, 'Other')
        self.assertIsNone(args.id)

    def test_explicit_id_overrides_config_name(self):
        args = tray.build_args(self.config({'device_name': 'Old'}) + ['--id', 'AA:BB:CC:DD:EE:02'])
        self.assertEqual(args.id, 'AA:BB:CC:DD:EE:02')
        self.assertIsNone(args.name)

    def test_list_does_not_need_config_or_mutex(self):
        with patch.object(tool, 'list_devices', return_value=0) as listing, patch.object(tray, 'acquire_tray_mutex') as mutex:
            self.assertEqual(tray.main(['--config', str(self.path), '--list']), 0)
        listing.assert_called_once()
        mutex.assert_not_called()

    def test_missing_config_rejected(self):
        self.assert_invalid(['--config', str(self.path)])

    def test_empty_or_wrong_type_config_rejected(self):
        for config in ({}, [], {'device_name': '  '}, {'device_id': 123}, {'device_name': False}):
            with self.subTest(config=config):
                self.assert_invalid(self.config(config))

    def test_invalid_json_rejected(self):
        self.path.write_text('{invalid', encoding='utf-8')
        self.assert_invalid(['--config', str(self.path)])

    def test_legacy_zero_nonce_option_rejected(self):
        self.assert_invalid(['--name', 'Example', '--nonce', 'zero'])


if __name__ == '__main__':
    unittest.main()
