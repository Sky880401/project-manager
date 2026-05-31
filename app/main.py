from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import os
import logging

from app.database import engine, Base, SessionLocal
from app.routes import projects
from app.routes import claude as claude_routes
from app.services.claude_monitor import get_current_rate_limit, resolve_rate_limit

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


def check_rate_limit_reset():
    """每分鐘檢查 rate limit 是否已過期，自動 resolve"""
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
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(check_rate_limit_reset, "interval", minutes=1)


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
