#!/usr/bin/env python3
"""Claude Code hook — PreToolUse (session start 偵測)
設定方法：在 .claude/settings.json 的 hooks 區段加入此腳本
"""
import sys
import json
import httpx
import uuid
import os
from datetime import datetime, timezone

API_URL = os.getenv("PM_API_URL", "https://lxc.tail92862c.ts.net")

try:
    hook_input = json.load(sys.stdin)
except Exception:
    hook_input = {}

session_id = hook_input.get("session_id") or str(uuid.uuid4())
project_path = os.getcwd()

try:
    httpx.post(
        f"{API_URL}/api/claude/sessions/start",
        json={
            "session_id": session_id,
            "project_path": project_path,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
        timeout=3,
    )
except Exception:
    pass  # API 無法連線時不影響 Claude Code 運作
