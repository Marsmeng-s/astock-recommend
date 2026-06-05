"""手机云端版入口 — 部署到公网后，手机浏览器直接打开"""
from __future__ import annotations

import os

# 云端始终使用手机版界面
os.environ["MOBILE_ONLY"] = "1"

from web_app import app  # noqa: E402, F401

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
