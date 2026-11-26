"""网页界面后端：标准库 http.server，只监听本机 127.0.0.1。

安全说明：
- 只绑定 127.0.0.1，局域网其他设备无法访问；没有登录功能，不要改成 0.0.0.0。
- 校验 Host 头，防止 DNS 重绑定攻击。
- 写操作要求 Content-Type: application/json，其他网站无法通过浏览器跨站提交。
- 发送邮件只能通过网页上的「确认发送」，AI 只能创建草稿。
- 多线程处理请求：AI 回答时，右侧面板的操作不会被卡住。同一时间只处理一条 AI 对话。
"""

import json
import re
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

from .config import Config
from .core import Agent, display_history
from .integrations import IntegrationError, calendar_mac, mail, notify
from .llm import LLMClient, LLMError
from .reminders import ReminderScheduler
from .storage import AGENT_TOOL_GROUPS, Store, today_str
from .team import GROUP_LABELS, TEMPLATES

STATIC_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 1024 * 1024  # 1MB
STATIC_TYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}


class HttpError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def _pick(data: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    """只取白名单字段，空字符串视为未填写。"""
    return {k: data[k] for k in keys if data.get(k) not in ("", None)}


class App:
    """持有存储和 Agent，负责所有业务路由。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = Store(cfg.db_path)
        self.calendar = calendar_mac.provider(self.store)
        self.llm_ready = bool(cfg.api_key) or cfg.is_local_llm
        self.agent = Agent(LLMClient(cfg.api_key, cfg.base_url, cfg.model), self.store,
                           on_tool=self._record_tool, cfg=cfg, on_event=self._record_event)
        self._tool_log: List[Dict[str, str]] = []
        self._events: List[Dict[str, Any]] = []   # 本轮多智能体协作的过程，供网页实时显示
        self._events_lock = threading.Lock()
        self._agent_lock = threading.Lock()  # Agent 的对话历史不能被两个请求同时修改

    def _with_agent(self, fn):
        if not self._agent_lock.acquire(blocking=False):
            raise HttpError(409, "上一条消息还在处理中，请稍等")
        try:
            self._tool_log = []
            with self._events_lock:
                self._events = []
            return fn()
        finally:
            with self._events_lock:
                self._events = []  # 结果已随响应返回，清空，避免下一轮显示旧进度
            self._agent_lock.release()

    def _record_event(self, event: Dict[str, Any]) -> None:
        with self._events_lock:
            self._events.append(dict(event, time=datetime.now().strftime("%H:%M:%S")))

    def events_since(self, since: int) -> Dict[str, Any]:
        with self._events_lock:
            return {"events": self._events[since:], "next": len(self._events),
                    "busy": self._agent_lock.locked()}

    def _record_tool(self, name: str, args: str) -> None:
        self._tool_log.append({"name": name, "args": args})

    def _require_llm(self) -> None:
        if not self.llm_ready:
            raise HttpError(400, "未配置 LLM_API_KEY，请在 .env 中填写后重启")

    def status(self) -> Dict[str, Any]:
        installation = None
        if sys.platform == "win32":
            from .service_windows import INSTALL_ID
            installation = INSTALL_ID
        c = self.cfg
        return {
            "installation": installation, "model": c.model, "llm_ready": self.llm_ready, "today": today_str(),
            "integrations": {
                "calendar": bool(self.calendar),
                "calendar_local": sys.platform == "win32",
                "email": c.email_ready,
                "email_address": c.email_address if c.email_ready else "",
                "wechat": c.wechat_ready,
                "search": "Tavily" if c.tavily_key else "DuckDuckGo",
            },
        }

    def route(self, method: str, path: str, q: Dict[str, str], body: Dict[str, Any]) -> Any:
        s = self.store
        cfg = self.cfg

        m = re.fullmatch(r"/api/(tasks|notes|worklogs|memories|reminders)/(\d+)", path)
        if m:
            kind, item_id = m.group(1), int(m.group(2))
            if method == "PATCH" and kind == "tasks":
                return s.update_task(item_id, **_pick(
                    body, "title", "status", "priority", "due_date", "project", "notes"))
            if method == "DELETE":
                {"tasks": s.delete_task, "notes": s.delete_note, "worklogs": s.delete_worklog,
                 "memories": s.delete_memory, "reminders": s.cancel_reminder}[kind](item_id)
                return {"deleted": item_id}
            raise HttpError(404, "未找到")

        m = re.fullmatch(r"/api/agents/(\d+)", path)
        if m:
            aid = int(m.group(1))
            if method == "PATCH":
                fields = _pick(body, "name", "role", "tool_groups")
                if "instructions" in body:
                    fields["instructions"] = str(body["instructions"] or "")
                if "enabled" in body:
                    fields["enabled"] = bool(body["enabled"])
                if "tool_groups" in fields and not isinstance(fields["tool_groups"], list):
                    raise ValueError("tool_groups 必须是列表")
                return s.update_agent(aid, **fields)
            if method == "DELETE":
                s.delete_agent(aid)
                return {"deleted": aid}
            raise HttpError(404, "未找到")

        m = re.fullmatch(r"/api/email/drafts/(\d+)(/send)?", path)
        if m:
            did = int(m.group(1))
            if method == "POST" and m.group(2):
                draft = s.get_draft(did)
                if draft["status"] != "pending":
                    raise ValueError("这封草稿已经处理过了")
                mail.send_email(cfg, draft["to_addr"], draft["subject"], draft["body"])
                return s.set_draft_status(did, "sent")
            if method == "DELETE" and not m.group(2):
                return s.set_draft_status(did, "discarded")
            raise HttpError(404, "未找到")

        m = re.fullmatch(r"/api/email/message/(\d+)", path)
        if m and method == "GET":
            return mail.read_email(cfg, m.group(1))

        if path == "/api/tool-presets" and method == "GET":
            return s.presets()
        decision = re.fullmatch(r"/api/tool-presets/([0-9]+)/(approve|reject|revoke)", path)
        if decision and method == "POST":
            return s.decide_preset(int(decision[1]), decision[2])
        key = (method, path)
        if key == ("GET", "/api/status"):
            return self.status()
        if key == ("GET", "/api/tasks"):
            return s.list_tasks(q.get("status", "open"), q.get("project") or None)
        if key == ("POST", "/api/tasks"):
            return s.add_task(**_pick(body, "title", "priority", "due_date", "project", "notes"))
        if key == ("GET", "/api/notes"):
            return s.search_notes(q.get("q", ""), limit=100)
        if key == ("POST", "/api/notes"):
            return s.add_note(**_pick(body, "title", "content", "tags"))
        if key == ("GET", "/api/worklogs"):
            return s.list_worklogs(q.get("date") or today_str())
        if key == ("POST", "/api/worklogs"):
            data = _pick(body, "content", "minutes", "project", "log_date")
            if "minutes" in data:
                try:
                    data["minutes"] = int(data["minutes"])
                except (TypeError, ValueError):
                    raise ValueError("分钟数必须是整数")
            return s.add_worklog(**data)
        if key == ("GET", "/api/memories"):
            return s.list_memories()
        if key == ("POST", "/api/memories"):
            return s.add_memory(str(body.get("content", "")))
        if key == ("GET", "/api/summary"):
            return s.daily_summary(q.get("date") or None)

        if key == ("GET", "/api/reminders"):
            return s.list_reminders(q.get("status", "pending"))
        if key == ("POST", "/api/reminders"):
            return s.add_reminder(str(body.get("content", "")), str(body.get("remind_at", "")))
        if key == ("GET", "/api/reminders/unseen"):
            return s.pop_unseen_reminders()

        if key == ("GET", "/api/calendar"):
            if not self.calendar:
                raise IntegrationError("此系统暂不支持日历")
            return self.calendar.list_events(q.get("date") or None, int(q.get("days", "1") or 1))
        if key == ("GET", "/api/email/inbox"):
            return mail.list_emails(cfg, int(q.get("days", "3") or 3), q.get("q", ""), 20)
        if key == ("GET", "/api/email/drafts"):
            return s.list_drafts()
        if key == ("POST", "/api/wechat/test"):
            return {"sent_via": notify.wechat_push(cfg, "测试消息", "私人工作助理的微信推送已经连通。")}

        if key == ("POST", "/api/chat"):
            self._require_llm()
            message = str(body.get("message", "")).strip()
            if not message:
                raise ValueError("消息不能为空")
            return self._with_agent(
                lambda: {"reply": self.agent.ask(message, developing=body.get("developing") is True), "tools": list(self._tool_log),
                         "events": list(self._events)})
        if key == ("GET", "/api/chat/progress"):
            return self.events_since(max(0, int(q.get("since", "0") or 0)))

        # ---------- 智能体管理 ----------
        if key == ("GET", "/api/agents"):
            return {"agents": s.list_agents(), "templates": TEMPLATES,
                    "tool_groups": [{"id": g, "label": GROUP_LABELS[g],
                                     "available": bool(self.agent.tools.specs_for([g]))}
                                    for g in AGENT_TOOL_GROUPS]}
        if key == ("POST", "/api/agents"):
            groups = body.get("tool_groups") or []
            if not isinstance(groups, list):
                raise ValueError("tool_groups 必须是列表")
            return s.add_agent(str(body.get("name", "")), str(body.get("role", "")),
                               str(body.get("instructions", "")), [str(g) for g in groups])
        if key == ("GET", "/api/chat/history"):
            return display_history(s.load_chat_messages(200))
        if key == ("POST", "/api/chat/reset"):
            self._with_agent(self.agent.reset)
            return {"ok": True}
        if key == ("POST", "/api/report"):
            self._require_llm()
            text, report_path = self._with_agent(
                lambda: self.agent.write_daily_report(self.cfg.reports_dir))
            return {"text": text, "path": str(report_path)}
        raise HttpError(404, "未找到")


def make_handler(app: App, port: int):
    allowed_hosts = {"127.0.0.1:{}".format(port), "localhost:{}".format(port)}

    class Handler(BaseHTTPRequestHandler):
        server_version = "PersonalAgent/0.2"

        def log_message(self, fmt, *args):
            sys.stderr.write("[web] " + (fmt % args) + "\n")

        def _send(self, code: int, data: Any) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> Dict[str, Any]:
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                raise HttpError(415, "需要 application/json")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > MAX_BODY:
                raise HttpError(413, "请求体过大")
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise HttpError(400, "JSON 格式错误")
            if not isinstance(data, dict):
                raise HttpError(400, "请求体必须是 JSON 对象")
            return data

        def _serve_static(self, name: str) -> None:
            path = (STATIC_DIR / name).resolve()
            if STATIC_DIR not in path.parents or not path.is_file() or path.suffix not in STATIC_TYPES:
                raise HttpError(404, "未找到")
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", STATIC_TYPES[path.suffix] + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self'; script-src 'self'")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _handle(self, method: str) -> None:
            try:
                if self.headers.get("Host", "") not in allowed_hosts:
                    raise HttpError(403, "Host 不被允许")
                url = urlparse(self.path)
                if method == "GET" and url.path in ("/", "/index.html"):
                    self._serve_static("index.html")
                    return
                if method == "GET" and url.path.startswith("/static/"):
                    self._serve_static(url.path[len("/static/"):])
                    return
                body = self._read_json() if method in ("POST", "PATCH") else {}
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                self._send(200, app.route(method, url.path, q, body))
            except HttpError as e:
                self._send(e.code, {"error": str(e)})
            except IntegrationError as e:
                self._send(502, {"error": str(e)})
            except (ValueError, TypeError) as e:
                self._send(400, {"error": str(e)})
            except LLMError as e:
                self._send(502, {"error": "调用大模型失败：{}".format(e)})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PATCH(self):
            self._handle("PATCH")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler


def serve(cfg: Config, port: int = 8765, on_ready=None) -> None:
    app = App(cfg)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
    except OSError as e:
        print("无法启动：端口 {} 已被占用（可能已经在后台运行了）。{}".format(port, e), file=sys.stderr)
        app.store.close()
        sys.exit(1)
    httpd.daemon_threads = True
    scheduler = ReminderScheduler(cfg)
    scheduler.start()
    print("网页界面已启动：http://127.0.0.1:{}  （按 Ctrl+C 停止）".format(port))
    if not app.llm_ready:
        print("提示：未配置 LLM_API_KEY，任务/笔记/日志可用，AI 对话不可用。")
    try:
        if on_ready:
            on_ready()
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        scheduler.stop()
        httpd.server_close()
        app.store.close()
