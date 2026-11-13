# AirPlayTray — Windows 电脑声音推流到 HomePod / AirPlay 2 音箱（TuneBlade 替代方案）

[English](README.md) | [简体中文](README.zh-CN.md)

[![Tests](https://github.com/uoweme325/AirPlayTray/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/uoweme325/AirPlayTray/actions/workflows/test.yml)

开源免费的 **TuneBlade 替代工具**，解决 HomePod / HomePod mini 升级新版固件（如 HomePod OS 27）后连不上、断流或无声的问题。将 **Windows 10 / 11 电脑系统声音**实时投送到 **HomePod、HomePod mini 与 AirPlay 2 音箱**，无需虚拟声卡；使用 Release 便携包即可免装 Python 开箱即用。

**实验性公开预览版，非 Apple 官方项目。**

HomePod 升级新固件后，TuneBlade 等老牌 AirPlay 1 推流工具出现了连接失败、无法播放等兼容性问题。本项目为此采用基于 pyatv 的 AirPlay 2 音频通道与 Windows WASAPI 环回采集，在任务栏托盘中完成切箱与推流控制。默认向接收端请求 0.3 秒延迟；实际端到端延迟取决于设备和网络。

## 核心特性

- **系统声音采集**：采集 Windows 默认播放设备的声音，无需虚拟声卡。
- **托盘选箱与双语菜单**：右键菜单根据 Windows 显示语言自动使用简体中文或英文，支持在「选择音箱 / Select Speaker」子菜单中切换设备或重新扫描，并保留自动裁剪日志。
- **真实状态驱动的三色托盘图标**：停用或异常为灰色，连接与重连时青色闪烁，静音待机为常亮青色，仅在真正发送音频包后才变为常亮蓝色。
- **零配置首次启动**：无需预先手动编辑 `config.json`；仅在没有旧配置且局域网只发现一台明确标识的 HomePod 时自动选定并保存。
- **静音待机**：默认安静 60 秒后释放音箱，检测到声音后重新连接；发现和握手需要时间，声音开头可能漏掉。
- **自动重试**：启动时音箱未上线会继续重试，断流后尝试重连；等待期间可以停止。

## 安装与第一次使用

### 方式一：Windows 便携压缩包（推荐，免装 Python）

1. 在 GitHub Releases 下载 `AirPlayTray-win-x64.zip`（可配合 `SHA256SUMS.txt` 校验完整性）。
2. 解压到固定、可写的文件夹，直接双击运行 `AirPlayTray.exe`。
3. 若还没有 `config.json` 且局域网中只发现一台明确的 HomePod，程序会自动连接并保存配置到同级 `data\config.json`；否则右键任务栏图标，在「选择音箱」中挑选目标。

### 方式二：从源码运行（Windows，64 位 Python 3.9–3.12）

需要 Windows、64 位 Python 3.9–3.12，以及可在同一局域网发现的 AirPlay 音箱。本机源码与打包离线自检基于 Python 3.9.13 验证；GitHub Actions 分别在 Python 3.9 和 3.12 上执行自动化测试，并在 Python 3.12 上打包便携版。自动测试与 `--self-check` 不代表真机硬件和固件的播放兼容性验证。

1. 下载源码并解压到一个固定、可写的文件夹。
2. 确认终端中的 `python --version` 指向受支持的 Python，然后双击 `install.cmd`。安装需要联网下载依赖，文件放在项目自己的 `.venv` 中。
3. 可用 `devices.cmd` 列出设备并编辑 `config.json`，也可直接双击 `background.vbs` 启动托盘，在右键菜单「选择音箱」中挑选设备。
4. 如需排查命令行日志，可先用 `start.cmd` 观察连接输出。

如果系统只提供 Python Launcher，可以在项目目录运行 `py -3.12 setup_env.py`，其中版本号替换为自己安装的受支持版本。项目不依赖开发者电脑的 Python 路径；更换项目位置或基础 Python 后，应重新创建 `.venv`。

设备配置示例：

```json
{
  "device_name": "客厅音箱",
  "device_id": null
}
```

名称按不区分大小写的子串匹配。存在同名或相似名称时，建议填写 `device_id` 并将 `device_name` 设为 `null`。设备标识取自 `devices.cmd` 的输出，不需要提交到仓库。

命令行指定目标会覆盖配置文件中的目标；同时指定 `--id` 和 `--name` 时，标识优先。`--list` 不要求存在设备配置。

```powershell
.\.venv\Scripts\python.exe airplay_system_audio.py --name "客厅音箱"
.\.venv\Scripts\python.exe airplay_system_audio.py --list
.\.venv\Scripts\python.exe tray_app.py --config "config.json"
```

## 日常控制

托盘右键菜单会根据系统语言显示简体中文或英文，提供「开始推流 / 停止推流」「选择音箱（含重新扫描）」「查看日志」和「退出」。
托盘图标颜色对应真实运行阶段：
- **灰色**：已停止或异常。
- **青色闪烁**：连接中或重连中。
- **青色常亮**：静音待机监听中。
- **蓝色常亮**：已完成握手与会话建立，并真正开始发送音频帧。
使用 `AirPlayTray.exe --self-check`（或 `tray_app.py --self-check`）可执行纯离线环境与可写 `data/` 目录自检，不会打开网络或音频设备。

`stop.cmd` 会强制停止从当前项目完整脚本路径启动的 Python 进程，适合源码方式运行且托盘无法退出时使用。它会先核对进程的命令行路径，不使用可能过期的 PID 直接结束进程。无法查询或停止进程时会报告失败。

可选开机自启：先确认托盘运行正常，再在程序目录或源码目录运行（`enable-startup.ps1` 存在 `AirPlayTray.exe` 时优先指向可执行文件，否则回退到 `background.vbs`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\enable-startup.ps1
```

它只会创建 `AirPlayTray-Public.lnk`。取消时运行 `shell:startup` 并删除该快捷方式。移动程序位置后需要重新设置。

### 音量控制（v0.3）

托盘“音量”子菜单提供 0%、25%、50%、75%、100% 档位，以及每次增加或减少 5%。调节的是本路 AirPlay 会话音量，不改 Windows 系统音量，也不为调节音量重新连接。0% 表示静音，选择其他档位恢复声音。

“当前”表示已经应用的音量；“已保存（下次连接应用）”表示待音箱连接后生效。选择保存在配置的 `volume` 字段中，待机、重连和程序重启后恢复。字段缺失或为 `null` 时保留音箱初始音量或原有回退值；`--volume` 优先于配置，输入须为 0–100 的有限数字。

尚未获得音箱初始音量时，先选择一个档位再使用加减操作。实验性的 `--engine pipe` 暂不支持这组菜单。网络或保存失败会提示并写入日志；若只有保存失败，当前音量可能已改变，但重启后不会保留。

继续采集 Windows 默认播放设备，不增加抓音设备选择或虚拟声卡依赖。

## 常用参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--name` / `--id` | 从本机配置读取 | 目标音箱 |
| `--config` | 项目目录的 `config.json` | 设备配置路径 |
| `--latency` | `0.3` | 向接收端请求的延迟秒数，程序下限 `0.25`；不等于实测端到端延迟 |
| `--prebuffer` | `0.12` | 本机音源预缓冲秒数 |
| `--volume` | 接收端初始值或回退值 | 推流音量百分比 |
| `--idle-timeout` | `60` | 安静后释放音箱的秒数；`0` 为不释放 |
| `--silence-threshold` | `-55` | 判断声音活动的 dBFS 门限 |
| `--retry-delay` | `3` | 重试间隔秒数 |
| `--max-retries` | `0` | 允许的重试次数；`0` 为不限 |
| `--engine` | `direct` | `pipe` 为实验性的文件流通道 |
| `--report` | 窗口版 `60`，托盘版 `0` | 状态输出间隔；`0` 为关闭 |
| `--seconds` | `0` | 限制单次推流会话时长；静音待机可能让实际运行提前结束 |
| `--debug` | 关闭 | 窗口版协议日志 |
| `--dump` | 关闭 | 记录采集音频为 WAV |

所有参数可通过 `airplay_system_audio.py --help` 查看。`--selftest` 和 `--selftest-live` 会在真实音箱上播放测试音，运行前应结束其他推流。托盘入口主要用于日常控制，诊断参数应通过窗口版使用。

## 排查与限制

- 找不到设备：确认音箱上线，网络允许设备发现，再运行 `devices.cmd`。带密码或需要持久配对的接收端不在当前验证范围。
- 卡顿：可以尝试 `start.cmd --latency 0.6 --prebuffer 0.3`，并检查网络信号。
- 切换 Windows 默认输出、拔插声卡、睡眠唤醒后的恢复仍需更多真机测试；必要时手动停止并重新开始。
- 重新连接需要发现和握手，短促的通知声或声音开头可能被漏掉。
- 仅验证过原型使用的 HomePod mini 场景，无法保证其他接收端或固件版本兼容。
- 项目修改了 pyatv 的内部接口和部分延迟处理，因此固定 `pyatv==0.18.0`。升级依赖前需要重新验证。
- `pipe` 通道不支持全部 `direct` 参数，例如自定义音量和静音门限；它只供实验排查。
- 建议只运行一套推流。不同目录或其他发送端同时连接音箱可能相互抢占。
- `diagnose.cmd` 会记录系统声音到 `diagnostic.wav`；诊断前结束托盘推流并关闭私人音频。WAV、日志、本机配置和进程文件均已加入忽略清单。
- 协议日志可能含设备地址和配对信息，提交问题前请检查并脱敏。

公开版保留依赖库默认的音频加密计数器处理，移除了原型中的固定零值 nonce 调试方案。

## 测试

在项目目录运行：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

自动化回归测试直接验证音源缓冲、参数与配置校验、路径解析（`app_paths`）、HomePod 识别与首次启动自动选择规则、状态回调与代际防覆盖、串行切换音箱、离线 `--self-check`、许可证收集以及限定路径的停止脚本；外部设备发现与进程操作均使用隔离样本，不会播放声音或终止无关真实进程。GitHub Actions 在推送/PR 时执行测试，并在推送 `v*` 标签或手动触发时构建 Windows x64 便携包（`release.yml`）。测试通过不代表所有音箱和固件已完成真机兼容性验证。

## 依赖与许可证

`requirements.txt` 列出直接依赖，`requirements-lock.txt` 固定完整运行依赖，`requirements-build.txt` 固定 PyInstaller 打包依赖。源码以 MIT 许可证提供，第三方库遵循各自许可证（含 LGPL 协议的 `pystray` 与 `zeroconf`）；便携压缩包内 `licenses/` 目录随附各固定运行依赖的原始许可证文件，详见 [LICENSE](LICENSE) 和 [THIRD_PARTY.md](THIRD_PARTY.md)。
