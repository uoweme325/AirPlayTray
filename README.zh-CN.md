# AirPlayTray

[English](README.md) | [简体中文](README.zh-CN.md)

[![Tests](https://github.com/uoweme325/AirPlayTray/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/uoweme325/AirPlayTray/actions/workflows/test.yml)

将 Windows 默认播放设备的系统声音实时推送到 HomePod / AirPlay 2 音箱的托盘工具。

**实验性公开预览版，非 Apple 官方项目。**

HomePod 升级新固件后，TuneBlade 等老牌 AirPlay 1 推流工具出现了连接失败、无法播放等兼容性问题。本项目为此采用基于 pyatv 的 AirPlay 2 音频通道与 Windows WASAPI 环回采集。默认向接收端请求 0.3 秒延迟；实际端到端延迟取决于设备和网络。

## 核心特性

- **系统声音采集**：采集 Windows 默认播放设备的声音，无需虚拟声卡。
- **托盘控制**：开始、停止推流、查看自动轮转日志，以及无窗口后台运行。
- **静音待机**：默认安静 60 秒后释放音箱，检测到声音后重新连接；发现和握手需要时间，声音开头可能漏掉。
- **自动重试**：启动时音箱未上线会继续重试，断流后尝试重连；等待期间可以停止。
- **本机配置**：按音箱名称或设备标识选择目标，私人配置不提交到仓库。

## 安装与第一次使用

需要 Windows、64 位 Python 3.9–3.12，以及可在同一局域网发现的 AirPlay 音箱。安装脚本接受上述 Python 版本。本机安装验证使用 Python 3.9.13；GitHub Actions 的 Windows 自动测试覆盖 Python 3.9 和 3.12，最新结果见顶部徽章。自动测试不代表真机播放兼容性。

1. 下载源码并解压到一个固定、可写的文件夹。Release 附件为源码包，需要安装 Python 和依赖。
2. 确认终端中的 `python --version` 指向受支持的 Python，然后双击 `install.cmd`。安装需要联网下载依赖，文件放在项目自己的 `.venv` 中。
3. 双击 `devices.cmd` 查看设备名称和标识。
4. 用文本编辑器打开安装时生成的 `config.json`，把示例名称改成自己的音箱。
5. 先双击 `start.cmd`，确认窗口中的连接结果和实际声音正常。
6. 停止窗口版后，可双击 `background.vbs` 在后台运行。右下角的喇叭图标可能位于隐藏图标区域。

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

托盘右键菜单提供“停止推流 / 开始推流”“查看日志”和“退出”。蓝色表示后台任务仍在运行，包括连接、等待音箱或静音待机；它不表示音箱已经实际播放声音。

`stop.cmd` 会强制停止从当前项目完整脚本路径启动的 Python 进程，适合托盘无法退出时使用。它会先核对进程的命令行路径，不使用可能过期的 PID 直接结束进程。无法查询或停止进程时会报告失败。

可选开机自启：先确认后台运行正常，再在项目目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\enable-startup.ps1
```

它只会创建 `AirPlayTray-Public.lnk`。取消时运行 `shell:startup` 并删除该快捷方式。移动项目后需要重新设置。

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

20 项回归测试直接验证音源、参数和配置逻辑；设备发现与停止进程等外部操作使用隔离样本，不会播放声音或结束真实进程。GitHub Actions 在推送、拉取请求和手动触发时运行 Windows 测试，并检查锁定依赖。测试通过不代表所有音箱和固件已完成兼容性验证。

## 依赖与许可证

`requirements.txt` 列出直接依赖；`requirements-lock.txt` 固定完整安装依赖。安装脚本使用后者。源码以 MIT 许可证提供，第三方库遵循各自许可证，详见 [LICENSE](LICENSE) 和 [THIRD_PARTY.md](THIRD_PARTY.md)。源码包不捆绑 Python 或第三方运行环境。
