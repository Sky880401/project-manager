#!/usr/bin/env python3
"""Hermes worker —— 跑在 bmo 上，輪詢後端佇列，用 Claude Code headless 執行任務。

設定（環境變數）：
  HERMES_API_BASE      後端網址，預設 https://lxc.tail92862c.ts.net
  HERMES_WORKER_TOKEN  與後端相同的 worker token（建議務必設定）
  HERMES_WORKSPACE     Claude 執行的工作目錄，預設 ~/hermes-workspace
  HERMES_POLL_SECONDS  輪詢間隔秒數，預設 10
  HERMES_TIMEOUT       單一任務逾時秒數，預設 600
  HERMES_CLAUDE_BIN    claude 執行檔路徑，預設用 PATH 中的 claude
  HERMES_CLAUDE_ARGS   附加在 claude 後的旗標（空白分隔），例如 "--permission-mode acceptEdits"

執行：
  python3 scripts/hermes_worker.py
"""
import os
import sys
import time
import shlex
import subprocess
from pathlib import Path

import requests

API_BASE = os.getenv("HERMES_API_BASE", "https://lxc.tail92862c.ts.net").rstrip("/")
TOKEN = os.getenv("HERMES_WORKER_TOKEN", "")
WORKSPACE = os.path.expanduser(os.getenv("HERMES_WORKSPACE", "~/hermes-workspace"))
POLL = int(os.getenv("HERMES_POLL_SECONDS", "10"))
TIMEOUT = int(os.getenv("HERMES_TIMEOUT", "600"))
CLAUDE_BIN = os.getenv("HERMES_CLAUDE_BIN", "claude")
CLAUDE_ARGS = shlex.split(os.getenv("HERMES_CLAUDE_ARGS", ""))

HEADERS = {"X-Worker-Token": TOKEN} if TOKEN else {}


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def run_claude(prompt: str) -> tuple[str | None, str | None]:
    """回傳 (result, error)。"""
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)
    cmd = [CLAUDE_BIN, *CLAUDE_ARGS, "-p", prompt]
    try:
        proc = subprocess.run(
            cmd, cwd=WORKSPACE, capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, f"逾時（>{TIMEOUT}s）"
    except FileNotFoundError:
        return None, f"找不到 claude 執行檔：{CLAUDE_BIN}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return None, f"claude exit {proc.returncode}\n{err or out}"
    return (out or "(無輸出)"), None


def process(job):
    jid = job["id"]
    # claim
    r = requests.post(f"{API_BASE}/api/hermes/jobs/{jid}/claim", headers=HEADERS, timeout=15)
    if r.status_code != 200:
        log(f"claim #{jid} 失敗：{r.status_code} {r.text[:120]}")
        return
    log(f"▶ 執行 #{jid}：{job['prompt'][:60]!r}")
    result, error = run_claude(job["prompt"])
    payload = {"result": result, "error": error}
    requests.post(f"{API_BASE}/api/hermes/jobs/{jid}/complete", headers=HEADERS, json=payload, timeout=30)
    log(f"✓ 完成 #{jid}" + (f"（錯誤：{error[:60]}）" if error else ""))


def main():
    if not TOKEN:
        log("⚠️ 未設定 HERMES_WORKER_TOKEN，僅適合本機測試")
    log(f"Hermes worker 啟動：API={API_BASE} workspace={WORKSPACE} poll={POLL}s")
    while True:
        try:
            r = requests.get(f"{API_BASE}/api/hermes/jobs/queued", headers=HEADERS, timeout=15)
            if r.status_code == 200:
                for job in r.json():
                    process(job)
            else:
                log(f"輪詢失敗：{r.status_code} {r.text[:120]}")
        except Exception as e:
            log(f"輪詢例外：{e}")
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("worker 結束")
        sys.exit(0)
