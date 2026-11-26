"""测试公用工具：临时数据目录、假的大模型。"""

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from agent.config import Config


def temp_config() -> Config:
    return Config(api_key="test-key", base_url="http://example.invalid/v1", model="fake",
                  data_dir=Path(tempfile.mkdtemp(prefix="pa-test-")))


def tool_call(call_id: str, tool_name: str, **args: Any) -> Dict[str, Any]:
    return {"id": call_id, "type": "function",
            "function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)}}


class FakeLLM:
    """按顺序返回预设的回复；记录每次收到的消息。"""

    def __init__(self, replies: List[Dict[str, Any]]):
        self.replies = list(replies)
        self.calls: List[List[Dict[str, Any]]] = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        if not self.replies:
            return {"content": "好的。"}
        return self.replies.pop(0)
