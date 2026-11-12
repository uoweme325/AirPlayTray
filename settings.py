"""本机设备配置；命令行指定的目标优先于配置文件。"""
import json
from pathlib import Path

DEFAULT_CONFIG = str(Path(__file__).resolve().with_name("config.json"))


def configure_device(args, parser):
    if args.list:
        return args
    if args.id is None and args.name is None:
        path = Path(args.config)
        try:
            settings = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            parser.error("请先复制 config.example.json 为 config.json 并选择音箱，或传入 --name / --id。")
        except (OSError, ValueError) as exc:
            parser.error("无法读取设备配置：%s" % exc)
        if not isinstance(settings, dict):
            parser.error("设备配置必须是 JSON 对象。")
        args.id = settings.get("device_id")
        args.name = settings.get("device_name")
    for field in ("id", "name"):
        value = getattr(args, field)
        if value is not None and not isinstance(value, str):
            parser.error("设备名称和标识必须是字符串或 null。")
        setattr(args, field, value.strip() if value else None)
    if not args.id and not args.name:
        parser.error("请在配置中填写 device_name 或 device_id，或传入 --name / --id。")
    return args
