import anthropic
import os
import json
from sqlalchemy.orm import Session
from app.models.project import Project, Task, Milestone
from app.models.claude_usage import RateLimitEvent, ResumeQueue
from app.models.conversation import ApiUsage
from app.services.claude_monitor import get_claude_status

# claude-sonnet-4-6 定價（USD per million tokens）
PRICE_INPUT_PER_M = 3.0
PRICE_OUTPUT_PER_M = 15.0


def calc_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000 * PRICE_INPUT_PER_M +
            output_tokens / 1_000_000 * PRICE_OUTPUT_PER_M)


def get_usage_summary(db: Session) -> dict:
    from datetime import datetime, timezone
    from sqlalchemy import func as sqlfunc
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def query_period(start):
        rows = db.query(ApiUsage).filter(ApiUsage.created_at >= start).all()
        return {
            "input_tokens": sum(r.input_tokens for r in rows),
            "output_tokens": sum(r.output_tokens for r in rows),
            "cost_usd": sum(r.cost_usd for r in rows),
            "calls": len(rows),
        }

    return {
        "today": query_period(today_start),
        "month": query_period(month_start),
    }

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """你是 Sky 的個人助理，專門幫助管理專案和任務。你可以使用工具來查詢和操作專案資料。

回覆請使用繁體中文，保持簡潔。你可以：
- 查詢所有專案和任務
- 新增專案或任務
- 更新任務狀態
- 查看 Claude 使用狀況

請主動使用工具取得最新資料，不要猜測。"""

TOOLS = [
    {
        "name": "list_projects",
        "description": "列出所有專案，包含任務進度",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_project_detail",
        "description": "查看單一專案的詳細資訊，包含所有任務和 milestone",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "專案 ID"}
            },
            "required": ["project_id"]
        }
    },
    {
        "name": "create_project",
        "description": "新增一個專案",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "專案名稱"},
                "description": {"type": "string", "description": "專案說明"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "create_task",
        "description": "在專案裡新增一個任務",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "專案 ID"},
                "title": {"type": "string", "description": "任務名稱"},
                "milestone_id": {"type": "integer", "description": "歸屬的 Milestone ID（選填）"}
            },
            "required": ["project_id", "title"]
        }
    },
    {
        "name": "update_task_status",
        "description": "更新任務狀態",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "task_id": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "completed"],
                    "description": "todo=待做, in_progress=進行中, completed=完成"
                }
            },
            "required": ["project_id", "task_id", "status"]
        }
    },
    {
        "name": "get_claude_status",
        "description": "查看 Claude 目前使用狀況、是否被限速、待續接任務數量",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "add_resume_queue",
        "description": "把一個任務加入待續接佇列",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "任務描述"},
                "checkpoint": {"type": "string", "description": "中斷點說明"},
                "priority": {"type": "integer", "description": "優先度 0=一般 1=優先"}
            },
            "required": ["description"]
        }
    }
]


def execute_tool(tool_name: str, tool_input: dict, db: Session) -> str:
    try:
        if tool_name == "list_projects":
            projects = db.query(Project).filter(Project.deleted_at.is_(None)).all()
            result = []
            for p in projects:
                tasks = [t for t in p.tasks if not t.deleted_at]
                done = sum(1 for t in tasks if t.status == "completed")
                result.append({
                    "id": p.id, "name": p.name,
                    "status": p.status,
                    "tasks": f"{done}/{len(tasks)}",
                    "description": p.description or ""
                })
            return json.dumps(result, ensure_ascii=False)

        elif tool_name == "get_project_detail":
            p = db.query(Project).filter(
                Project.id == tool_input["project_id"],
                Project.deleted_at.is_(None)
            ).first()
            if not p:
                return "找不到此專案"
            tasks = [t for t in p.tasks if not t.deleted_at]
            milestones = [m for m in p.milestones if not m.deleted_at]
            return json.dumps({
                "id": p.id, "name": p.name, "status": p.status,
                "description": p.description,
                "milestones": [{"id": m.id, "name": m.name, "status": m.status} for m in milestones],
                "tasks": [{"id": t.id, "title": t.title, "status": t.status, "milestone_id": t.milestone_id} for t in tasks]
            }, ensure_ascii=False)

        elif tool_name == "create_project":
            p = Project(name=tool_input["name"], description=tool_input.get("description"))
            db.add(p)
            db.commit()
            db.refresh(p)
            return f"專案「{p.name}」已建立（ID: {p.id}）"

        elif tool_name == "create_task":
            task = Task(
                project_id=tool_input["project_id"],
                title=tool_input["title"],
                milestone_id=tool_input.get("milestone_id")
            )
            db.add(task)
            db.commit()
            return f"任務「{task.title}」已新增"

        elif tool_name == "update_task_status":
            task = db.query(Task).filter(
                Task.id == tool_input["task_id"],
                Task.project_id == tool_input["project_id"],
                Task.deleted_at.is_(None)
            ).first()
            if not task:
                return "找不到此任務"
            task.status = tool_input["status"]
            db.commit()
            status_label = {"todo": "待做", "in_progress": "進行中", "completed": "完成"}
            return f"任務「{task.title}」已更新為{status_label.get(tool_input['status'], tool_input['status'])}"

        elif tool_name == "get_claude_status":
            s = get_claude_status(db)
            return json.dumps({
                "is_available": s["is_available"],
                "is_rate_limited": s["is_rate_limited"],
                "minutes_until_reset": s["minutes_until_reset"],
                "today_sessions": s["today_sessions"],
                "today_tokens": s["today_tokens"],
                "queue_count": s["queue_count"]
            }, ensure_ascii=False)

        elif tool_name == "add_resume_queue":
            item = ResumeQueue(
                description=tool_input["description"],
                checkpoint=tool_input.get("checkpoint"),
                priority=tool_input.get("priority", 0)
            )
            db.add(item)
            db.commit()
            return f"已加入待續接佇列：{item.description}"

        return "未知工具"
    except Exception as e:
        return f"工具執行失敗：{str(e)}"


def chat_with_claude(messages: list, db: Session) -> str:
    """與 Claude 對話，支援工具呼叫，並記錄 token 用量"""
    total_input = 0
    total_output = 0
    MODEL = "claude-sonnet-4-6"

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages,
    )
    total_input += response.usage.input_tokens
    total_output += response.usage.output_tokens

    while response.stop_reason == "tool_use":
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(block.name, block.input, db)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })

        messages = messages + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tool_results}
        ]
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens

    # 記錄用量
    cost = calc_cost(total_input, total_output)
    db.add(ApiUsage(model=MODEL, input_tokens=total_input, output_tokens=total_output, cost_usd=cost))
    db.commit()

    for block in response.content:
        if hasattr(block, "text"):
            return block.text

    return "（無回覆）"
