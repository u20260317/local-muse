"""板块 2：独立 Chromium 浏览器自动化 + Vault 加密凭证保险库。

隔离铁则：只启动 Playwright 自带独立 Chromium；用户数据、缓存、Cookie
全部存放于项目内 browser_profile/session_<task_id>/，绝不接管本机浏览器。
"""
