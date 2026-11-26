import json
import re
import threading
import time
import unittest

from agent.core import Agent
from agent.storage import Store
from tests.helpers import temp_config, tool_call


class RoutingLLM:
    """根据系统提示里的身份，给不同智能体返回各自预设的回复。"""

    def __init__(self, scripts, delay=0.0):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.delay = delay
        self.tools_seen = {}
        self.lock = threading.Lock()

    def chat(self, messages, tools=None):
        m = re.match(r"你是「(.+?)」", messages[0]["content"])
        who = m.group(1) if m else "总助理"
        with self.lock:
            self.tools_seen.setdefault(who, []).append([t["function"]["name"] for t in tools or []])
            queue = self.scripts.get(who) or []
            reply = queue.pop(0) if queue else {"content": who + " 完成"}
        time.sleep(self.delay)
        return reply


class TeamTest(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_config()
        self.s = Store(self.cfg.db_path)
        self.s.add_agent("研究员", "查资料", tool_groups=["search"])
        self.s.add_agent("写作助手", "写文字", tool_groups=["notes"])
        self.events = []

    def tearDown(self):
        self.s.close()

    def make(self, llm):
        return Agent(llm, self.s, cfg=self.cfg, on_event=self.events.append)

    def test_parallel_delegation_and_summary(self):
        llm = RoutingLLM({
            "总助理": [
                {"content": None, "tool_calls": [
                    tool_call("a", "ask_agent", agent_name="研究员", task="查市场数据"),
                    tool_call("b", "ask_agent", agent_name="写作助手", task="写开头"),
                ]},
                {"content": "汇总：研究员查了数据，写作助手写了开头。"},
            ],
            "研究员": [{"content": "市场规模 100 亿"}],
            "写作助手": [{"content": "开头段落"}],
        }, delay=0.5)
        start = time.time()
        reply = self.make(llm).ask("帮我准备一份市场分析")
        elapsed = time.time() - start
        self.assertIn("汇总", reply)
        # 两个成员并行：总耗时约 3 次调用（总助理2次 + 成员并行1次），不是 4 次
        self.assertLess(elapsed, 1.9)
        delegated = sorted(e["to"] for e in self.events if e["type"] == "delegate")
        self.assertEqual(delegated, ["写作助手", "研究员"])
        # 成员的结果交回给了总助理
        tool_msgs = [m for m in self.make(RoutingLLM({})).history if m["role"] == "tool"]
        results = [json.loads(m["content"])["result"]["result"] for m in tool_msgs]
        self.assertIn("市场规模 100 亿", results)

    def test_member_cannot_use_unauthorized_tools(self):
        llm = RoutingLLM({
            "总助理": [{"content": None, "tool_calls": [
                tool_call("a", "ask_agent", agent_name="研究员", task="建个任务")]},
                {"content": "好"}],
            "研究员": [{"content": None, "tool_calls": [tool_call("x", "add_task", title="越权任务")]},
                     {"content": "没有权限"}],
        })
        self.make(llm).ask("让研究员建任务")
        self.assertEqual(self.s.list_tasks("all"), [])
        self.assertNotIn("add_task", llm.tools_seen["研究员"][0])
        self.assertIn("web_search", llm.tools_seen["研究员"][0])

    def test_no_circular_delegation_and_depth_limit(self):
        self.s.add_agent("甲", "a")
        self.s.add_agent("乙", "b")
        llm = RoutingLLM({
            "总助理": [{"content": None, "tool_calls": [
                tool_call("1", "ask_agent", agent_name="甲", task="t")]}, {"content": "结束"}],
            "甲": [{"content": None, "tool_calls": [
                tool_call("2", "ask_agent", agent_name="乙", task="t")]}, {"content": "甲完成"}],
            "乙": [{"content": None, "tool_calls": [
                tool_call("3", "ask_agent", agent_name="甲", task="t")]}, {"content": "乙完成"}],
        })
        self.make(llm).ask("开始")
        # 乙处在第 3 层（总助理 → 甲 → 乙），不应该再有 ask_agent 工具
        self.assertNotIn("ask_agent", llm.tools_seen["乙"][0])
        self.assertIn("ask_agent", llm.tools_seen["甲"][0])
        self.assertEqual(sum(1 for e in self.events if e["type"] == "delegate" and e["to"] == "甲"), 1)

    def test_loop_rejected(self):
        agent = self.make(RoutingLLM({}))
        with self.assertRaises(ValueError):
            agent.team.delegate("研究员", "研究员", "t", ("总助理", "研究员"))

    def test_mention_goes_directly_to_member(self):
        llm = RoutingLLM({"写作助手": [{"content": "这是草稿"}]})
        reply = self.make(llm).ask("@写作助手 写一段介绍")
        self.assertIn("这是草稿", reply)
        self.assertNotIn("总助理", llm.tools_seen)

    def test_create_agent_tool(self):
        llm = RoutingLLM({"总助理": [
            {"content": None, "tool_calls": [tool_call(
                "c", "create_agent", name="审核员", role="检查错误", tool_groups=["search"])]},
            {"content": "已创建"}]})
        self.make(llm).ask("建一个审核员")
        self.assertIsNotNone(self.s.find_agent("审核员"))

    def test_agent_validation(self):
        with self.assertRaises(ValueError):
            self.s.add_agent("研究员", "重名")
        with self.assertRaises(ValueError):
            self.s.add_agent("总助理", "保留名")
        with self.assertRaises(ValueError):
            self.s.add_agent("有 空格", "x")
        with self.assertRaises(ValueError):
            self.s.add_agent("新成员", "x", tool_groups=["delete_everything"])
        a = self.s.find_agent("研究员")
        self.s.update_agent(a["id"], enabled=False)
        self.assertIsNone(self.s.find_agent("研究员"))  # 停用后不能被委托


if __name__ == "__main__":
    unittest.main()
