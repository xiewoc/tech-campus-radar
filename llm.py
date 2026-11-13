"""可插拔的 completion 层：httpx 直连 OpenAI 兼容接口，不依赖 openai SDK。

配置（config.yaml 的 llm 段，全部可留空）：

    llm:
      provider: openai_compatible   # 也可以是你自己注册的 provider
      base_url: "https://api.openai.com/v1"
      api_key: ""                   # 留空读环境变量 OPENAI_API_KEY
      model: "gpt-4o-mini"
      temperature: 0.2
      max_tokens: 2048
      json_mode: true
      timeout: 60

接入自定义端点（任何兼容 /chat/completions 的服务）：

    import llm
    llm.register("my_proxy")(MyProvider)      # 继承 llm.OpenAICompatible 即可
    cfg = llm.LLMConfig(provider="my_proxy", base_url="https://my.proxy/v1", api_key="sk-...")

直接调用：

    from llm import complete, complete_json
    res = await complete([{"role": "user", "content": "hi"}], cfg)
    data = await complete_json(messages, cfg)     # 强制 JSON 模式并解析
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

TZ = timezone(timedelta(hours=8))
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
FENCE = chr(96) * 3          # 三个反引号，用 chr 拼出来避免源码里出现裸反引号


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class LLMConfig:
    provider: str = "openai_compatible"
    base_url: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    max_tokens: int | None = 2048
    timeout: float = 60.0
    json_mode: bool = False
    retries: int = 3
    extra_headers: dict = field(default_factory=dict)

    @property
    def endpoint(self) -> str:
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        return f"{base}/chat/completions"


@dataclass
class Completion:
    text: str
    model: str = ""
    provider: str = ""
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    latency_ms: int = 0
    created_at: str = ""

    def json(self) -> dict:
        """把模型输出当 JSON 解析（容忍 code fence 包裹或前后废话）。"""
        text = (self.text or "").strip()
        if text.startswith(FENCE):
            text = text[len(FENCE):]
            if text.lower().startswith("json"):
                text = text[4:]
            if text.rstrip().endswith(FENCE):
                text = text.rstrip()[:-len(FENCE)]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            raise LLMError(f"模型输出不是合法 JSON：{e}") from e


def config_from(cfg: dict | None, env: str = "OPENAI_API_KEY") -> LLMConfig:
    """从 config.yaml 的 llm 段构造配置；key 缺失时读环境变量。"""
    cfg = dict(cfg or {})
    return LLMConfig(
        provider=cfg.get("provider", "openai_compatible"),
        base_url=cfg.get("base_url", "") or "",
        api_key=cfg.get("api_key") or os.getenv(env, ""),
        model=cfg.get("model", "gpt-4o-mini"),
        temperature=float(cfg.get("temperature", 0.2)),
        max_tokens=cfg.get("max_tokens", 2048),
        timeout=float(cfg.get("timeout", 60)),
        json_mode=bool(cfg.get("json_mode", False)),
        retries=int(cfg.get("retries", 3)),
        extra_headers=cfg.get("extra_headers") or {},
    )


def available(cfg: dict | LLMConfig | None) -> bool:
    """是否具备调用条件：有 key，或指向自建/本地端点（通常无需鉴权），或 mock。"""
    conf = cfg if isinstance(cfg, LLMConfig) else config_from(cfg)
    if conf.provider == "mock":
        return True
    if conf.api_key:
        return True
    return bool(conf.base_url) and "api.openai.com" not in conf.base_url


class Provider:
    """provider 基类。实现 chat() 即可接入任意服务。"""

    name = "base"

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    async def chat(self, messages: list[dict], **overrides) -> Completion:
        raise NotImplementedError


_PROVIDERS: dict[str, type[Provider]] = {}


def register(name: str | None = None):
    """llm.register("my_provider") 装饰器。"""
    def deco(cls: type[Provider]):
        _PROVIDERS[name or cls.name] = cls
        return cls
    return deco


def providers() -> list[str]:
    return sorted(_PROVIDERS)


def build(cfg: LLMConfig) -> Provider:
    cls = _PROVIDERS.get(cfg.provider)
    if cls is None:
        raise LLMError(f"未知 provider: {cfg.provider}（可用：{', '.join(providers())}）")
    return cls(cfg)


@register("openai_compatible")
class OpenAICompatible(Provider):
    """任何兼容 POST {base_url}/chat/completions 的服务。"""

    name = "openai_compatible"

    def __init__(self, cfg: LLMConfig):
        super().__init__(cfg)
        # 官方端点必须带 key；自建/本地端点允许匿名
        if not cfg.api_key and (not cfg.base_url or "api.openai.com" in cfg.base_url):
            raise LLMError("缺少 api_key（config.llm.api_key 或 OPENAI_API_KEY）")

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        headers.update(self.cfg.extra_headers or {})
        return headers

    def payload(self, messages: list[dict], **overrides) -> dict:
        body = {
            "model": overrides.get("model", self.cfg.model),
            "messages": messages,
            "temperature": overrides.get("temperature", self.cfg.temperature),
        }
        max_tokens = overrides.get("max_tokens", self.cfg.max_tokens)
        if max_tokens:
            body["max_tokens"] = max_tokens
        if overrides.get("json_mode", self.cfg.json_mode):
            body["response_format"] = {"type": "json_object"}
        return body

    async def chat(self, messages: list[dict], **overrides) -> Completion:
        body = self.payload(messages, **overrides)
        last_error = None
        started = time.perf_counter()

        async with httpx.AsyncClient(timeout=self.cfg.timeout) as client:
            for attempt in range(max(1, self.cfg.retries)):
                try:
                    resp = await client.post(self.cfg.endpoint, json=body, headers=self._headers())
                except httpx.HTTPError as e:
                    last_error = f"{type(e).__name__}: {e}"
                    await asyncio.sleep(2 ** attempt)
                    continue

                if resp.status_code == 401:
                    raise LLMError("api_key 无效（401）", status=401, body=resp.text[:500])
                if resp.status_code in RETRY_STATUS:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status_code >= 400:
                    raise LLMError(f"请求失败 HTTP {resp.status_code}", status=resp.status_code,
                                   body=resp.text[:500])

                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                return Completion(
                    text=message.get("content") or "",
                    model=data.get("model", body["model"]),
                    provider=self.name,
                    finish_reason=choice.get("finish_reason", ""),
                    usage=data.get("usage") or {},
                    raw=data,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    created_at=datetime.now(TZ).isoformat(timespec="seconds"),
                )

        raise LLMError(f"重试 {self.cfg.retries} 次仍失败：{last_error}")


@register("mock")
class MockProvider(Provider):
    """离线/单测用：把最后一条 user 消息回显，json_mode 时包装成 {"echo": ...}。"""

    name = "mock"

    async def chat(self, messages: list[dict], **overrides) -> Completion:
        text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                text = m.get("content", "")
                break
        out = json.dumps({"echo": text[:200]}, ensure_ascii=False) if (
            overrides.get("json_mode", self.cfg.json_mode)) else text
        return Completion(text=out, model="mock", provider=self.name,
                          finish_reason="stop", created_at=datetime.now(TZ).isoformat(timespec="seconds"))


async def complete(messages: list[dict], cfg: dict | LLMConfig | None = None, **overrides) -> Completion:
    conf = cfg if isinstance(cfg, LLMConfig) else config_from(cfg)
    return await build(conf).chat(messages, **overrides)


async def complete_text(messages: list[dict], cfg: dict | LLMConfig | None = None, **overrides) -> str:
    return (await complete(messages, cfg, **overrides)).text


async def complete_json(messages: list[dict], cfg: dict | LLMConfig | None = None, **overrides) -> dict:
    overrides.setdefault("json_mode", True)
    return (await complete(messages, cfg, **overrides)).json()