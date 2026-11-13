# 第三方依赖

本项目调用以下第三方库。从源码运行时，依赖通过安装脚本安装；在 GitHub Release 发布的 Windows 便携二进制包（`AirPlayTray-win-x64.zip`）中，运行库随包捆绑，所有固定运行时依赖的原始许可证与声明文件均完整附带在压缩包内同级的 `licenses/` 目录中。

| 依赖 | 用途 | 已安装版本中的许可证标识 | 上游 |
| --- | --- | --- | --- |
| pyatv | AirPlay 发现、连接和传输 | MIT | [仓库](https://github.com/postlund/pyatv) |
| SoundCard | Windows WASAPI 环回采集 | BSD 3-clause | [仓库](https://github.com/bastibe/SoundCard) |
| NumPy | PCM 数值和字节序转换 | BSD 及其发行包附带声明 | [仓库](https://github.com/numpy/numpy) |
| pystray | Windows 托盘图标 | LGPLv3 | [仓库](https://github.com/moses-palmer/pystray) |
| Pillow | 托盘图标绘制 | MIT-CMU | [仓库](https://github.com/python-pillow/Pillow) |
| zeroconf | mDNS / 服务发现（pyatv 依赖） | LGPL-2.1-or-later | [仓库](https://github.com/python-zeroconf/python-zeroconf) |

以上标识来自所固定版本的包元数据。完整依赖列表见 `requirements-lock.txt`，另包含 `miniaudio`（MIT）、`cryptography`（Apache-2.0 OR BSD-3-Clause）、`aiohttp` 等传输与加密依赖。对于采用 LGPL 的 `pystray` 与 `zeroconf`，可按上游仓库获取对应版本源码并遵守各自 LGPL 条款。项目自身的 MIT 许可证不替代第三方许可证。

项目不附带 Apple 软件、固件或商标素材，也不代表 Apple 官方项目。AirPlay、HomePod 等名称用于描述兼容目标。
