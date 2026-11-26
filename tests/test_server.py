import http.client
import json
import socket
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from agent.server import App, make_handler
from tests.helpers import temp_config


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SlowLLM:
    """模拟回答很慢的大模型。"""

    def __init__(self, delay: float):
        self.delay = delay

    def chat(self, messages, tools=None):
        time.sleep(self.delay)
        return {"content": "慢慢回答完了。"}


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = temp_config()
        cls.app = App(cls.cfg)
        cls.app.agent.llm = SlowLLM(1.5)
        cls.port = free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), make_handler(cls.app, cls.port))
        cls.httpd.daemon_threads = True
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.app.store.close()

    def req(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": "127.0.0.1:{}".format(self.port)}
        h.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        payload = json.loads(r.read().decode("utf-8") or "null")
        conn.close()
        return r.status, payload

    def test_crud_and_validation(self):
        code, t = self.req("POST", "/api/tasks", {"title": "写方案", "due_date": "2026-12-01"})
        self.assertEqual(code, 200)
        self.assertEqual(self.req("PATCH", "/api/tasks/{}".format(t["id"]), {"status": "done"})[0], 200)
        self.assertEqual(self.req("POST", "/api/tasks", {"title": "x", "due_date": "bad"})[0], 400)
        self.assertEqual(self.req("DELETE", "/api/tasks/999999")[0], 400)

    def test_agents_api(self):
        code, a = self.req("POST", "/api/agents", {"name": "研究员", "role": "查资料",
                                                   "tool_groups": ["search"]})
        self.assertEqual(code, 200)
        self.assertEqual(self.req("POST", "/api/agents", {"name": "研究员", "role": "x"})[0], 400)
        code, upd = self.req("PATCH", "/api/agents/{}".format(a["id"]),
                             {"role": "查资料并核实", "enabled": False})
        self.assertEqual((code, upd["role"], upd["enabled"]), (200, "查资料并核实", False))
        code, data = self.req("GET", "/api/agents")
        self.assertEqual(len(data["templates"]), 4)
        self.assertIn("search", [g["id"] for g in data["tool_groups"] if g["available"]])
        self.assertEqual(self.req("DELETE", "/api/agents/{}".format(a["id"]))[0], 200)

    def test_security_checks(self):
        self.assertEqual(self.req("GET", "/api/status", headers={"Host": "evil.com"})[0], 403)
        code, _ = self.req("POST", "/api/tasks", None, headers={"Content-Type": "text/plain"})
        self.assertEqual(code, 415)

    def test_panel_not_blocked_while_ai_is_answering(self):
        results = {}

        def chat():
            results["chat"] = self.req("POST", "/api/chat", {"message": "你好"})

        th = threading.Thread(target=chat)
        th.start()
        time.sleep(0.3)  # 确保对话请求已经在处理中
        start = time.time()
        code, _ = self.req("GET", "/api/tasks")
        elapsed = time.time() - start
        self.assertEqual(code, 200)
        self.assertLess(elapsed, 1.0, "AI 回答时，任务列表请求被卡住了")
        # 同时发第二条对话应该被拒绝，而不是打乱对话历史
        self.assertEqual(self.req("POST", "/api/chat", {"message": "再来"})[0], 409)
        th.join()
        self.assertEqual(results["chat"][0], 200)
        code, history = self.req("GET", "/api/chat/history")
        self.assertEqual([m["content"] for m in history][-2:], ["你好", "慢慢回答完了。"])


if __name__ == "__main__":
    unittest.main()
