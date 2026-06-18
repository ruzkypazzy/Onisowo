"""
Mobile/PWA backend for Àkànjí Oníṣòwò.

Exposes the same Agent over HTTP so the PWA (and any future mobile app)
can talk to the bot without going through Telegram. Same skills, same
risk engine, same memory, same Yoruba personality.

Run: python mobile_server.py
Port: 8765 (configurable via MOBILE_PORT)

Endpoints:
  GET  /health     — liveness check
  POST /chat       — send a message, get a reply
  GET  /assets/*   — serve images, PWA, etc
  GET  /skills     — list available skills
  GET  /status     — bot status (balance, open positions, etc)
"""

import os
import sys
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("akanji.mobile")

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    logger.warning("FastAPI not installed — pip install fastapi uvicorn")


def create_app():
    if not HAS_FASTAPI:
        raise RuntimeError("Install fastapi + uvicorn first: pip install fastapi uvicorn")

    app = FastAPI(title="Àkànjí Oníṣòwò — Mobile API", version="1.0")

    # CORS so the PWA can hit us from any origin (it's a local service)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Lazy-init the agent (don't blow up if .env is missing during dev)
    agent = None

    def get_agent():
        nonlocal agent
        if agent is None:
            from agent.core import Agent
            agent = Agent()
        return agent

    # ---------- Routes ----------

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "akanji-mobile", "version": "1.0"}

    @app.get("/status")
    def status():
        try:
            a = get_agent()
            bal = a.bitget.get_account_balance("USDT")
            n_open = len(a.db.get_open_trades())
            return {
                "ok": True,
                "balance_usdt": bal,
                "open_positions": n_open,
                "skills": len(a.skills.skills),
            }
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/chat")
    async def chat(request: Request):
        from agent.core import AgentContext
        body = await request.json()
        text = (body.get("text") or "").strip()
        user_id = int(body.get("user_id") or 0) or 1
        if not text:
            raise HTTPException(400, "text is required")

        a = get_agent()
        ctx = AgentContext(
            user_id=user_id,
            user_message=text,
            command="ask",
            args={},
        )
        try:
            reply = a.handle(ctx)
        except Exception as e:
            logger.exception("chat failed")
            reply = f"❌ Internal error: {e}"
        return {"ok": True, "reply": reply, "user_id": user_id}

    # Serve the PWA + assets from the repo
    repo_root = Path(__file__).parent
    app_dir = repo_root / "app"
    assets_dir = repo_root / "assets"

    if app_dir.exists():
        app.mount("/app", StaticFiles(directory=str(app_dir), html=True), name="app")
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def root():
        idx = app_dir / "index.html"
        if idx.exists():
            return FileResponse(str(idx))
        return {"service": "akanji-mobile", "see": "/app/index.html"}

    return app


def main():
    if not HAS_FASTAPI:
        print("❌ FastAPI not installed. Run: pip install fastapi uvicorn")
        sys.exit(1)
    port = int(os.environ.get("MOBILE_PORT", "8765"))
    host = os.environ.get("MOBILE_HOST", "0.0.0.0")
    app = create_app()
    print()
    print("=" * 60)
    print("  Àkànjí Oníṣòwò — Mobile/PWA backend")
    print("=" * 60)
    print(f"  Listening: http://{host}:{port}")
    print(f"  Open in browser: http://localhost:{port}/")
    print()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
