"""DeepSeek API 封装。

上层只需要说「要一段 JSON」或「要一段文字」，不用碰 HTTP、超时、重试、解析。

六条设计（都是踩过坑的）：
  1. **客户端模块级复用** —— 不每次 new，TCP 连接和 TLS 握手只做一次
  2. **超时显式设置** —— SDK 默认十分钟量级，网络半死时会静默挂住。
     connect 短（连不上就是连不上），read 长（生成本来就要几十秒）
  3. **max_retries=0** —— openai SDK 默认自己重试 2 次。
     不关掉的话，本模块的 max_attempts=3 实际会变成 9 次调用
  4. **思考开关放 extra_body** —— 不是顶层参数。
     且思考模式下 temperature 会被忽略（传了不报错但无效）
  5. **JSON 两种失败分开** —— 空返回（可重试）vs 截断（重试无用，必须调大 max_tokens）
  6. **不硬编码 key** —— 从 .env 或环境变量读
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

__all__ = [
    "LLMError", "LLMEmptyResponse", "LLMTruncated",
    "chat", "chat_json", "extract_json", "MODEL", "MODEL_PRO",
]

# ══════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════
BASE_URL = "https://api.deepseek.com"

MODEL = "deepseek-flash"        # 判点：关思考 + 低温，量大要便宜
MODEL_PRO = "deepseek-v4-pro"   # 采点 / 自由推理：开思考，需要读懂整篇材料

# 超时参数（构造 client 时才组装，避免顶层就依赖 httpx）
# 本地模型慢，read 要给大；云端 300 秒也够
TIMEOUT_CONNECT = 10.0
TIMEOUT_READ = 300.0
TIMEOUT_WRITE = 60.0
TIMEOUT_POOL = 10.0

DEFAULT_MAX_ATTEMPTS = 3


# ══════════════════════════════════════════════════════════
# 异常
# ══════════════════════════════════════════════════════════
class LLMError(RuntimeError):
    """调用失败（已重试完仍失败）。"""


class LLMEmptyResponse(LLMError):
    """HTTP 200 但内容为空 —— DeepSeek JSON 模式的官方已知问题，值得重试。"""


class LLMTruncated(LLMError):
    """输出被 max_tokens 截断。**重试无用**，会截在同一位置。"""


# ══════════════════════════════════════════════════════════
# API Key
# ══════════════════════════════════════════════════════════
def _load_key() -> str:
    """优先环境变量，其次 .env。绝不硬编码。"""
    import os

    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key

    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            # 兼容 `export KEY=...` 和 `KEY=...` 两种写法
            if line.startswith("export "):
                line = line[7:].strip()
            if line.startswith("DEEPSEEK_API_KEY"):
                value = line.split("=", 1)[-1].strip().strip('"').strip("'")
                if value:
                    return value

    raise LLMError(
        "未找到 DEEPSEEK_API_KEY。\n"
        "  方式一：环境变量  set DEEPSEEK_API_KEY=sk-xxxx\n"
        "  方式二：项目根建 .env，内容 DEEPSEEK_API_KEY=sk-xxxx"
    )


# ══════════════════════════════════════════════════════════
# 客户端（模块级单例）
# ══════════════════════════════════════════════════════════
_client = None


def _get_client():
    global _client
    if _client is None:
        import httpx
        from openai import OpenAI

        _client = OpenAI(
            api_key=_load_key(),
            base_url=BASE_URL,
            timeout=httpx.Timeout(
                connect=TIMEOUT_CONNECT,
                read=TIMEOUT_READ,
                write=TIMEOUT_WRITE,
                pool=TIMEOUT_POOL,
            ),
            max_retries=0,          # ← 关键：传输层重试由本模块统一管
        )
    return _client


# ══════════════════════════════════════════════════════════
# 错误分类
# ══════════════════════════════════════════════════════════
def _is_retryable(exc: Exception) -> bool:
    """只有「再试一次可能成功」的错误才值得重试。

    401 鉴权、402 余额、400 参数错 —— 重发一百次结果一样。
    """
    try:
        import openai
    except ImportError:
        return False

    if isinstance(exc, openai.APIConnectionError):      # 含 APITimeoutError
        return True
    if isinstance(exc, openai.APIStatusError):          # 所有 HTTP 错误都有 status_code
        return exc.status_code >= 500 or exc.status_code == 429
    return False


# ══════════════════════════════════════════════════════════
# JSON 提取（三级兜底）
# ══════════════════════════════════════════════════════════
def extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON。

    模型经常不老实：包在 ```json 围栏里、前面加一句「好的，以下是结果」。

    第 ③ 级用 JSONDecoder.raw_decode，而不是「第一个 { 到最后一个 }」——
    后者在「字符串里含花括号」或「后面还跟着别的片段」时会崩。
    """
    s = (text or "").strip()
    if not s:
        raise LLMEmptyResponse("模型返回空内容")

    # ① 本来就是纯 JSON
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # ② 剥 markdown 围栏（奇数下标才是围栏里的内容）
    if "```" in s:
        parts = s.split("```")
        for i in range(1, len(parts), 2):
            body = parts[i].strip()
            if body[:4].lower() == "json":
                body = body[4:].strip()
            if body.startswith("{"):
                try:
                    obj = json.loads(body)
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    continue

    # ③ 逐个 { 位置尝试 raw_decode
    start = s.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(s, start)
            if isinstance(obj, dict) and obj:
                return obj
        except json.JSONDecodeError:
            pass
        start = s.find("{", start + 1)

    raise LLMError(f"无法从模型输出里解析出 JSON（前 200 字）：{s[:200]}")


# ══════════════════════════════════════════════════════════
# 退避
# ══════════════════════════════════════════════════════════
def _sleep_backoff(attempt: int) -> None:
    """指数退避 + 抖动。批改本来就要等几十秒，退避不能设太狠。"""
    time.sleep(min((2 ** attempt) * 0.8 + random.uniform(0, 0.4), 8.0))


# ══════════════════════════════════════════════════════════
# 主调用
# ══════════════════════════════════════════════════════════
def chat(
    system: str,
    user: str,
    thinking: bool = False,
    json_mode: bool = False,
    *,
    model: str = MODEL,
    temperature: float | None = None,
    max_tokens: int = 8192,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> str:
    """调一次模型，返回文本内容。

    :param thinking:    思考模式。**开着时 temperature 无效**
    :param json_mode:   开 JSON 模式（会强制 response_format）
    :param temperature: 只在 thinking=False 时生效
    """
    # 防御：DeepSeek JSON 模式的硬要求（提示词里必须有 "json" 字样）。
    # 将来改提示词容易把这句删掉，然后系统开始随机失败且看不出原因。
    if json_mode and "json" not in (system + user).lower():
        system += '\n\n请严格以 JSON 格式输出。'

    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        # 思考开关必须放 extra_body，不是顶层参数
        "extra_body": {"thinking": {"type": "enabled" if thinking else "disabled"}},
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    # 思考模式下 temperature 被忽略，所以只在关思考时传
    if temperature is not None and not thinking:
        kwargs["temperature"] = temperature

    last_error: Exception | None = None

    for attempt in range(max_attempts):
        if attempt > 0:
            _sleep_backoff(attempt - 1)

        try:
            resp = _get_client().chat.completions.create(**kwargs)
        except Exception as exc:                        # noqa: BLE001
            last_error = exc
            if not _is_retryable(exc) or attempt == max_attempts - 1:
                raise LLMError(f"调用 {model} 失败：{exc}") from exc
            continue

        choice = resp.choices[0]

        # ① 截断：重试无用，直接抛
        if getattr(choice, "finish_reason", None) == "length":
            raise LLMTruncated(
                f"输出被 max_tokens={max_tokens} 截断。重试无用，请调大 max_tokens。"
            )

        content = (choice.message.content or "").strip()

        # ② 空返回：官方已知问题，值得重试
        if not content:
            last_error = LLMEmptyResponse("模型返回空内容（DeepSeek 已知问题）")
            if attempt == max_attempts - 1:
                raise last_error
            continue

        return content

    raise LLMError(f"重试 {max_attempts} 次仍失败：{last_error}")


def chat_json(
    system: str,
    user: str,
    thinking: bool = False,
    *,
    model: str = MODEL,
    temperature: float | None = None,
    max_tokens: int = 8192,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict:
    """要结构化输出，直接返回已解析的 dict。

    解析失败会重试（把「上次输出不是合法 JSON」作为线索）。
    """
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        try:
            text = chat(
                system, user, thinking, json_mode=True,
                model=model, temperature=temperature,
                max_tokens=max_tokens, max_attempts=1,   # 单次，重试在下面统一管
            )
            return extract_json(text)
        except (LLMError, ValueError) as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                break
            _sleep_backoff(attempt)

    raise LLMError(f"重试 {max_attempts} 次仍未取到合法 JSON：{last_error}")


# ══════════════════════════════════════════════════════════
# 自测：不需要 API Key
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    dirty = [
        ("纯 JSON", '{"a": 1}'),
        ("围栏包裹", '```json\n{"a": 1}\n```'),
        ("前后有废话", '好的，结果如下：\n{"a": 1}\n希望对你有帮助。'),
        ("字符串含花括号", '{"a": "值里有 { 和 } 两个符号", "b": 2}'),
        ("后面跟着别的片段", '{"a": 1}\n\n另外 {"b": 2}'),
        ("空内容", ''),
        ("完全不是 JSON", '抱歉，我无法完成这个请求。'),
    ]
    for name, text in dirty:
        try:
            print(f"  OK  {name:20s} → {extract_json(text)}")
        except LLMError as exc:
            print(f"  ERR {name:20s} → {type(exc).__name__}")
