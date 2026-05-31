#!/usr/bin/env python3
"""Claude Code hook — Notification
捕捉 Claude Code 的通知訊息，偵測 rate limit
"""
import sys
import json
import httpx
import os
from datetime import datetime, timezone, timedelta

API_URL = os.getenv("PM_API_URL", "https://lxc.tail92862c.ts.net")

try:
    hook_input = json.load(sys.stdin)
except Exception:
    hook_input = {}

message = str(hook_input.get("message", "")).lower()
title = str(hook_input.get("title", "")).lower()
combined = message + " " + title

# 偵測是否為 rate limit 通知
is_rate_limited = any(word in combined for word in [
    "rate limit", "usage limit", "limit reached", "too many",
    "被限速", "limit", "quota"
])

if is_rate_limited:
    try:
        reset_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        httpx.post(
            f"{API_URL}/api/claude/rate-limit",
            json={
                "reset_at": reset_at,
                "message": hook_input.get("message", "Rate limit detected via notification hook"),
            },
            timeout=3,
        )
    except Exception:
        pass
