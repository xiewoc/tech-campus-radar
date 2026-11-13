"""Tavily 搜索客户端（纯 httpx，不引 SDK）。

用法：
    from tavily import search, search_many
    res = await search("large language model reasoning", max_results=5)
    batch = await search_many(topics, concurrency=3)

API key 从 config 的 tavily.api_key 或环境变量 TAVILY_API_KEY 读取。
"""

import asyncio
import os

import httpx

ENDPOINT = "https://api.tavily.com/search"
RETRY_STATUS = {429, 500, 502, 503, 504}


class TavilyError(RuntimeError):
    pass


def get_api_key(explicit: str = "") -> str:
    return (explicit or os.getenv("TAVILY_API_KEY", "")).strip()


def _normalize(body: dict, query: str) -> dict:
    results = []
    for r in body.get("results") or []:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        results.append({
            "title": (r.get("title") or "").strip(),
            "url": url,
            "content": (r.get("content") or "").strip(),
            "score": r.get("score"),
            "published": _norm_date(r.get("published_date") or r.get("published") or ""),
            "domain": _domain(url),
        })
    return {
        "query": query,
        "answer": body.get("answer") or "",
        "results": results,
    }


def _norm_date(value) -> str:
    """把 Tavily 返回的多种日期格式统一成 YYYY-MM-DD。"""
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # 已经是 2026-11-07 或 ISO
    m = __import__("re").match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return m.group(0)
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(text).strftime("%Y-%m-%d")
    except Exception:
        return text


def _domain(url: str) -> str:
    try:
        return url.split("//", 1)[1].split("/", 1)[0].replace("www.", "")
    except IndexError:
        return ""


async def search(query: str, api_key: str = "", max_results: int = 8,
                 search_depth: str = "advanced", topic: str = "news",
                 days: int = 7, include_domains: list[str] | None = None,
                 include_answer: bool = False, timeout: float = 40,
                 retries: int = 3, **_) -> dict:
    key = get_api_key(api_key)
    if not key:
        raise TavilyError("缺少 TAVILY_API_KEY（config.tavily.api_key 或环境变量）")

    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "topic": topic,
        "include_answer": include_answer,
        "include_raw_content": False,
    }
    if topic == "news" and days:
        payload["days"] = days
    if include_domains:
        payload["include_domains"] = include_domains[:20]

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    last_error = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(retries):
            try:
                resp = await client.post(ENDPOINT, json=payload, headers=headers)
                if resp.status_code == 401:
                    raise TavilyError("Tavily key 无效或已过期（401）")
                if resp.status_code in RETRY_STATUS:
                    last_error = f"HTTP {resp.status_code}"
                    await asyncio.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                return _normalize(resp.json(), query)
            except TavilyError:
                raise
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                await asyncio.sleep(2 ** attempt)
    raise TavilyError(f"Tavily 请求失败：{last_error}")


async def search_many(topics: list[dict], concurrency: int = 3, **kwargs) -> list[dict]:
    """按主题并发搜索；单个主题失败不影响其它主题。"""
    sem = asyncio.Semaphore(concurrency)

    async def one(topic: dict):
        try:
            async with sem:
                res = await search(
                    topic["query"],
                    max_results=topic.get("max_results", kwargs.get("max_results", 8)),
                    search_depth=topic.get("search_depth", kwargs.get("search_depth", "advanced")),
                    topic=topic.get("topic", kwargs.get("topic", "news")),
                    days=topic.get("days", kwargs.get("days", 7)),
                    include_domains=topic.get("domains"),
                    api_key=kwargs.get("api_key", ""),
                )
            return {**topic, "ok": True, "error": None, "results": res["results"],
                    "answer": res["answer"]}
        except Exception as e:
            return {**topic, "ok": False, "error": f"{type(e).__name__}: {e}", "results": [],
                    "answer": ""}

    return await asyncio.gather(*(one(t) for t in topics))