from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

from app.database import engine, Base
from app.routes import projects

load_dotenv()

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=os.getenv("APP_NAME", "Project Manager"),
    description="專案管理系統 — 任務追蹤 + Claude Code 使用量監控",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api")


@app.get("/")
def root():
    return {"status": "ok", "message": "Project Manager API is running"}

@app.get("/health")
def health():
    return {"status": "healthy"}
