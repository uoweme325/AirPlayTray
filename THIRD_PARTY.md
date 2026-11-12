# 第三方依赖

本项目调用以下库。依赖通过安装脚本单独安装，源码包不捆绑解释器或第三方库。

| 依赖 | 用途 | 已安装版本中的许可证标识 | 上游 |
| --- | --- | --- | --- |
| pyatv | AirPlay 发现、连接和传输 | MIT | [仓库](https://github.com/postlund/pyatv) |
| SoundCard | Windows WASAPI 环回采集 | BSD 3-clause | [仓库](https://github.com/bastibe/SoundCard) |
| NumPy | PCM 数值和字节序转换 | BSD 及其发行包附带声明 | [仓库](https://github.com/numpy/numpy) |
| pystray | Windows 托盘图标 | LGPLv3 | [仓库](https://github.com/moses-palmer/pystray) |
| Pillow | 托盘图标绘制 | MIT-CMU | [仓库](https://github.com/python-pillow/Pillow) |

以上标识来自所固定版本的包元数据。完整依赖列表见 `requirements-lock.txt`，包括 pyatv 引入的 miniaudio、cryptography 等。各依赖以其发行包中的许可证和声明为准；项目自身的 MIT 许可证不替代第三方许可证。

项目不附带 Apple 软件、固件或商标素材，也不代表 Apple 官方项目。AirPlay、HomePod 等名称用于描述兼容目标。
