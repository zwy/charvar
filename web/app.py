"""Web 管理端入口：python -m web.app → http://127.0.0.1:8710

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
    uvicorn.run("web.app:app", host="127.0.0.1", port=port, log_level="info")
