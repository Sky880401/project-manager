# Project Manager — Claude Code 操作守則

## 絕對禁止
- 不可刪除 `project_manager.db` 或 `data/` 目錄下的任何檔案
- 不可執行 `rm -rf`、`DROP TABLE`、`DELETE FROM` 等破壞性指令
- 不可刪除 `alembic/versions/` 下的 migration 檔案
- 不可強制 push (`git push --force`) 到 main 分支
- 不可修改 `.env` 中的 `DATABASE_URL`（會導致資料遺失）

## 資料庫變更規則
- 所有 schema 變更必須透過 Alembic migration：
  ```bash
  alembic revision --autogenerate -m "描述變更"
  alembic upgrade head
  ```
- 不可直接用 `Base.metadata.create_all()` 覆蓋既有 schema

## 刪除操作
- 專案、任務、Milestone 的刪除已實作軟刪除（soft delete）
- 實際上是設定 `deleted_at`，資料不會真正消失
- 若需永久刪除，需使用者明確確認後才能操作

## 備份位置
- 自動備份每小時執行，存放於 `backups/` 目錄
- 手動備份：`python scripts/backup_db.py`

## 開發環境啟動
```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 專案結構
```
app/
  main.py          # FastAPI 入口，勿隨意新增 middleware
  database.py      # DB 連線，勿修改
  models/          # SQLAlchemy models
  routes/          # API 路由
  services/        # 業務邏輯
  schemas/         # Pydantic schemas
static/            # 前端 HTML（無框架）
hooks/             # Claude Code hook 腳本
alembic/           # DB migration
backups/           # 自動備份（不進 git）
scripts/           # 工具腳本
```

## ⭐ 前端介面對應（改 UI 前務必看清楚改哪個檔）
使用者**幾乎都在 LINE 上操作**，所以多數 UI 需求指的是 LIFF：

| 檔案 | 路由 | 是什麼 | 何時改它 |
|---|---|---|---|
| `static/liff.html` | `/liff` | **LINE 內的 LIFF 介面（主要！）** | 使用者說「在 LINE 上 / 手機 / LIFF」看到的任何 UI 需求 |
| `static/index.html` | `/dashboard` | 網頁版儀表板（桌機瀏覽器用，較少用） | 明確指定「網頁版 / dashboard」時才改 |
| `static/api-explorer.html` | `/api-explorer` | API 測試頁 | 幾乎不動 |

- 兩個 HTML 有**各自獨立**的 JS（如各自的 `sortTasks`、`showProjectDetail`），改了一個**不會**自動同步到另一個。
- 接到 UI 需求若沒指明檔案，**預設改 `liff.html`**；若兩邊都該一致，要明講「兩個都改」。
