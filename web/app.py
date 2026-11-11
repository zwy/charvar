"""Web 管理端入口：python -m web.app → http://0.0.0.0:8710（本机 127.0.0.1 亦可）

- 改 Python 代码后需重启（无热重载）；web/static/** 刷新页面即生效。
- 停止：lsof -ti :8710 | xargs kill（本仓库独占端口时安全）。
"""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from charvar import paths
from charvar.api import build_app

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "web" / "static"

app = build_app(static_dir=STATIC_DIR)


if __name__ == "__main__":
    port = int(os.environ.get("CHARVAR_WEB_PORT", "8710"))
    # 默认 0.0.0.0：同局域网可用本机 IP 访问；要只监听本机则设 CHARVAR_WEB_HOST=127.0.0.1
    host = os.environ.get("CHARVAR_WEB_HOST", "0.0.0.0")
    uvicorn.run("web.app:app", host=host, port=port, log_level="info")
