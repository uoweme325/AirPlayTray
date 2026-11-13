# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("assets/app.ico", "assets"),
    ("config.example.json", "."),
    ("README.md", "."),
    ("README.zh-CN.md", "."),
    ("LICENSE", "."),
    ("THIRD_PARTY.md", "."),
]
datas += collect_data_files("soundcard")
datas += collect_data_files("pyatv")

hiddenimports = (
    collect_submodules("pyatv")
    + collect_submodules("pystray")
    + collect_submodules("zeroconf")
    + ["_cffi_backend"]
)

a = Analysis(
    ["tray_app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AirPlayTray",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/app.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AirPlayTray",
)
