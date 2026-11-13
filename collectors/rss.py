"""RSS / Atom 采集：httpx 拉取 + feedparser 解析，单源失败不影响整体。"""

import asyncio
import random
import re
from datetime import datetime, timezone

import feedparser
import httpx

from models import NewsItem

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]
ACCEPT = ("application/rss+xml, application/atom+xml, application/xml, text/xml, */*")
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ITEMS_PER_FEED = 40


def _headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": ACCEPT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _parse_date(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                return None
    return None


_NOISE = re.compile(
    r"(?:Article URL|Comments URL|Points|#\s*Comments)\s*:\s*\S*", re.I
)


_BOILERPLATE = re.compile(r"(?:点击查看原文|阅读原文|Read more|Continue reading)\s*>?", re.I)


def clean_summary(text: str, limit: int = 800) -> str:
    """去 HTML 标签 + HN 那种 "Article URL: … Points: …" 的机读噪声 + 裸链接。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = _NOISE.sub("", text)
    text = re.sub(r"https?://\S+", "", text)
    text = _BOILERPLATE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:limit]


async def fetch_feed(client: httpx.AsyncClient, feed: dict,
                     retries: int = 2, timeout: float = 20) -> list[NewsItem]:
    name = feed.get("name") or feed["url"]
    category = feed.get("category", "")
    kind = feed.get("kind", "news")

    for attempt in range(retries + 1):
        try:
            resp = await client.get(feed["url"], headers=_headers(), timeout=timeout)
            if resp.status_code in RETRY_STATUS:
                await asyncio.sleep(1.5 ** attempt + random.uniform(0, 0.5))
                continue
            resp.raise_for_status()
            parsed = await asyncio.to_thread(feedparser.parse, resp.content)
            if parsed.bozo and not parsed.entries:
                print(f"[rss] 解析为空 {name}: {parsed.get('bozo_exception')}")
                return []
            items: list[NewsItem] = []
            for entry in parsed.entries[:MAX_ITEMS_PER_FEED]:
                url = entry.get("link") or entry.get("id") or ""
                title = (entry.get("title") or "").strip()
                if not url or not title:
                    continue
                items.append(NewsItem(
                    kind=kind,
                    source=name,
                    title=title,
                    url=url,
                    summary=clean_summary(entry.get("summary") or entry.get("description") or ""),
                    category=category,
                    published=_parse_date(entry),
                    feed_url=feed["url"],
                ))
            return items
        except Exception as e:
            if attempt >= retries:
                print(f"[rss] 失败 {name}: {type(e).__name__} {e}")
                return []
            await asyncio.sleep(1.5 ** attempt + random.uniform(0, 0.5))
    return []


async def collect(feeds: list[dict], concurrency: int = 6,
                  request_interval: float = 0.2) -> list[NewsItem]:
    if not feeds:
        return []
    sem = asyncio.Semaphore(concurrency)
    results: list[NewsItem] = []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        async def worker(feed: dict):
            async with sem:
                items = await fetch_feed(client, feed)
                if items:
                    print(f"[rss] {feed.get('name')}: {len(items)}")
                results.extend(items)
                await asyncio.sleep(request_interval)

        await asyncio.gather(*(worker(f) for f in feeds))

    # 发布时间新的排前面（没有时间的排最后）
    results.sort(key=lambda i: i.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return results