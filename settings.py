"""本机设备配置；命令行指定的目标优先于配置文件。"""
import json
import math
import os
import shutil
import tempfile
from pathlib import Path

from app_paths import default_config_path

DEFAULT_CONFIG = str(default_config_path())


def volume_percent(value):
    """Validate percentages once at config/CLI boundaries."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("音量必须是 0–100 的有限数字。")
    return float(value)


def configure_device(args, parser, allow_missing=False):
    args.config_error = None
    if getattr(args, "list", False) or getattr(args, "self_check", False):
        return args
    if args.volume is not None:
        try:
            args.volume = volume_percent(args.volume)
        except ValueError as exc:
            parser.error(str(exc))
    needs_target = args.id is None and args.name is None
    if needs_target or args.volume is None:
        path = Path(args.config)
        try:
            settings = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            if not needs_target:
                settings = {}
            elif allow_missing:
                args.config_error = "missing"
                return args
            else:
                parser.error("请先复制 config.example.json 为 config.json 并选择音箱，或传入 --name / --id。")
        except (OSError, ValueError) as exc:
            if not needs_target:
                settings = {}
            elif allow_missing:
                args.config_error = "无法读取设备配置：%s" % exc
                return args
            else:
                parser.error("无法读取设备配置：%s" % exc)
        if not isinstance(settings, dict):
            if not needs_target:
                settings = {}
            elif allow_missing:
                args.config_error = "设备配置必须是 JSON 对象。"
                return args
            else:
                parser.error("设备配置必须是 JSON 对象。")
        if needs_target:
            args.id = settings.get("device_id")
            args.name = settings.get("device_name")
        if args.volume is None and settings.get("volume") is not None:
            try:
                args.volume = volume_percent(settings["volume"])
            except ValueError as exc:
                parser.error(str(exc))
    for field in ("id", "name"):
        value = getattr(args, field)
        if value is not None and not isinstance(value, str):
            if allow_missing:
                args.config_error = "设备名称和标识必须是字符串或 null。"
                args.id = None
                args.name = None
                return args
            parser.error("设备名称和标识必须是字符串或 null。")
        setattr(args, field, value.strip() if value else None)
    if not args.id and not args.name:
        if allow_missing:
            return args
        parser.error("请在配置中填写 device_name 或 device_id，或传入 --name / --id。")
    return args


def save_device_selection(path, device_id, device_name):
    """Persist selected speaker into JSON config, preserving extra fields."""
    _save_settings(path, {"device_name": device_name or "", "device_id": device_id or None}, recover=True)


def save_volume_selection(path, volume):
    _save_settings(path, {"volume": volume})


def _save_settings(path, updates, recover=False):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data = loaded
            else:
                raise ValueError("配置文件必须是 JSON 对象。")
        except (OSError, ValueError):
            if not recover:
                raise
            shutil.copy2(target, target.with_name(target.name + ".bak"))
    data.update(updates)

    fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise

