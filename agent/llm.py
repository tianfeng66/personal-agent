"""OpenAI 兼容的 Chat Completions 客户端（零依赖，基于 urllib）。

兼容 OpenAI / DeepSeek / 通义千问 / Moonshot / Ollama 等提供 /v1/chat/completions 的服务。
"""

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 120):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """发送一次对话请求，返回 assistant message（可能包含 tool_calls）。"""
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key

        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise LLMError("HTTP {}: {}".format(e.code, detail[:500])) from e
        except urllib.error.URLError as e:
            raise LLMError("网络错误: {}".format(e.reason)) from e
        except (TimeoutError, OSError) as e:
            raise LLMError("请求失败: {}".format(e)) from e
        except json.JSONDecodeError as e:
            raise LLMError("响应不是合法 JSON") from e

        try:
            return body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("响应格式异常: {}".format(str(body)[:500])) from e
