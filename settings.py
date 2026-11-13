"""本机设备配置；命令行指定的目标优先于配置文件。"""
import json
import os
import shutil
import tempfile
from pathlib import Path

from app_paths import default_config_path

DEFAULT_CONFIG = str(default_config_path())


def configure_device(args, parser, allow_missing=False):
    args.config_error = None
    if getattr(args, "list", False) or getattr(args, "self_check", False):
        return args
    if args.id is None and args.name is None:
        path = Path(args.config)
        try:
            settings = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            if allow_missing:
                args.config_error = "missing"
                return args
            parser.error("请先复制 config.example.json 为 config.json 并选择音箱，或传入 --name / --id。")
        except (OSError, ValueError) as exc:
            if allow_missing:
                args.config_error = "无法读取设备配置：%s" % exc
                return args
            parser.error("无法读取设备配置：%s" % exc)
        if not isinstance(settings, dict):
            if allow_missing:
                args.config_error = "设备配置必须是 JSON 对象。"
                return args
            parser.error("设备配置必须是 JSON 对象。")
        args.id = settings.get("device_id")
        args.name = settings.get("device_name")
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
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data = loaded
            else:
                shutil.copy2(target, target.with_name(target.name + ".bak"))
        except (OSError, ValueError):
            shutil.copy2(target, target.with_name(target.name + ".bak"))
    data["device_name"] = device_name or ""
    data["device_id"] = device_id or None

    fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise

