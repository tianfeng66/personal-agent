import json
import unittest

from agent.core import Agent, display_history
from agent.integrations import IntegrationError, search
from agent.storage import Store
from tests.helpers import FakeLLM, temp_config, tool_call


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_config()
        self.s = Store(self.cfg.db_path)

    def tearDown(self):
        self.s.close()

    def test_tool_calls_and_errors_are_returned_to_model(self):
        llm = FakeLLM([
            {"content": None, "tool_calls": [
                tool_call("c1", "add_task", title="发周报", priority="high"),
                tool_call("c2", "add_task", title="x", due_date="bad"),
            ]},
            {"content": "已记录。"},
        ])
        agent = Agent(llm, self.s, cfg=self.cfg)
        self.assertEqual(agent.ask("明天发周报"), "已记录。")
        self.assertEqual([t["title"] for t in self.s.list_tasks()], ["发周报"])
        last = llm.calls[-1][-1]
        self.assertEqual(last["role"], "tool")
        self.assertFalse(json.loads(last["content"])["ok"])

    def test_history_survives_restart(self):
        Agent(FakeLLM([{"content": "你好，我在。"}]), self.s, cfg=self.cfg).ask("在吗")
        llm2 = FakeLLM([{"content": "刚才你问我在不在。"}])
        agent2 = Agent(llm2, self.s, cfg=self.cfg)  # 模拟重启
        agent2.ask("我刚才说了什么")
        sent = [m["content"] for m in llm2.calls[0] if m["role"] == "user"]
        self.assertEqual(sent, ["在吗", "我刚才说了什么"])
        agent2.reset()
        self.assertEqual(Agent(FakeLLM([]), self.s, cfg=self.cfg).history, [])

    def test_display_history_hides_tool_messages(self):
        llm = FakeLLM([
            {"content": None, "tool_calls": [tool_call("c1", "list_tasks")]},
            {"content": "没有任务。"},
        ])
        Agent(llm, self.s, cfg=self.cfg).ask("有任务吗")
        shown = display_history(self.s.load_chat_messages(50))
        self.assertEqual([m["role"] for m in shown], ["user", "assistant"])
        self.assertEqual(shown[1]["tools"][0]["name"], "list_tasks")

    def test_email_and_wechat_tools_hidden_when_not_configured(self):
        names = [t["function"]["name"] for t in Agent(FakeLLM([]), self.s, cfg=self.cfg).tools.specs]
        self.assertIn("web_search", names)
        self.assertNotIn("draft_email", names)
        self.assertNotIn("send_wechat", names)

    def test_system_prompt_contains_memories(self):
        self.s.add_memory("用户是产品经理")
        agent = Agent(FakeLLM([]), self.s, cfg=self.cfg)
        self.assertIn("产品经理", agent._system_message()["content"])


class FetchUrlSafetyTest(unittest.TestCase):
    def test_blocks_local_and_private_addresses(self):
        for url in ["http://127.0.0.1:8765/api/status", "http://localhost/",
                    "http://192.168.1.1/", "http://10.0.0.1/", "file:///etc/passwd"]:
            with self.assertRaises(IntegrationError, msg=url):
                search.fetch_url(url)


if __name__ == "__main__":
    unittest.main()
