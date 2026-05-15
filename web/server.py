"""
FastAPI web server for ИНТЕГРАЛ school landing page.

Serves index.html + static teacher photos, handles /api/apply form submissions
by forwarding them as Telegram messages to SCHOOL_BOT_APPLICATIONS_CHAT_ID.

Run:
    uvicorn server:app --host 0.0.0.0 --port 8080
"""

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── Config ──────────────────────────────────────────────────────────────────

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent  # school-bot/

# Load .env from repo root (mirrors school-bot config.py logic)
_env_path = ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k, _v = _k.strip(), _v.strip().strip("\"'")
        if _k and _k not in os.environ:
            os.environ[_k] = _v

BOT_TOKEN = os.getenv("SCHOOL_BOT_TOKEN", "")
APPLICATIONS_CHAT_ID = os.getenv("SCHOOL_BOT_APPLICATIONS_CHAT_ID", "")

# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(docs_url=None, redoc_url=None)

# Serve teacher photos and other assets at /assets/
_assets_dir = ROOT / "school-bot" / "assets"
if _assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return (BASE / "index.html").read_text(encoding="utf-8")


@app.post("/api/apply")
async def apply(
    name: str = Form(...),
    school_class: str = Form(...),
    subject: str = Form(...),
    lesson_type: str = Form(...),
    goal: str = Form(...),
    contact_method: str = Form(...),
    contact_value: str = Form(...),
    comment: str = Form(""),
):
    text = (
        "🌐 <b>Заявка с сайта</b>\n\n"
        f"📝 <b>Имя:</b> {name}\n"
        f"🏫 <b>Класс:</b> {school_class}\n"
        f"📖 <b>Предмет:</b> {subject}\n"
        f"👥 <b>Формат:</b> {lesson_type}\n"
        f"🎯 <b>Цель:</b> {goal}\n"
        f"📞 <b>Способ связи:</b> {contact_method}\n"
        f"🔗 <b>Контакт:</b> {contact_value}\n"
        f"💬 <b>Комментарий:</b> {comment or '—'}"
    )

    if not BOT_TOKEN or not APPLICATIONS_CHAT_ID:
        # Dev mode — just log and return success
        print("[web] Form submission (bot not configured):", text)
        return JSONResponse({"ok": True})

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": int(APPLICATIONS_CHAT_ID),
                "text": text,
                "parse_mode": "HTML",
            },
        )

    if resp.status_code == 200:
        return JSONResponse({"ok": True})

    return JSONResponse({"ok": False, "detail": resp.text}, status_code=502)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}
