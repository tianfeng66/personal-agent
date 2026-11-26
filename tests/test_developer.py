import json
import tempfile
import unittest
from pathlib import Path

from agent.config import Config
from agent.server import App
from tests.helpers import FakeLLM


class DeveloperModeTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.cfg = Config("test", "", "fake", Path(self.folder.name))
        self.app = App(self.cfg)
        self.definition = dict(name="会议纪要", description="保存会议笔记", action="add_note",
                               fixed={"tags": "会议"})

    def tearDown(self):
        self.app.store.close()
        self.folder.cleanup()

    def invoke(self, name, args, developing=False):
        return json.loads(self.app.agent.tools.run(name, json.dumps(args), developing))

    def decide(self, row, decision):
        return self.app.route("POST", "/api/tool-presets/{}/{}".format(row, decision), {}, {})

    def propose(self):
        return self.invoke("propose_tool", self.definition, True)["result"]["id"]

    def test_chat_approval_execution_persistence_and_revocation(self):
        call = {"id": "proposal", "type": "function", "function": {
            "name": "propose_tool", "arguments": json.dumps(self.definition)}}
        self.app.agent.llm = FakeLLM([{"tool_calls": [call]}, {"content": "请审核草案"}])
        self.app.route("POST", "/api/chat", {}, {"message": "做一个会议笔记工具", "developing": True})
        row, = self.app.route("GET", "/api/tool-presets", {}, {})
        name = "custom_" + str(row["id"])
        args = {"title": "周会", "content": "评审"}
        self.assertFalse(self.invoke(name, args)["ok"])
        self.decide(row["id"], "approve")
        self.app.store.close()
        self.app = App(self.cfg)
        spec, = [s["function"] for s in self.app.agent.tools.chat_specs() if s["function"]["name"] == name]
        self.assertNotIn("tags", spec["parameters"]["properties"])
        self.assertFalse(self.invoke(name, dict(args, tags="其他"))["ok"])
        tool_call = {"id": "use", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        self.app.agent.llm = FakeLLM([{"tool_calls": [tool_call]}, {"content": "已保存"}])
        self.app.route("POST", "/api/chat", {}, {"message": "用会议纪要保存这次周会"})
        note, = self.app.store.search_notes("周会")
        self.assertEqual(note["tags"], "会议")
        self.decide(row["id"], "revoke")
        self.assertFalse(self.invoke(name, args)["ok"])

    def test_scope(self):
        self.assertFalse(self.invoke("propose_tool", self.definition)["ok"])
        self.assertFalse(self.invoke("add_task", {"title": "未经授权"}, True)["ok"])
        self.assertFalse(self.invoke("propose_tool", dict(self.definition, action="send_wechat"), True)["ok"])
        self.assertFalse(self.invoke("propose_tool", dict(self.definition, fixed={"bogus": 1}), True)["ok"])
        self.assertEqual(self.app.store.presets(), [])

    def test_replacement_and_rejection(self):
        first = self.propose()
        self.decide(first, "approve")
        rejected = self.propose()
        self.decide(rejected, "reject")
        with self.assertRaises(ValueError):
            self.decide(rejected, "approve")
        self.assertEqual(self.app.store.presets("active")[0]["id"], first)
        second = self.propose()
        self.decide(second, "approve")
        self.assertEqual([p["id"] for p in self.app.store.presets("active")], [second])
        self.assertFalse(self.invoke("custom_" + str(first), {})["ok"])
