# AirPlayTray — Stream Windows PC Audio to HomePod / AirPlay 2 (TuneBlade Alternative)

[English](README.md) | [简体中文](README.zh-CN.md)

[![Tests](https://github.com/uoweme325/AirPlayTray/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/uoweme325/AirPlayTray/actions/workflows/test.yml)

An open-source **TuneBlade alternative** for **Windows 10 / 11** when legacy AirPlay 1 streaming fails on **HomePod OS 27** and recent HomePod firmware updates. Stream **Windows system audio** from your default playback device to **HomePod, HomePod mini, and AirPlay 2 speakers** with a lightweight system tray controller — no virtual audio cables or Python install required with the portable ZIP.

**Experimental public preview. Not affiliated with Apple.**

HomePod firmware updates have caused compatibility issues with legacy AirPlay 1 streaming tools such as TuneBlade, including connection failures and an inability to play audio. AirPlayTray uses pyatv's AirPlay 2 audio channel and Windows WASAPI loopback capture to act as a lightweight Windows AirPlay 2 audio sender. The default requested receiver latency is 0.3 seconds; actual end-to-end latency depends on the device and network.

## Highlights

- **System audio capture:** capture the default Windows playback device without virtual audio cables.
- **Tray controls & speaker picker:** right-click menu in Simplified Chinese or English (matching the Windows display language), dynamic **Select Speaker** submenu with **Rescan Speakers**, and automatically rotated logs.
- **Real status-driven icon:** grey when stopped or errored, blinking teal while connecting or retrying, steady teal during idle monitoring, and steady blue only after the streaming pipeline actively reads audio frames.
- **Zero-config tray launch:** starts without requiring a pre-created `config.json`; auto-selects only when no prior config exists and a single clearly identified HomePod is found on the LAN.
- **Idle release:** release the speaker after 60 seconds of silence and reconnect when audio resumes. Discovery and handshaking take time, so the start of audio may be missed.
- **Automatic retry:** retry when the speaker is offline at startup or streaming disconnects; waiting can be stopped.

## Installation and first use

### Option A: Portable Windows x64 build (no Python required)

1. Download `AirPlayTray-win-x64.zip` from GitHub Releases and verify `SHA256SUMS.txt` if desired.
2. Extract it to a fixed, writable directory and launch `AirPlayTray.exe`.
3. If no `config.json` exists yet and exactly one HomePod is discovered on your local network, AirPlayTray connects automatically and saves the selection to `data\config.json` next to `AirPlayTray.exe`. Otherwise, right-click the tray icon and choose **Select Speaker**.

### Option B: Run from source (Windows, 64-bit Python 3.9–3.12)

You need Windows, 64-bit Python 3.9–3.12, and an AirPlay speaker discoverable on the same local network. Local development was verified with Python 3.9.13; GitHub Actions runs Windows tests on Python 3.9 and 3.12 and builds the Windows x64 bundle on Python 3.12. Automated tests and `--self-check` do not establish live hardware playback compatibility across all receivers and firmwares.

1. Download the source and extract it to a fixed, writable directory.
2. Check that `python --version` selects a supported Python, then double-click `install.cmd`. Installation downloads dependencies into the project's own `.venv` directory.
3. Either edit `config.json` manually (or inspect speakers via `devices.cmd`), or run `background.vbs` directly and pick your speaker from the tray **Select Speaker** submenu.
4. For CLI diagnostics, run `start.cmd` first and check both the connection messages and actual sound.

If your system only provides the Python Launcher, run `py -3.12 setup_env.py` in the project directory, replacing the version with an installed supported version. The project does not depend on the developer's Python path. Recreate `.venv` after moving the project or replacing the base Python installation.

Example device configuration:

```json
{
  "device_name": "Living Room Speaker",
  "device_id": null
}
```

Names use a case-insensitive substring match. If several speakers have similar names, set `device_id` from the output of `devices.cmd` and set `device_name` to `null`. Keep these settings local.

A command-line target overrides the target in the configuration file. If both `--id` and `--name` are supplied, the identifier takes precedence. `--list` does not require a configuration file.

```powershell
.\.venv\Scripts\python.exe airplay_system_audio.py --name "Living Room Speaker"
.\.venv\Scripts\python.exe airplay_system_audio.py --list
.\.venv\Scripts\python.exe tray_app.py --config "config.json"
```

## Everyday controls

### Volume (v0.3)

The **Volume** submenu offers 0%, 25%, 50%, 75%, and 100%, plus **Increase / Decrease 5%**. It adjusts this AirPlay session without changing Windows volume or restarting the connection. 0% mutes the session; select another level to restore sound.

**Current** shows an applied level; **Saved (next connection)** means the selection will be applied when the speaker reconnects. Choices are saved to `config.json` as `volume` and restored after standby, reconnection, and app restart. `null` or an absent field retains the receiver's initial volume (or the existing fallback). `--volume` overrides the saved value. Volumes must be finite numbers from 0 to 100.

Until the initial receiver volume is known, choose a preset before using the +/- controls. Volume controls are unavailable with the experimental `--engine pipe`. A failed network or save operation produces a notification and log entry; if only saving fails, the live volume may have changed but will not persist across restart.

Audio capture continues to use the default Windows playback device; this version adds no capture-device picker or virtual-cable requirement.

### Tray and startup

The tray menu follows your Windows display language (Simplified Chinese or English) with **Start / Stop Streaming**, **Select Speaker** (including **Rescan Speakers**), **Open Log**, and **Quit**.
Icon colors indicate the real streaming stage:
- **Grey:** stopped or errored.
- **Blinking teal:** connecting or retrying.
- **Steady teal:** idle monitoring (silence detection).
- **Steady blue:** actively sending audio frames (after handshake and setup succeed).
Notice that `--self-check` (`AirPlayTray.exe --self-check` or `tray_app.py --self-check`) runs an offline self-check of runtime imports, `assets/app.ico`, and writable `data/` creation without opening audio devices or the network.

Use `stop.cmd` if a source-launched instance cannot quit. It force-stops Python processes whose command lines contain the complete script paths of this project. It verifies paths instead of terminating an arbitrary process from a stale PID file. It reports failures to inspect or stop processes.

To enable optional startup at login, first verify tray mode, then run this in the project or extracted release directory (`enable-startup.ps1` prefers `AirPlayTray.exe` if present, otherwise falls back to `background.vbs`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\enable-startup.ps1
```

This creates `AirPlayTray-Public.lnk`. To disable it, open `shell:startup` and delete that shortcut. Set it up again after moving the folder.

## Common options

| Option | Default | Meaning |
| --- | --- | --- |
| `--name` / `--id` | Local configuration | Target speaker |
| `--config` | `config.json` in the project directory | Device configuration path |
| `--latency` | `0.3` | Requested receiver latency in seconds, clamped to at least `0.25`; not measured end-to-end latency |
| `--prebuffer` | `0.12` | Local audio prebuffer in seconds |
| `--volume` | Receiver initial value or fallback | Streaming volume percentage |
| `--idle-timeout` | `60` | Silence duration before releasing the speaker; `0` disables release |
| `--silence-threshold` | `-55` | Audio activity threshold in dBFS |
| `--retry-delay` | `3` | Seconds between retries |
| `--max-retries` | `0` | Allowed retry count; `0` means unlimited |
| `--engine` | `direct` | `pipe` is an experimental file-stream channel |
| `--report` | Console: `60`; tray: `0` | Status output interval in seconds; `0` disables it |
| `--seconds` | `0` | Limit an individual streaming session; idle release may end it earlier |
| `--debug` | Off | Protocol logging in console mode |
| `--dump` | Off | Record captured audio to WAV |

Use `airplay_system_audio.py --help` for all options. Application messages and command-line help currently remain in Chinese. `--selftest` and `--selftest-live` play test tones on the real speaker; stop other streaming sessions first. Use console mode for diagnostics and tray mode for everyday control.

## Troubleshooting and limitations

- **Speaker not found:** check power, local-network discovery, and `devices.cmd`. Password-protected receivers and receivers requiring persistent pairing are outside the current validation scope.
- **Dropouts:** try `start.cmd --latency 0.6 --prebuffer 0.3` and check wireless signal quality.
- Recovery after changing the default Windows output, unplugging an audio device, or waking from sleep needs more hardware testing. Stop and restart streaming manually if necessary.
- Discovery and handshaking can miss short notification sounds or the beginning of resumed audio.
- The original prototype was used with a HomePod mini. Other receiver models and firmware versions are not guaranteed to work.
- The project patches pyatv internals and latency handling, so `pyatv==0.18.0` is pinned. Revalidate before upgrading dependencies.
- The experimental `pipe` channel does not implement every `direct` option, including custom volume and silence thresholds.
- Run one sender at a time. Other copies or applications can compete for the same speaker.
- `diagnose.cmd` records system audio to `diagnostic.wav`. Stop tray streaming and close private audio first. WAV files, logs, local configuration, and process files are ignored by Git.
- Protocol logs may include device addresses and pairing information. Review and redact them before filing an issue.

The public version keeps the dependency's standard audio-encryption counter behavior. The prototype's fixed-zero nonce diagnostic option has been removed.

## Tests

Run this from the project directory:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

The automated test suite exercises audio buffering, argument and settings validation, path resolution (`app_paths`), HomePod detection/auto-selection rules, real status transitions, generation guards, serial speaker switching, `--self-check`, license collection, and stop-script scoping using isolated fixtures (without playing real audio or terminating unrelated processes). GitHub Actions runs Windows tests on pushes/pull requests and builds portable x64 release bundles (`release.yml`) on `v*` tags or manual dispatch. Passing tests do not guarantee compatibility with every speaker or firmware version.

## Dependencies and license

`requirements.txt` lists direct dependencies, `requirements-lock.txt` pins the runtime installation set, and `requirements-build.txt` pins PyInstaller for packaging. This project's source uses the MIT license. Third-party libraries retain their own licenses (including LGPL for `pystray` and `zeroconf`); portable release archives bundle dependency license texts under `licenses/`. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md).
