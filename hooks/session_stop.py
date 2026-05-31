#!/usr/bin/env python3
"""Claude Code hook — Stop
當 Claude Code session 結束時，回報狀態到 API
"""
import sys
import json
import httpx
import os
from datetime import datetime, timezone

API_URL = os.getenv("PM_API_URL", "http://localhost:8000")

try:
    hook_input = json.load(sys.stdin)
except Exception:
    hook_input = {}

session_id = hook_input.get("session_id", "unknown")
stop_reason = hook_input.get("stop_reason", "normal")

# 偵測是否為 rate limit
is_rate_limited = "rate" in str(stop_reason).lower() or "limit" in str(stop_reason).lower()
exit_reason = "rate_limited" if is_rate_limited else "normal"

try:
    httpx.post(
        f"{API_URL}/api/claude/sessions/end",
        json={
            "session_id": session_id,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "exit_reason": exit_reason,
            "turns": hook_input.get("num_turns", 0),
        },
        timeout=3,
    )

    if is_rate_limited:
        from datetime import timedelta
        reset_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        httpx.post(
            f"{API_URL}/api/claude/rate-limit",
            json={
                "reset_at": reset_at,
                "message": str(stop_reason),
            },
            timeout=3,
        )
except Exception:
    pass
