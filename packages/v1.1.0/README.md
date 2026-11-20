# local-muse v1.1.0 安装包备份

三个平台包的正式备份（分支 `backup-v1.1.0`），与源码 main 分支分离存放。

| 文件 | 平台 | 说明 |
|---|---|---|
| `local-muse-v1.1-win64.zip` | Windows | 解压双击 local-muse.exe；Browserr 无 Windows 引擎，浏览器采集不可用 |
| `local-muse_1.1.0_amd64.deb` | Debian/Ubuntu | `sudo apt install ./local-muse_1.1.0_amd64.deb` 后运行 `local-muse`；引擎首次运行自动下载 |
| `local-muse-v1.1-x86_64.AppImage.part00` ~ `part07` | Linux 通用 | AppImage 本体 126 MB，超过 GitHub 单文件 100 MB 上限，故按 16 MiB 分卷存放 |

## 还原 AppImage

```bash
cat local-muse-v1.1-x86_64.AppImage.part* > local-muse-v1.1-x86_64.AppImage
chmod +x local-muse-v1.1-x86_64.AppImage
sha256sum -c SHA256SUMS   # 校验三个包（含拼好的 AppImage）
```
