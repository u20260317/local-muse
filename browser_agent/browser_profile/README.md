# 占位说明（此处不存放任何浏览器数据）

本目录只是强制目录结构中的占位说明，**不是** Browserr 用户数据目录。

项目内唯一的 Browserr（Servo）用户数据根目录是：

```
<项目根>/browser_profile/
└── session_<task_id>/     # 每条任务独立目录，互不共享 Cookie、缓存、本地存储
```

- 由 `core/paths.py::session_dir()` 统一创建与解析，路径基于 `__file__` 动态计算，不含硬编码绝对路径。
- 由 `browser_agent/browser_runner.py` 作为 Browserr Driver 的 `profile_dir` 使用。
- 只使用 `scripts/install_browserr.*` 下载到项目根级 `browserr_bin/` 的 Browserr 官方 release
  （engine 二进制 + driver.py）；运行时由 `paths.setup_browserr_env()` 注入 `BROWSERR_HOME/BROWSERR_ENGINE`。
- `browserr_bin/`（二进制）与本目录所对应的 `browser_profile/`（用户数据）职责分离，两者均不入库。
- 绝不接管、读取、唤醒本机 Chrome / Edge / Firefox，不共用任何配置文件。
- 面板「浏览器会话管理」中的清空缓存 / 一键重置，作用对象是项目根级 `browser_profile/`。
