"""Loopback-only web entry point; keep model credentials in the server process."""

import argparse
import os
from contextlib import chdir
from pathlib import Path

import uvicorn
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from quant_assistant.config import Settings
from quant_assistant.controllers.auth_app import create_authenticated_app


def main():
    root = Path(__file__).resolve().parents[1]
    workspace = root.parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=workspace / "data")
    args = parser.parse_args()
    with chdir(workspace):
        _serve(args, root)


def _serve(args, root):
    workspace = root.parent.parent
    web = Path(os.environ.get("QUANT_WEB_OUTPUT_DIR", workspace / "data/runtime/web")) / "build"
    index = web / "index.html"
    if not index.is_file():
        raise SystemExit("网页产物缺失，请运行 web/start.ps1 构建并启动。")
    os.environ["QUANT_DATA_DIR"] = str(args.data_dir.resolve())
    app = create_authenticated_app(Settings.from_env(), public_web_assets=True)

    @app.get("/", include_in_schema=False, response_class=HTMLResponse)
    @app.get("/index.html", include_in_schema=False, response_class=HTMLResponse)
    def local_index():
        html = index.read_text(encoding="utf-8")
        return html.replace("<head>", "<head><script>window.__QUANT_LOCAL_WEB__=true;</script>", 1)

    app.mount("/", StaticFiles(directory=web, html=True))
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
