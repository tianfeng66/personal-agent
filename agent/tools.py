"""Agent 可调用的工具：定义（给大模型看的 JSON Schema）+ 执行。

只有已配置的集成才会把对应工具交给模型。
"""

import json
from typing import Any, Callable, Dict, List, Optional

from .integrations import IntegrationError, calendar_mac, mail, notify, search
from .storage import Store


def _fn(name: str, desc: str, props: Dict[str, Any], required: List[str] = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required or []},
        },
    }


_PRIORITY = {"type": "string", "enum": ["high", "medium", "low"]}
_DATE = {"type": "string", "description": "YYYY-MM-DD"}
_DATETIME = {"type": "string", "description": "YYYY-MM-DD HH:MM（24小时制，本地时间）"}

BASE_SPECS: List[Dict[str, Any]] = [
    _fn("add_task", "新增一个待办任务",
        {"title": {"type": "string"}, "priority": _PRIORITY, "due_date": _DATE,
         "project": {"type": "string"}, "notes": {"type": "string"}}, ["title"]),
    _fn("list_tasks", "列出任务。status: open(未完成,默认)/todo/doing/done/all",
        {"status": {"type": "string", "enum": ["open", "todo", "doing", "done", "all"]},
         "project": {"type": "string"}, "due_before": _DATE}),
    _fn("update_task", "修改任务（标题/状态/优先级/截止日期/项目/备注）。完成任务时 status=done",
        {"task_id": {"type": "integer"}, "title": {"type": "string"},
         "status": {"type": "string", "enum": ["todo", "doing", "done"]},
         "priority": _PRIORITY, "due_date": _DATE, "project": {"type": "string"},
         "notes": {"type": "string"}}, ["task_id"]),
    _fn("delete_task", "删除任务（仅在用户明确要求删除时使用）",
        {"task_id": {"type": "integer"}}, ["task_id"]),
    _fn("add_note", "保存一条笔记（会议纪要、想法、资料等）",
        {"title": {"type": "string"}, "content": {"type": "string"},
         "tags": {"type": "string", "description": "逗号分隔"}}, ["title", "content"]),
    _fn("search_notes", "按关键词搜索笔记", {"query": {"type": "string"}}, ["query"]),
    _fn("add_worklog", "记录一条工作日志（今天做了什么、花了多久）",
        {"content": {"type": "string"}, "minutes": {"type": "integer"},
         "project": {"type": "string"}, "log_date": _DATE}, ["content"]),
    _fn("daily_summary", "获取某天的工作数据（已完成任务、日志、到期/逾期任务），用于写日报",
        {"date": _DATE}),
    _fn("remember", "记住关于用户的长期信息（偏好、角色、常用项目等）",
        {"content": {"type": "string"}}, ["content"]),
    _fn("forget", "删除一条长期记忆", {"memory_id": {"type": "integer"}}, ["memory_id"]),
    _fn("add_reminder", "设置定时提醒，到时间会弹系统通知（配置了微信则同时推送到微信）",
        {"content": {"type": "string", "description": "提醒内容"}, "remind_at": _DATETIME},
        ["content", "remind_at"]),
    _fn("list_reminders", "列出提醒。status: pending(待提醒,默认)/all",
        {"status": {"type": "string", "enum": ["pending", "all"]}}),
    _fn("cancel_reminder", "取消一个还没触发的提醒",
        {"reminder_id": {"type": "integer"}}, ["reminder_id"]),
]

SEARCH_SPECS = [
    _fn("web_search", "上网搜索资料，返回标题、链接和摘要",
        {"query": {"type": "string"}, "max_results": {"type": "integer", "description": "1-10，默认5"}},
        ["query"]),
    _fn("fetch_url", "打开一个网页并读取正文（用于查看搜索结果的详细内容）",
        {"url": {"type": "string"}}, ["url"]),
]

CALENDAR_SPECS = [
    _fn("calendar_events", "查看 日历里的日程",
        {"start_date": _DATE, "days": {"type": "integer", "description": "查看几天，默认1，最多31"}}),
    _fn("calendar_add_event", "在 日历里新建日程",
        {"title": {"type": "string"}, "start": _DATETIME,
         "end": dict(_DATETIME, description="YYYY-MM-DD HH:MM，默认开始后1小时"),
         "location": {"type": "string"},
         "calendar": {"type": "string", "description": "日历名称，不填用默认可写日历"}},
        ["title", "start"]),
]

EMAIL_SPECS = [
    _fn("list_emails", "列出收件箱最近的邮件（只读）。query 按主题/发件人筛选",
        {"days": {"type": "integer", "description": "最近几天，默认3"},
         "query": {"type": "string"}, "limit": {"type": "integer", "description": "默认10"}}),
    _fn("read_email", "读取一封邮件的正文", {"uid": {"type": "integer"}}, ["uid"]),
    _fn("draft_email", "草拟一封邮件。不会直接发送，用户需在网页「邮件」标签页确认后才发出",
        {"to": {"type": "string", "description": "收件人邮箱，多个用逗号分隔"},
         "subject": {"type": "string"}, "body": {"type": "string"}},
        ["to", "subject", "body"]),
]

WECHAT_SPECS = [
    _fn("send_wechat", "推送一条消息到用户自己的微信（例如日报、重要事项）",
        {"message": {"type": "string"}, "title": {"type": "string"}}, ["message"]),
]


# 自定义智能体按类别授权工具。删除类工具和长期记忆只给总助理。
TOOL_GROUPS: Dict[str, List[str]] = {
    "tasks": ["add_task", "list_tasks", "update_task"],
    "notes": ["add_note", "search_notes"],
    "worklogs": ["add_worklog", "daily_summary"],
    "reminders": ["add_reminder", "list_reminders"],
    "calendar": ["calendar_events", "calendar_add_event"],
    "search": ["web_search", "fetch_url"],
    "email": ["list_emails", "read_email", "draft_email"],
    "wechat": ["send_wechat"],
}


PRESET_ACTIONS = {fn["name"]: fn for fn in (spec["function"] for spec in BASE_SPECS)
                  if fn["name"] in {"add_task", "list_tasks", "add_note", "search_notes", "add_worklog", "daily_summary"}}
PROPOSE = _fn("propose_tool", "按用户要求设计工具草案，需用户在开发面板批准后生效。相同名称可替换旧版。",
              {"name": {"type": "string"}, "description": {"type": "string"},
               "action": {"type": "string", "enum": list(PRESET_ACTIONS)},
               "fixed": {"type": "object", "description": "固定参数对象。其余参数在调用时填写。"}},
              ["name", "description", "action", "fixed"])


class ToolExecutor:
    def specs_for(self, groups: List[str]) -> List[Dict[str, Any]]:
        """按工具类别筛选（只包含当前已配置可用的工具）。"""
        names = {n for g in groups for n in TOOL_GROUPS.get(g, [])}
        return [s for s in self.specs if s["function"]["name"] in names]

    def register(self, spec: Dict[str, Any], handler: Callable[..., Any]) -> None:
        self.specs.append(spec)
        self._handlers[spec["function"]["name"]] = handler

    def __init__(self, store: Store, cfg: Optional[Any] = None):
        self.store = store
        self.cfg = cfg
        s = store
        self.specs: List[Dict[str, Any]] = list(BASE_SPECS) + list(SEARCH_SPECS)
        self.capabilities = ["待办任务", "笔记", "工作日志", "日报", "长期记忆", "定时提醒", "上网搜索"]
        self._handlers: Dict[str, Callable[..., Any]] = {
            "add_task": s.add_task,
            "list_tasks": s.list_tasks,
            "update_task": lambda task_id, **kw: s.update_task(task_id, **kw),
            "delete_task": lambda task_id: (s.delete_task(task_id), {"deleted": task_id})[1],
            "add_note": s.add_note,
            "search_notes": s.search_notes,
            "add_worklog": s.add_worklog,
            "daily_summary": lambda date=None: s.daily_summary(date),
            "remember": s.add_memory,
            "forget": lambda memory_id: (s.delete_memory(memory_id), {"forgotten": memory_id})[1],
            "add_reminder": s.add_reminder,
            "list_reminders": lambda status="pending": s.list_reminders(status),
            "cancel_reminder": lambda reminder_id: s.cancel_reminder(reminder_id),
            "web_search": lambda query, max_results=5: search.web_search(cfg, query, max_results),
            "fetch_url": lambda url: search.fetch_url(url),
        }
        calendar = calendar_mac.provider(store)
        if calendar:
            self.specs += CALENDAR_SPECS
            self.capabilities.append("日历（查看/新建日程）")
            self._handlers["calendar_events"] = calendar.list_events
            self._handlers["calendar_add_event"] = calendar.add_event
        if cfg is not None and cfg.email_ready:
            self.specs += EMAIL_SPECS
            self.capabilities.append("邮箱（读取、草拟邮件）")
            self._handlers["list_emails"] = lambda days=3, query="", limit=10: \
                mail.list_emails(cfg, days, query, limit)
            self._handlers["read_email"] = lambda uid: mail.read_email(cfg, uid)
            self._handlers["draft_email"] = self._draft_email
        if cfg is not None and cfg.wechat_ready:
            self.specs += WECHAT_SPECS
            self.capabilities.append("微信推送")
            self._handlers["send_wechat"] = lambda message, title="私人工作助理": \
                {"sent_via": notify.wechat_push(cfg, title, message)}

    def chat_specs(self, developing=False):
        if developing:
            return [PROPOSE]
        specs = list(self.specs)
        for preset in self.store.presets("active"):
            params = PRESET_ACTIONS[preset["action"]]["parameters"]
            free = {k: v for k, v in params["properties"].items() if k not in preset["fixed"]}
            specs.append(_fn("custom_" + str(preset["id"]), preset["name"] + "：" + preset["description"],
                             free, [k for k in params["required"] if k in free]))
        return specs

    @staticmethod
    def _validate_params(action, values):
        if action not in PRESET_ACTIONS or not isinstance(values, dict):
            raise ValueError("不支持的动作或参数")
        fields = PRESET_ACTIONS[action]["parameters"]["properties"]
        for key, value in values.items():
            field = fields.get(key)
            if not field or type(value) is not {"string": str, "integer": int}[field["type"]]:
                raise ValueError("无效参数：" + key)
            if "enum" in field and value not in field["enum"]:
                raise ValueError("无效选项：" + key)

    def _propose(self, name, description, action, fixed):
        if not all(isinstance(v, str) and v.strip() for v in (name, description, action)):
            raise ValueError("请填写工具名称、用途和基础动作")
        if len(name) > 40 or len(description) > 240 or len(json.dumps(fixed)) > 8000:
            raise ValueError("工具定义过长")
        self._validate_params(action, fixed)
        return self.store.propose_preset(name.strip(), description.strip(), action, fixed)

    def _perform_preset(self, action, fixed, args):
        if fixed.keys() & args.keys():
            raise ValueError("固定参数不可覆盖")
        self._validate_params(action, args)
        return self._handlers[action](**fixed, **args)

    def _draft_email(self, to: str, subject: str, body: str) -> Dict[str, Any]:
        addrs = mail.validate_recipients(to)
        d = self.store.add_draft(", ".join(addrs), subject, body)
        return {"draft_id": d["id"], "status": "待用户确认",
                "hint": "邮件尚未发送，请提醒用户到网页「邮件」标签页确认发送"}

    def run(self, name: str, arguments: str, developing: bool = False) -> str:
        """执行工具，始终返回 JSON 字符串（错误也以 JSON 返回给模型）。"""
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("参数必须是 JSON 对象")
            if developing and name == "propose_tool":
                result = self._propose(**args)
            elif not developing and name.startswith("custom_") and len(name) <= 25 and name[7:].isascii() and name[7:].isdigit():
                result = self.store.run_preset(int(name[7:]), args, self._perform_preset)
            elif not developing and name in self._handlers:
                result = self._handlers[name](**args)
            else:
                raise ValueError("未知工具: " + name)
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False, default=str)
        except (ValueError, TypeError, IntegrationError) as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
