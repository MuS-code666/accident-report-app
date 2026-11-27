"""Ollama（ローカルLLM）への接続。

- 接続先は 127.0.0.1 に固定し、外部ホストへは接続しない。
- 出力形式は JSON スキーマで制約し、Pydantic で検証する。不正なら再試行する。
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

DEFAULT_URL = "http://127.0.0.1:11434"

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def strict_schema(node):
    """すべてのプロパティを必須にする（省略可能だとモデルが項目ごと省略することがあるため）。"""
    if isinstance(node, dict):
        out = {k: strict_schema(v) for k, v in node.items() if k != "default"}
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])
        return out
    if isinstance(node, list):
        return [strict_schema(v) for v in node]
    return node


@dataclass
class CallStats:
    seconds: float
    prompt_tokens: int
    output_tokens: int
    attempts: int


@dataclass
class OllamaClient:
    model: str = "qwen3:8b"
    url: str = DEFAULT_URL
    temperature: float = 0.0
    num_ctx: int = 8192
    timeout: float = 600.0  # 1回の処理の上限（設計条件: 10分以内）
    max_attempts: int = 2
    history: list[CallStats] = field(default_factory=list)

    def __post_init__(self) -> None:
        host = self.url.split("//", 1)[-1].split(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError("LLMの接続先はループバックアドレスに限定しています")

    def generate_json(self, system: str, prompt: str, schema: type[T], max_tokens: int = 1024) -> T:
        """JSONスキーマに従った出力を生成し、Pydanticモデルとして返す。"""
        body = {
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "format": strict_schema(schema.model_json_schema()),
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx, "num_predict": max_tokens},
        }
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            start = time.perf_counter()
            data = self._post("/api/generate", body)
            elapsed = time.perf_counter() - start
            try:
                result = schema.model_validate_json(data["response"])
            except (ValidationError, KeyError, json.JSONDecodeError) as e:
                last_error = e
                continue
            self.history.append(
                CallStats(
                    seconds=elapsed,
                    prompt_tokens=data.get("prompt_eval_count", 0),
                    output_tokens=data.get("eval_count", 0),
                    attempts=attempt,
                )
            )
            return result
        raise LLMError(f"LLMの出力を検証できませんでした: {last_error}")

    def warm_up(self) -> None:
        """モデルを読み込んでおく（初回の待ち時間を測定から除くため）。"""
        self._post("/api/generate", {"model": self.model, "prompt": "", "keep_alive": "30m"})

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return json.loads(res.read().decode("utf-8"))
        except OSError as e:
            raise LLMError(f"Ollamaに接続できません（起動しているか確認してください）: {e}") from e
