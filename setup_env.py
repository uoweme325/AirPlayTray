"""Create a project-local Python environment and install pinned dependencies."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import venv


def main():
    if os.name != "nt" or struct.calcsize("P") != 8 or not (3, 9) <= sys.version_info[:2] <= (3, 12):
        print("Please use 64-bit Python 3.9 through 3.12 on Windows.")
        return 1
    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    venv.create(environment, with_pip=True)
    python = environment / "Scripts" / "python.exe"
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(root / "requirements-lock.txt")], check=True)
    config = root / "config.json"
    if not config.exists():
        shutil.copyfile(root / "config.example.json", config)
    print("Installed. Edit config.json to select your speaker, then run start.cmd.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as exc:
        print("Setup failed: %s" % exc, file=sys.stderr)
        raise SystemExit(1)
