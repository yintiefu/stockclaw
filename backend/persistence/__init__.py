"""本地 SQLite 持久化层。

- 强制 aiosqlite，所有读写 async（不阻塞 FastAPI event loop）
- WAL 模式 + busy_timeout=5000ms（防 database is locked）
- PRAGMA user_version 做轻量 migration（无外部工具依赖）
- 决策卡含个人投资决策——不入 git、不上传（backend/.cache/ 已 gitignore）
"""
