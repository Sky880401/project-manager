from fastapi import APIRouter, Request, HTTPException, Depends
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage, FlexMessage, FlexContainer,
    URIAction, MessageAction, TemplateMessage, ButtonsTemplate,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent
from linebot.v3.exceptions import InvalidSignatureError
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import os, json, logging, certifi, ssl

# macOS Python 沒有系統 SSL 憑證，用 certifi 補上
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

logger = logging.getLogger(__name__)

from app.database import get_db, SessionLocal
from app.models.project import Project, Task, Milestone
from app.models.claude_usage import LineUser
from app.models.conversation import Conversation
from app.services.claude_monitor import get_claude_status
from app.services.claude_ai import chat_with_claude, get_usage_summary

load_dotenv()

router = APIRouter(prefix="/line", tags=["line"])

CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)


def messaging_api():
    return MessagingApi(ApiClient(configuration))


# ── Webhook 入口 ────────────────────────────────────────

@router.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    body_str = body.decode()
    try:
        handler.handle(body_str, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        # 記錄錯誤但仍回 200，避免 LINE 不斷重試
        logger.error(f"Webhook handler error: {e}", exc_info=True)
        logger.error(f"Raw body: {body_str[:500]}")
    return {"status": "ok"}


# ── 訊息處理 ────────────────────────────────────────────

@handler.add(FollowEvent)
def on_follow(event):
    reply(event.reply_token, welcome_text())


@handler.add(MessageEvent, message=TextMessageContent)
def on_message(event):
    text = event.message.text.strip()

    # LIFF 完成任務的自動通知 → 給予鼓勵回覆
    if text.startswith("✅ 完成任務："):
        # 格式：✅ 完成任務：[專案名稱] 任務名稱
        content = text.replace("✅ 完成任務：", "").strip()
        reply(event.reply_token, TextMessage(text=f"👏 {content}\n繼續加油！"))
        return

    db = SessionLocal()
    try:
        # 記錄 LINE userId
        user_id = event.source.user_id if hasattr(event.source, 'user_id') else None
        if user_id:
            existing = db.query(LineUser).filter(LineUser.user_id == user_id).first()
            if not existing:
                db.add(LineUser(user_id=user_id))
                db.commit()

        if text in ["專案", "p", "projects"]:
            reply(event.reply_token, projects_flex(db))
        elif text in ["狀態", "claude", "status"]:
            reply(event.reply_token, claude_status_text(db))
        elif text in ["待辦", "todo", "tasks"]:
            reply(event.reply_token, todo_text(db))
        elif text in ["儀表板", "dashboard", "liff"]:
            reply(event.reply_token, dashboard_message())
        elif text in ["用量", "usage", "費用"]:
            reply(event.reply_token, usage_text(db))
        elif text in ["清除對話", "clear", "重置"]:
            db.query(Conversation).filter(Conversation.line_user_id == user_id).delete()
            db.commit()
            reply(event.reply_token, TextMessage(text="✅ 對話記錄已清除"))
        elif text in ["說明", "help", "?"]:
            reply(event.reply_token, TextMessage(text=help_text()))
        elif text.startswith("新增專案 ") or text.startswith("add "):
            name = text.split(" ", 1)[1].strip()
            create_project(db, name, event.reply_token)
        elif text.startswith("完成 "):
            mark_done(db, text.split(" ", 1)[1].strip(), event.reply_token)
        elif text.startswith("/"):
            # / 前綴觸發 Claude AI
            if not user_id:
                reply(event.reply_token, TextMessage(text="無法取得你的 LINE ID"))
                return

            question = text[1:].strip()
            if not question:
                reply(event.reply_token, TextMessage(text="請在 / 後面輸入問題\n例：/幫我列出未完成的任務"))
                return

            history = db.query(Conversation).filter(
                Conversation.line_user_id == user_id
            ).order_by(Conversation.created_at.asc()).limit(20).all()

            messages = [{"role": h.role, "content": h.content} for h in history]
            messages.append({"role": "user", "content": question})

            ai_reply = chat_with_claude(messages, db)

            db.add(Conversation(line_user_id=user_id, role="user", content=question))
            db.add(Conversation(line_user_id=user_id, role="assistant", content=ai_reply))
            db.commit()

            reply(event.reply_token, TextMessage(text=ai_reply))
        else:
            reply(event.reply_token, TextMessage(text="不認識這個指令 🤔\n輸入「說明」查看所有指令"))
    finally:
        db.close()


# ── Reply helper ────────────────────────────────────────

def reply(reply_token, message):
    if not isinstance(message, list):
        message = [message]
    with ApiClient(configuration) as client:
        MessagingApi(client).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=message)
        )


# ── 指令處理 ────────────────────────────────────────────

def welcome_text():
    return TextMessage(text="👋 哈囉！我是你的專案管理助手\n\n輸入「說明」查看所有指令")


def usage_text(db):
    u = get_usage_summary(db)
    t = u["today"]
    m = u["month"]

    def fmt(data):
        return (f"  輸入：{data['input_tokens']:,} tokens\n"
                f"  輸出：{data['output_tokens']:,} tokens\n"
                f"  費用：${data['cost_usd']:.4f} USD\n"
                f"  次數：{data['calls']} 次對話")

    return TextMessage(text=(
        "📊 Claude API 用量\n\n"
        f"今日：\n{fmt(t)}\n\n"
        f"本月：\n{fmt(m)}\n\n"
        "定價（Sonnet 4.6）：\n"
        "  輸入 $3 / 百萬 tokens\n"
        "  輸出 $15 / 百萬 tokens"
    ))


def help_text():
    return (
        "📋 快捷指令：\n\n"
        "專案 — 查看所有專案\n"
        "待辦 — 查看未完成任務\n"
        "狀態 — 查看 Claude 使用狀況\n"
        "儀表板 — 開啟視覺化介面\n"
        "新增專案 名稱 — 建立新專案\n"
        "完成 任務名稱 — 標記任務完成\n"
        "用量 — 查看 API 用量與費用\n"
        "清除對話 — 清除 AI 對話記錄\n"
        "說明 — 顯示此說明\n\n"
        "💬 AI 對話（/ 開頭）：\n"
        "  /幫我列出未完成的任務\n"
        "  /現在 Claude 可以用嗎？"
    )

def dashboard_message():
    return FlexMessage(
        alt_text="開啟專案管理儀表板",
        contents=FlexContainer.from_dict({
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": "📊 專案管理儀表板", "weight": "bold", "size": "lg"},
                    {"type": "text", "text": "點下方按鈕開啟視覺化介面", "size": "sm", "color": "#6a737d", "wrap": True},
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [{
                    "type": "button",
                    "style": "primary",
                    "color": "#06C755",
                    "action": {
                        "type": "uri",
                        "label": "開啟儀表板",
                        "uri": f"https://liff.line.me/2010243777-kq9FJSJT"
                    }
                }]
            }
        })
    )


def projects_flex(db: Session):
    projects = db.query(Project).filter(Project.deleted_at.is_(None)).all()
    if not projects:
        return TextMessage(text="目前沒有任何專案\n輸入「新增專案 名稱」建立第一個")

    bubbles = []
    for p in projects[:10]:
        tasks = [t for t in p.tasks if not t.deleted_at]
        done = sum(1 for t in tasks if t.status == "completed")
        pct = int(done / len(tasks) * 100) if tasks else 0
        status_color = {"active": "#28a745", "paused": "#e36209", "completed": "#6a737d"}.get(p.status, "#6a737d")
        status_label = {"active": "進行中", "paused": "暫停", "completed": "已完成", "archived": "封存"}.get(p.status, p.status)

        bubbles.append({
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": p.name, "weight": "bold", "size": "md", "wrap": True},
                    {
                        "type": "box", "layout": "horizontal", "contents": [
                            {"type": "text", "text": status_label, "size": "xs", "color": status_color, "flex": 1},
                            {"type": "text", "text": f"{done}/{len(tasks)} 任務", "size": "xs", "color": "#6a737d", "align": "end"},
                        ]
                    },
                    {
                        "type": "box", "layout": "vertical", "contents": [
                            {"type": "box", "layout": "vertical", "contents": [
                                {"type": "filler"}
                            ], "width": f"{pct}%", "height": "4px", "backgroundColor": "#28a745"}
                        ], "backgroundColor": "#e1e4e8", "height": "4px", "cornerRadius": "2px"
                    },
                ]
            }
        })

    return FlexMessage(
        alt_text=f"專案列表（共 {len(projects)} 個）",
        contents=FlexContainer.from_dict({
            "type": "carousel",
            "contents": bubbles
        })
    )


def claude_status_text(db: Session):
    s = get_claude_status(db)
    if s["is_rate_limited"]:
        mins = s.get("minutes_until_reset", "?")
        return TextMessage(text=f"🔴 Claude 目前被限速\n⏱ 約 {mins} 分鐘後恢復\n\n今日 sessions：{s['today_sessions']}\n待續接任務：{s['queue_count']} 個")
    else:
        return TextMessage(text=f"🟢 Claude 可正常使用\n\n今日 sessions：{s['today_sessions']}\n今日 tokens：{s['today_tokens']:,}\n待續接任務：{s['queue_count']} 個")


def todo_text(db: Session):
    tasks = db.query(Task).filter(
        Task.deleted_at.is_(None),
        Task.status.in_(["todo", "in_progress"])
    ).limit(15).all()

    if not tasks:
        return TextMessage(text="✅ 目前沒有待辦任務！")

    lines = ["📝 待辦任務：\n"]
    for t in tasks:
        icon = "▶" if t.status == "in_progress" else "○"
        project = db.query(Project).filter(Project.id == t.project_id).first()
        pname = f"[{project.name}] " if project else ""
        lines.append(f"{icon} {pname}{t.title}")

    return TextMessage(text="\n".join(lines))


def create_project(db: Session, name: str, reply_token: str):
    if not name:
        reply(reply_token, TextMessage(text="請輸入專案名稱\n例：新增專案 我的新專案"))
        return
    p = Project(name=name)
    db.add(p)
    db.commit()
    reply(reply_token, TextMessage(text=f"✅ 專案「{name}」已建立！"))


def mark_done(db: Session, title: str, reply_token: str):
    task = db.query(Task).filter(
        Task.title.ilike(f"%{title}%"),
        Task.deleted_at.is_(None),
        Task.status != "completed"
    ).first()
    if not task:
        reply(reply_token, TextMessage(text=f"找不到任務「{title}」\n輸入「待辦」查看所有未完成任務"))
        return
    task.status = "completed"
    db.commit()
    reply(reply_token, TextMessage(text=f"✅ 任務「{task.title}」已標記完成！"))
