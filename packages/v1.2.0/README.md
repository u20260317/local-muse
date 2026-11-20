# local-muse v1.2.0 安装包备份

本目录是 v1.2.0 三个安装包的正式备份（分支 `backup-v1.2.0`）。
GitHub 单个 blob 有大小上限，三个包都按 16 MiB 切成了分卷（part00、part01……），
还原方法见下文。完整文件的 SHA-256 在同目录 `SHA256SUMS` 里。

## 文件清单

| 还原后文件 | 分卷数 | 说明 |
|---|---|---|
| `local-muse-v1.2-win64.zip` | 4 | Windows 免安装包：解压后双击 `local-muse.exe`（托盘模式，无需装 Python/ffmpeg） |
| `local-muse_1.2.0_amd64.deb` | 6 | Debian/Ubuntu 包：`sudo apt install ./local-muse_1.2.0_amd64.deb`，装完运行 `local-muse` |
| `local-muse-v1.2-x86_64.AppImage` | 13 | Linux 通用包：还原后 `chmod +x` 直接运行 |

## 还原方法（Linux/macOS 终端）

```bash
cat local-muse-v1.2-win64.zip.part*       > local-muse-v1.2-win64.zip
cat local-muse_1.2.0_amd64.deb.part*      > local-muse_1.2.0_amd64.deb
cat local-muse-v1.2-x86_64.AppImage.part* > local-muse-v1.2-x86_64.AppImage
sha256sum -c SHA256SUMS
chmod +x local-muse-v1.2-x86_64.AppImage
```

Windows 下可用 `copy /b part00+part01+... 目标文件` 合并，或用 7-Zip 打开第一卷解压。

## 对应源码

- 源码版本：仓库 main 提交 `f46962f`（tag `v1.2.0`，Release 说明见
  https://github.com/u20260317/local-muse/releases/tag/v1.2.0 ）
- 本版内容：成片合成 compose（分镜 + ffmpeg + 字幕 + 配音）、系统托盘、
  万相出图/出视频接线、Agent 操作手册 docs/skill/SKILL.md。
