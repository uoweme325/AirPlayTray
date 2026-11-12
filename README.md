# AirPlayTray

[English](README.md) | [简体中文](README.zh-CN.md)

[![Tests](https://github.com/uoweme325/AirPlayTray/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/uoweme325/AirPlayTray/actions/workflows/test.yml)

Stream audio from the Windows default playback device to HomePod / AirPlay 2 speakers, with a lightweight system tray controller.

**Experimental public preview. Not affiliated with Apple.**

HomePod firmware updates have caused compatibility issues with legacy AirPlay 1 streaming tools such as TuneBlade, including connection failures and an inability to play audio. AirPlayTray uses pyatv's AirPlay 2 audio channel and Windows WASAPI loopback capture. The default requested receiver latency is 0.3 seconds; actual end-to-end latency depends on the device and network.

## Highlights

- **System audio capture:** capture the default Windows playback device without virtual audio cables.
- **Tray controls:** start and stop streaming, view automatically rotated logs, and run without a console window.
- **Idle release:** release the speaker after 60 seconds of silence and reconnect when audio resumes. Discovery and handshaking take time, so the start of audio may be missed.
- **Automatic retry:** retry when the speaker is offline at startup or streaming disconnects; waiting can be stopped.
- **Local configuration:** select a speaker by name or identifier without committing private settings.

## Installation and first use

You need Windows, 64-bit Python 3.9–3.12, and an AirPlay speaker discoverable on the same local network. The installer accepts these Python versions. Local installation was verified with Python 3.9.13. GitHub Actions runs Windows tests on Python 3.9 and 3.12; see the badge above for current results. Automated tests do not establish hardware playback compatibility.

1. Download the source and extract it to a fixed, writable directory. Release attachments contain source code and require Python and dependencies.
2. Check that `python --version` selects a supported Python, then double-click `install.cmd`. Installation downloads dependencies into the project's own `.venv` directory.
3. Run `devices.cmd` to list speaker names and identifiers.
4. Open the generated `config.json` in a text editor and replace the example name with your speaker's name.
5. Run `start.cmd` first and check both the connection messages and actual sound.
6. Stop the console instance before running `background.vbs` for tray mode. The speaker icon may be in the notification area's hidden icons.

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

The tray menu offers start/stop streaming, view logs, and quit. The current menu labels are Chinese: `开始推流` / `停止推流`, `查看日志`, and `退出`. A blue icon means the background task is alive, including connection attempts, waiting for a speaker, and idle monitoring. It does not confirm audible playback.

Use `stop.cmd` if the tray application cannot quit. It force-stops Python processes whose command lines contain the complete script paths of this project. It verifies paths instead of terminating an arbitrary process from a stale PID file. It reports failures to inspect or stop processes.

To enable optional startup at login, first verify tray mode, then run this in the project directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\enable-startup.ps1
```

This creates `AirPlayTray-Public.lnk`. To disable it, open `shell:startup` and delete that shortcut. Set it up again after moving the project.

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

The 20 regression tests exercise the real audio-source, argument, and configuration logic. External device discovery and process termination use isolated samples; the tests neither play audio nor terminate real processes. GitHub Actions runs Windows tests and checks pinned dependencies on pushes, pull requests, and manual dispatch. Passing tests do not guarantee compatibility with every speaker or firmware version.

## Dependencies and license

`requirements.txt` lists direct dependencies; `requirements-lock.txt` pins the complete installation set used by the installer. This project's source uses the MIT license. Third-party libraries retain their own licenses; see [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md). The source archive does not bundle Python or a third-party runtime.
