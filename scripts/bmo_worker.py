#!/usr/bin/env python3
"""BMO worker —— 跑在 bmo 上，輪詢後端佇列，用 Claude Code headless 執行任務。

每個新任務會開一個獨立 git 分支（bmo/job-<id>-<時間>）再執行；review 後續任務
（帶 branch）則沿用同一分支繼續修改。跑完把變更提交在分支、切回原分支，分支保留
供 review，並把 branch / diff 回報後端。

設定（環境變數）：
  BMO_API_BASE      後端網址，預設 https://lxc.tail92862c.ts.net
  BMO_WORKER_TOKEN  與後端相同的 worker token
  BMO_WORKSPACE     Claude 執行的工作目錄（git repo），預設 ~/project-manager
  BMO_POLL_SECONDS  輪詢間隔秒數，預設 10
  BMO_TIMEOUT       單一任務逾時秒數，預設 1200
  BMO_CLAUDE_BIN    claude 執行檔路徑，預設用 PATH 中的 claude
  BMO_CLAUDE_ARGS   附加在 claude 後的旗標（空白分隔）

執行：
  python3 scripts/bmo_worker.py
"""
import os
import sys
import time
import shlex
import subprocess
from pathlib import Path

import requests

API_BASE = os.getenv("BMO_API_BASE", "https://lxc.tail92862c.ts.net").rstrip("/")
TOKEN = os.getenv("BMO_WORKER_TOKEN", "")
WORKSPACE = os.path.expanduser(os.getenv("BMO_WORKSPACE", "~/project-manager"))
# 這個 worker 負責的 workspace key（對應後端 BMO_WORKSPACES），只認領同 key 的 job
WORKSPACE_KEY = os.getenv("BMO_WORKSPACE_KEY", "project-manager")
POLL = int(os.getenv("BMO_POLL_SECONDS", "10"))
TIMEOUT = int(os.getenv("BMO_TIMEOUT", "1200"))
CLAUDE_BIN = os.getenv("BMO_CLAUDE_BIN", "claude")
CLAUDE_ARGS = shlex.split(os.getenv("BMO_CLAUDE_ARGS", ""))
# 多代理：依 job.agent 載入對應角色的 system prompt（<AGENT_DIR>/<agent>.md），
# 用 --append-system-prompt 疊加。找不到檔就當預設 coding（不疊加），完全向後相容。
AGENT_DIR = os.path.expanduser(os.getenv("BMO_AGENT_DIR", "/root/project-manager/agents"))
# 部署參數（不同 workspace 各異）：合併/推送的分支、部署目標 ssh host、遠端部署指令
BASE_BRANCH = os.getenv("BMO_BASE_BRANCH", "main")
DEPLOY_SSH = os.getenv("BMO_DEPLOY_SSH", "lxc")
DEPLOY_CMD = os.getenv(
    "BMO_DEPLOY_CMD",
    "cd ~/project-manager && git pull --ff-only && "
    "venv/bin/alembic upgrade head && systemctl restart project-manager && "
    "sleep 2 && systemctl is-active project-manager",
)
# 本地用來檢查/合併 alembic head 的執行檔（防呆用，避免多分支各產 migration 撞多重 head）
ALEMBIC_BIN = os.getenv("BMO_ALEMBIC_BIN", os.path.join(WORKSPACE, "venv/bin/alembic"))
# 部署若動到 worker 自身的 code/plist，需重載 launchd 才會生效（KeepAlive 不會因改碼重載）。
# 路徑檔名片段命中就視為「worker 改動」；逗號分隔。
WORKER_RELOAD_PATHS = [s for s in os.getenv(
    "BMO_WORKER_RELOAD_PATHS", "scripts/bmo_worker.py").split(",") if s.strip()]
# 要重載的 launchd plist（兩個 worker 共用同一份 code，預設都重載）；逗號分隔，~ 會展開。
WORKER_PLISTS = [os.path.expanduser(s.strip()) for s in os.getenv(
    "BMO_WORKER_PLISTS",
    "~/Library/LaunchAgents/com.bmo.worker.plist,"
    "~/Library/LaunchAgents/com.bmo.worker2.plist").split(",") if s.strip()]

HEADERS = {"X-Worker-Token": TOKEN} if TOKEN else {}


def schedule_worker_reload():
    """部署動到 worker code 時呼叫：開一個脫離本進程的子程序，延遲數秒後重載兩個
    launchd worker。延遲是為了讓本次 deploy job 先把 complete 回報出去，重載會把
    目前這個 worker 進程一起換掉，新進程啟動那輪就吃到新 code。"""
    cmds = "; ".join(f'launchctl unload "{p}" 2>/dev/null; launchctl load "{p}"'
                     for p in WORKER_PLISTS)
    script = f"sleep 8; {cmds}"
    try:
        subprocess.Popen(["bash", "-c", script], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"🔁 偵測到 worker 改動，已排程 {len(WORKER_PLISTS)} 個 worker 於回報後重載")
        return True
    except Exception as e:
        log(f"排程 worker 重載失敗：{e}")
        return False


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def git(*args) -> str:
    p = subprocess.run(["git", "-C", WORKSPACE, *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def is_git_repo() -> bool:
    return os.path.isdir(os.path.join(WORKSPACE, ".git"))


# 回報格式守則：附加到每個 prompt，強制 BMO 用 LINE 看得懂的簡短純文字回報。
# 使用者在 LINE/LIFF 看不到 markdown 粗體、項目符號、表格、縮排，且討厭落落長。
OUTPUT_GUIDE = (
    "\n\n---\n"
    "【回報格式 · 務必遵守】完成後用繁體中文「純文字」回報，控制在 5 行內：\n"
    "第 1 行：『✅ 完成：<任務一句話>』或『❌ 未完成：<原因一句話>』。\n"
    "接著 1～3 句講結論／你做了什麼／使用者要不要做什麼。\n"
    "嚴禁使用 markdown：不要 **粗體**、不要 - 或 * 項目符號、不要表格、不要縮排、不要程式碼框。"
    "這些在 LINE 上無法顯示。要分點就用『1. 2. 3.』開頭的短句。"
)


def agent_system_prompt(agent: str | None) -> str | None:
    """讀 <AGENT_DIR>/<agent>.md 當該角色的 system prompt；找不到回 None（預設 coding 行為）。"""
    if not agent:
        return None
    safe = "".join(c for c in agent if c.isalnum() or c in "-_")  # 防路徑穿越
    path = os.path.join(AGENT_DIR, f"{safe}.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def run_claude(prompt: str, agent: str | None = None) -> tuple[str | None, str | None, bool]:
    """回傳 (result, error, timed_out)。timed_out=True 代表是逾時被砍，
    但 claude 在逾時前可能已對檔案做出變更，呼叫端需檢查 git 狀態。"""
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)
    sys_args = []
    sp = agent_system_prompt(agent)
    if sp:
        sys_args = ["--append-system-prompt", sp]
        log(f"  使用 agent 角色：{agent}")
    cmd = [CLAUDE_BIN, *CLAUDE_ARGS, *sys_args, "-p", prompt + OUTPUT_GUIDE]
    try:
        proc = subprocess.run(cmd, cwd=WORKSPACE, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"逾時（>{TIMEOUT}s）", True
    except FileNotFoundError:
        return None, f"找不到 claude 執行檔：{CLAUDE_BIN}", False
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return None, f"claude exit {proc.returncode}\n{err or out}", False
    return (out or "(無輸出)"), None, False


def run_job_on_branch(prompt: str, job_id: int, existing_branch: str | None,
                       agent: str | None = None):
    """回傳 (result, error, branch, diff)。

    existing_branch 有值＝review 後續任務，沿用同分支；否則開新分支。
    """
    if not is_git_repo():
        result, error, _ = run_claude(prompt, agent)
        return result, error, None, None

    try:
        orig = git("rev-parse", "--abbrev-ref", "HEAD")
    except Exception as e:
        result, error, _ = run_claude(prompt, agent)
        return result, error, None, f"取得分支失敗：{e}"

    # 新任務開分支；後續任務沿用既有分支
    resumed_note = ""
    if existing_branch:
        branch = existing_branch
        try:
            git("checkout", branch)
        except Exception:
            # 既有分支不在本機：可能只在遠端，先試 fetch 後再 checkout
            try:
                git("fetch", "origin", branch)
                git("checkout", branch)
            except Exception:
                # 分支已被合併刪除（deploy 會把整鏈 archived+刪分支）。
                # 舊變更早已併入主線，從目前主線開新分支接續即可。
                branch = f"bmo/job-{job_id}-{int(time.time())}"
                try:
                    git("checkout", "-b", branch)
                except Exception as e:
                    return None, f"建立接續分支失敗：{e}", None, None
                resumed_note = (f"\n\nℹ️ 原分支 `{existing_branch}` 已被合併刪除，"
                                f"舊變更已在主線，改從 `{orig}` 開新分支 `{branch}` 接續。")
        base = orig  # diff 相對於主線
    else:
        # 新任務一律從「最新的主線」開分支，避免接到別的 task 還沒合併的 review 分支上，
        # 縮短並行時間、減少日後合併衝突（對應同時作業疑慮的建議做法）。
        start_point = BASE_BRANCH
        try:
            git("fetch", "origin", BASE_BRANCH)
            start_point = f"origin/{BASE_BRANCH}"
        except Exception:
            # 抓不到遠端（離線等）就退回本機主線；本機主線抓不到再退回目前分支
            try:
                git("rev-parse", "--verify", BASE_BRANCH)
                start_point = BASE_BRANCH
            except Exception:
                start_point = orig
        branch = f"bmo/job-{job_id}-{int(time.time())}"
        try:
            git("checkout", "-b", branch, start_point)
        except Exception as e:
            return None, f"建立分支失敗：{e}", None, None
        base = start_point  # diff 相對於開分支的最新主線

    result, error, timed_out = run_claude(prompt, agent)

    diff = None
    info = resumed_note
    committed = False
    try:
        changed = git("status", "--porcelain")
        if changed.strip():
            git("add", "-A")
            git("commit", "-m", f"BMO job #{job_id}", "-m", prompt[:200])
            committed = True
        # 不論這輪有無新變更，回報分支相對主線的完整 diff
        diff = git("diff", f"{base}...{branch}")
        if committed:
            stat = git("diff", "--stat", f"{base}...{branch}")
            info += f"\n\n🌿 變更已提交於分支 `{branch}`（已切回 {orig} 待 review）\n{stat}"
        else:
            info += f"\n\n🌿 這一輪未產生新的檔案變更（分支 `{branch}` 維持原樣）"
        git("checkout", orig)
    except Exception as e:
        try:
            git("checkout", orig)
        except Exception:
            pass
        info += f"\n\n⚠️ 分支處理出錯：{e}"

    # 逾時但已有 commit：claude 雖被砍，變更已保住，視為完成（避免誤報為錯誤嚇人）
    if error and timed_out and committed:
        result = (f"⏱️ 已達逾時上限（{TIMEOUT}s）被中止，但 claude 在中止前已提交變更，"
                  f"請 review 分支內容是否完整。" + info)
        return result, None, branch, diff

    if error:
        return None, (error + info), branch, diff
    return (result or "") + info, None, branch, diff


def ensure_single_alembic_head() -> str | None:
    """部署防呆：若合併後出現多個 alembic head，自動 merge heads 並 commit。

    多個 BMO 任務各開分支、各自產 migration 接在同一父 revision 上時會形成並行 head，
    LXC 端 `alembic upgrade head` 會以 'Multiple head revisions are present' 失敗。
    在 push 前就地收斂成單一 head，避免部署炸掉。回傳一句說明（有合併時）或 None。
    """
    # 非 alembic 專案（如 stock_quant 沒有 alembic/ 目錄）直接略過，不要硬跑 alembic
    if not os.path.isdir(os.path.join(WORKSPACE, "alembic")):
        return None
    alembic = ALEMBIC_BIN if os.path.exists(ALEMBIC_BIN) else "alembic"
    try:
        p = subprocess.run([alembic, "heads"], cwd=WORKSPACE, capture_output=True, text=True)
    except FileNotFoundError:
        return None  # 找不到 alembic 執行檔（非本專案需求）→ 放行
    if p.returncode != 0:
        return None  # 查不動就放行，交給遠端部署照常處理／回報
    heads = [ln for ln in p.stdout.splitlines() if "(head)" in ln]
    if len(heads) <= 1:
        return None
    m = subprocess.run([alembic, "merge", "heads", "-m", "auto-merge bmo migration heads"],
                       cwd=WORKSPACE, capture_output=True, text=True)
    if m.returncode != 0:
        raise RuntimeError(f"alembic merge heads 失敗：{(m.stderr or m.stdout).strip()[:200]}")
    git("add", "alembic/versions")
    git("commit", "-m", "chore(alembic): auto-merge migration heads (BMO deploy 防呆)")
    return f"⚙️ 偵測到 {len(heads)} 個 alembic head，已自動 merge 為單一 head"


def deploy_branch(branch: str):
    """把 branch 合併進 main、push，並 ssh 到 LXC 部署。回傳 (result, error, branch, None)。"""
    if not is_git_repo():
        return None, "workspace 不是 git repo", branch, None
    try:
        git("checkout", BASE_BRANCH)
        git("fetch", "origin")
        try:
            git("merge", "--ff-only", f"origin/{BASE_BRANCH}")  # 先跟上遠端
        except Exception:
            pass
    except Exception as e:
        return None, f"切到 {BASE_BRANCH} 失敗：{e}", branch, None
    # 合併分支
    try:
        git("merge", "--no-ff", "--no-edit", branch)
    except Exception as e:
        try:
            git("merge", "--abort")
        except Exception:
            pass
        return None, f"合併失敗（可能有衝突，已 abort）：{e}", branch, None
    # 合併後立即看這次併入了哪些檔案（HEAD 是 merge commit，相對第一父= main 舊 tip）
    worker_changed = False
    try:
        changed = git("diff", "--name-only", "HEAD^1", "HEAD").splitlines()
        worker_changed = any(any(key in f for key in WORKER_RELOAD_PATHS) for f in changed)
    except Exception:
        pass
    # 防呆：合併後若出現多個 alembic head，就地自動 merge 成單一 head 再 push
    try:
        heads_note = ensure_single_alembic_head()
    except Exception as e:
        return None, f"alembic 多重 head 自動修復失敗：{e}", branch, None
    # push
    p = subprocess.run(["git", "-C", WORKSPACE, "push", "origin", BASE_BRANCH], capture_output=True, text=True)
    if p.returncode != 0:
        return None, f"push 失敗：{(p.stderr or p.stdout)[:200]}", branch, None
    # 部署到遠端主機
    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", DEPLOY_SSH, DEPLOY_CMD],
                           capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None, f"{DEPLOY_SSH} 部署逾時", branch, None
    if p.returncode != 0:
        return None, f"{DEPLOY_SSH} 部署失敗：{(p.stderr or p.stdout)[:300]}", branch, None
    # 清掉本機已合併分支
    try:
        git("branch", "-d", branch)
    except Exception:
        pass
    # 若這次部署動到 worker 自身 code/plist，排程重載（在 complete 回報後才真正重載）
    reload_note = ""
    if worker_changed and schedule_worker_reload():
        reload_note = "\n🔁 已自動重載 BMO worker（新 code 數秒後生效）"
    head_line = f"\n{heads_note}" if heads_note else ""
    return (f"🚀 已合併 `{branch}` 進 main 並部署上線{head_line}{reload_note}\n"
            f"{p.stdout.strip()[-200:]}", None, branch, None)


def auto_dispatch():
    """請後端把標注 auto_solve 的待辦任務自動轉成 queued job（本 worker 的 workspace）。

    後端 /auto-dispatch 會挑 status=todo 且 auto_solve=True、且沒有未封存 job 的任務，
    建立 job 並把任務標為 in_progress，避免重複派工。沒有符合的任務時回傳空陣列。
    """
    try:
        r = requests.post(f"{API_BASE}/api/bmo/auto-dispatch",
                          params={"workspace": WORKSPACE_KEY}, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            n = len(r.json())
            if n:
                log(f"🤖 auto-solve：自動派工 {n} 個任務")
        else:
            log(f"auto-dispatch 失敗：{r.status_code} {r.text[:120]}")
    except Exception as e:
        log(f"auto-dispatch 例外：{e}")


def process(job):
    jid = job["id"]
    r = requests.post(f"{API_BASE}/api/bmo/jobs/{jid}/claim", headers=HEADERS, timeout=15)
    if r.status_code != 200:
        log(f"claim #{jid} 失敗：{r.status_code} {r.text[:120]}")
        return
    log(f"▶ 執行 #{jid}（{job.get('kind','task')}）：{job['prompt'][:50]!r}")
    if job.get("kind") == "deploy":
        result, error, branch, diff = deploy_branch(job.get("branch"))
    else:
        result, error, branch, diff = run_job_on_branch(job["prompt"], jid, job.get("branch"),
                                                         job.get("agent"))
    requests.post(f"{API_BASE}/api/bmo/jobs/{jid}/complete", headers=HEADERS,
                  json={"result": result, "error": error, "branch": branch, "diff": diff}, timeout=30)
    log(f"✓ 完成 #{jid}" + (f"（錯誤：{error[:60]}）" if error else f"（分支 {branch}）"))


def main():
    if not TOKEN:
        log("⚠️ 未設定 BMO_WORKER_TOKEN，僅適合本機測試")
    log(f"BMO worker 啟動：API={API_BASE} workspace={WORKSPACE} poll={POLL}s")
    while True:
        try:
            auto_dispatch()
            r = requests.get(f"{API_BASE}/api/bmo/jobs/queued",
                             params={"workspace": WORKSPACE_KEY}, headers=HEADERS, timeout=15)
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
