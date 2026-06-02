from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import os
import logging

from app.database import engine, Base, SessionLocal
from app.routes import projects
from app.routes import claude as claude_routes
from app.routes import line_bot
from app.routes import bmo
from app.services.claude_monitor import get_current_rate_limit, resolve_rate_limit
from app.services.line_push import check_and_notify_rate_limit

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=os.getenv("APP_NAME", "Project Manager"),
    description="專案管理系統 — 任務追蹤 + Claude Code 使用量監控",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api")
app.include_router(claude_routes.router, prefix="/api")
app.include_router(bmo.router, prefix="/api")
app.include_router(line_bot.router)

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# LINE LIFF / 行動瀏覽器會狠狠快取 HTML，導致部署後使用者看不到更新。
# 對 HTML 入口一律回 no-cache，讓每次開啟都重抓最新（JS/邏輯都在這幾頁內）。
NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}

@app.get("/api-explorer", include_in_schema=False)
def api_explorer():
    return FileResponse(os.path.join(STATIC_DIR, "api-explorer.html"), headers=NO_CACHE)

@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers=NO_CACHE)

@app.get("/liff", include_in_schema=False)
def liff():
    return FileResponse(os.path.join(STATIC_DIR, "liff.html"), headers=NO_CACHE)


def backup_database():
    """每小時自動備份 SQLite"""
    import shutil, glob
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "project_manager.db")
    backup_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backups")
    if not os.path.exists(db_path):
        return
    os.makedirs(backup_dir, exist_ok=True)
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"project_manager_{ts}.db")
    shutil.copy2(db_path, dest)
    files = sorted(glob.glob(os.path.join(backup_dir, "*.db")))
    for old in files[:-48]:
        os.remove(old)
    logger.info(f"DB backed up → {dest}")


def check_rate_limit_reset():
    """每分鐘檢查 rate limit 是否已過期，自動 resolve 並發送 LINE 推播"""
    from datetime import datetime, timezone
    db = SessionLocal()
    try:
        event = get_current_rate_limit(db)
        if event and event.reset_at:
            now = datetime.now(timezone.utc)
            reset = event.reset_at.replace(tzinfo=timezone.utc) if event.reset_at.tzinfo is None else event.reset_at
            if now >= reset:
                resolve_rate_limit(db)
                logger.info("Rate limit auto-resolved — Claude is available again")
        check_and_notify_rate_limit(db)
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(check_rate_limit_reset, "interval", minutes=1)
scheduler.add_job(backup_database, "interval", hours=1)


@app.on_event("startup")
def startup():
    scheduler.start()
    logger.info("Scheduler started")


@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()


@app.get("/")
def root():
    return {"status": "ok", "message": "Project Manager API is running", "version": "0.2.0"}


@app.get("/health")
def health():
    return {"status": "healthy"}
