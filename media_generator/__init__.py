"""板块 3：图文视频 API 聚合调度 + 并发请求池。

本板块与 vault 完全解耦：不 import vault、不读取任何原始登录凭证。
生成请求里只使用 config.json 中的 API Key，且绝不混入绘画 prompt。
"""
