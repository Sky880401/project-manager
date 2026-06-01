#!/usr/bin/env python3
"""建立並套用 LINE 圖文選單（Rich Menu）。

目的：BMO 執行任務時會洗版聊天記錄，導致「開啟儀表板」的連結被往上推、找不到。
圖文選單永遠固定在輸入框正下方，不會被任何訊息推走。

選單分兩格：
  左：📊 開啟儀表板 → 直接開 LIFF
  右：🔴 紅燈任務   → 送出「紅燈任務」訊息，bot 即時列出高優先未完成任務

用法：
  source venv/bin/activate
  python scripts/setup_richmenu.py            # 建立＋上傳圖片＋設為預設
  python scripts/setup_richmenu.py --list     # 列出現有 rich menu
  python scripts/setup_richmenu.py --clear     # 刪除全部 rich menu

需要環境變數：LINE_CHANNEL_ACCESS_TOKEN，可選 LIFF_ID。
"""
import os
import sys
import io
import certifi
from dotenv import load_dotenv

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

from PIL import Image, ImageDraw, ImageFont
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    RichMenuRequest, RichMenuArea, RichMenuBounds, RichMenuSize,
    URIAction, MessageAction,
)

load_dotenv()

ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LIFF_ID = os.getenv("LIFF_ID", "2010243777-kq9FJSJT")
DASHBOARD_URL = f"https://liff.line.me/{LIFF_ID}"

# LINE 規定的尺寸之一（半高）
WIDTH, HEIGHT = 2500, 843

if not ACCESS_TOKEN:
    print("❌ 缺少 LINE_CHANNEL_ACCESS_TOKEN，請先設定 .env")
    sys.exit(1)

configuration = Configuration(access_token=ACCESS_TOKEN)


def _load_font(size):
    """盡量找一個支援中文的字型，找不到就用預設。"""
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def build_image() -> bytes:
    img = Image.new("RGB", (WIDTH, HEIGHT), "#ffffff")
    d = ImageDraw.Draw(img)
    half = WIDTH // 2

    # 左格（儀表板）綠底、右格（紅燈）紅底
    d.rectangle([0, 0, half, HEIGHT], fill="#06C755")
    d.rectangle([half, 0, WIDTH, HEIGHT], fill="#e53935")
    # 中間分隔線
    d.rectangle([half - 3, 0, half + 3, HEIGHT], fill="#ffffff")

    # 註：PIL 無法渲染彩色 emoji，選單圖以中文標籤為主
    label_font = _load_font(140)

    def centered(text, font, cx, cy, fill="#ffffff"):
        box = d.textbbox((0, 0), text, font=font)
        w, h = box[2] - box[0], box[3] - box[1]
        d.text((cx - w / 2 - box[0], cy - h / 2 - box[1]), text, font=font, fill=fill)

    centered("開啟儀表板", label_font, half / 2, HEIGHT / 2)
    centered("紅燈任務", label_font, half + half / 2, HEIGHT / 2)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def list_menus():
    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        res = api.get_rich_menu_list()
        if not res.richmenus:
            print("（沒有任何 rich menu）")
            return
        for m in res.richmenus:
            print(f"- {m.rich_menu_id}  name={m.name}")


def clear_menus():
    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        for m in api.get_rich_menu_list().richmenus:
            api.delete_rich_menu(m.rich_menu_id)
            print(f"🗑 已刪除 {m.rich_menu_id}")


def setup():
    half = WIDTH // 2
    rich_menu = RichMenuRequest(
        size=RichMenuSize(width=WIDTH, height=HEIGHT),
        selected=True,
        name="專案管理主選單",
        chat_bar_text="選單",
        areas=[
            RichMenuArea(
                bounds=RichMenuBounds(x=0, y=0, width=half, height=HEIGHT),
                action=URIAction(label="開啟儀表板", uri=DASHBOARD_URL),
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=half, y=0, width=WIDTH - half, height=HEIGHT),
                action=MessageAction(label="紅燈任務", text="紅燈任務"),
            ),
        ],
    )

    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        blob = MessagingApiBlob(client)

        rich_menu_id = api.create_rich_menu(rich_menu).rich_menu_id
        print(f"✅ 已建立 rich menu：{rich_menu_id}")

        blob.set_rich_menu_image(
            rich_menu_id=rich_menu_id,
            body=bytearray(build_image()),
            _headers={"Content-Type": "image/png"},
        )
        print("✅ 已上傳選單圖片")

        api.set_default_rich_menu(rich_menu_id)
        print("✅ 已設為預設選單（所有人進聊天室即可見）")


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_menus()
    elif "--clear" in sys.argv:
        clear_menus()
    else:
        setup()
