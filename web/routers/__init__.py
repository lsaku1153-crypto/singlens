"""HTTP 路由分组。

`server.py` 仍是组合根（app、中间件、启动、共享状态与业务 helper）；
这里只放"HTTP 表面"。每拆一组，先跑 `_run_all.py` 24 项再继续。
"""
