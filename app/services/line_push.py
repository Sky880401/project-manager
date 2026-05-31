from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, PushMessageRequest, TextMessage
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import os, certifi, logging

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

logger = logging.getLogger(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)


def get_all_user_ids(db: Session) -> list[str]:
    from app.models.claude_usage import LineUser
    return [u.user_id for u in db.query(LineUser).all()]


def push_to_all(db: Session, text: str):
    user_ids = get_all_user_ids(db)
    if not user_ids:
        logger.warning("No LINE users to push to")
        return
    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        for uid in user_ids:
            try:
                api.push_message(PushMessageRequest(
                    to=uid,
                    messages=[TextMessage(text=text)]
                ))
                logger.info(f"Push sent to {uid}")
            except Exception as e:
                logger.error(f"Push failed for {uid}: {e}")


def check_and_notify_rate_limit(db: Session):
    from app.models.claude_usage import RateLimitEvent
    from app.models.project import Task

    # 通知：新的 rate limit 事件
    unnotified = db.query(RateLimitEvent).filter(
        RateLimitEvent.is_resolved == False,
        RateLimitEvent.notified == False,
    ).first()

    if unnotified:
        now = datetime.now(timezone.utc)
        reset = unnotified.reset_at
        if reset:
            reset = reset.replace(tzinfo=timezone.utc) if reset.tzinfo is None else reset
            mins = max(0, int((reset - now).total_seconds() / 60))
            reset_str = f"約 {mins} 分鐘後（{reset.strftime('%H:%M')} UTC）"
        else:
            reset_str = "時間未知"

        push_to_all(db, f"🔴 Claude 被限速了\n⏱ 預計 {reset_str} 恢復\n\n有任務要加入待續接佇列嗎？\n輸入「儀表板」開啟管理介面")
        unnotified.notified = True
        db.commit()

    # 通知：rate limit 解除
    resolved_unnotified = db.query(RateLimitEvent).filter(
        RateLimitEvent.is_resolved == True,
        RateLimitEvent.resolved_notified == False,
    ).first()

    if resolved_unnotified:
        waiting = db.query(Task).filter(
            Task.status == "paused",
            Task.deleted_at.is_(None)
        ).count()

        msg = "🟢 Claude 可以用了！"
        if waiting > 0:
            msg += f"\n📋 有 {waiting} 個暫停中的任務等待續接"
        msg += "\n\n輸入「狀態」查看詳情"

        push_to_all(db, msg)
        resolved_unnotified.resolved_notified = True
        db.commit()
