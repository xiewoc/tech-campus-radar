#!/usr/bin/env python3
"""
[已并入 collectors/rss.py] 此脚本仅保留作参照，功能已由主流水线覆盖：
    python main.py --only news
- RSS 抓取/解析  → collectors/rss.py（含 OPML）
- 去重存储       → storage.py 的 items 表（统一库，不再单独用 frontend_news.db）

原说明：前端科技新闻定时爬取
- RSS 优先，feedparser 解析
- SQLite 存储，链接唯一约束 + UPSERT 去重
- 单源失败隔离，指数退避重试
- 只保留元数据，不抓全文
"""

import logging
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

# ========================= 配置区 =========================

DB_PATH = Path("frontend_news.db")

# 前端/科技新闻 RSS 源
# 来源：awesome-rsshub-routes 前端设计分类、front-end-rss 项目等
FEEDS = [
    # 技术周刊 / 前端社区
    ("JavaScript Weekly", "https://javascriptweekly.com/rss/"),
    ("Frontend Focus", "https://frontendfoc.us/rss/"),
    ("Node Weekly", "https://nodeweekly.com/rss/"),
    # 大厂 / 团队博客（通过 RSSHub 或官方源，按需替换）
    ("web.dev Blog", "https://web.dev/feed.xml"),
    ("MDN Blog", "https://developer.mozilla.org/en-US/blog/rss.xml"),
    # 国际科技媒体（科技板块）
    ("TechCrunch", "https://techcrunch.com/feed/"),          # 硅谷科技新闻
    ("The Verge", "https://www.theverge.com/rss/index.xml"), # 科技与文化
    # 社区聚合
    ("Hacker News Front Page", "https://hnrss.org/frontpage"),
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

MAX_RETRIES = 3
BACKOFF_BASE = 1.5  # 秒，指数退避基数

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ========================= 数据库 =========================

def init_db(conn: sqlite3.Connection) -> None:
    """初始化表结构。link 唯一约束是去重的核心。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            link        TEXT NOT NULL UNIQUE,
            title       TEXT NOT NULL,
            source      TEXT NOT NULL,
            published   TEXT,
            summary     TEXT,
            fetched_at  TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_entries_published ON entries(published DESC)"
    )
    conn.commit()


def upsert_entry(conn: sqlite3.Connection, item: dict) -> bool:
    """
    插入条目；若 link 已存在则忽略。
    返回 True 表示新插入，False 表示已存在。
    """
    try:
        conn.execute(
            """
            INSERT INTO entries (link, title, source, published, summary, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(link) DO NOTHING
            """,
            (
                item["link"],
                item["title"],
                item["source"],
                item.get("published"),
                item.get("summary"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return conn.total_changes > 0
    except sqlite3.Error as e:
        logger.warning("写入失败 %s: %s", item.get("link"), e)
        return False


# ========================= 抓取 =========================

def fetch_feed_with_retry(feed_url: str) -> requests.Response | None:
    """带指数退避的 HTTP 获取。失败返回 None，不抛异常。"""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(feed_url, headers=headers, timeout=20)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = BACKOFF_BASE ** attempt + random.uniform(0, 0.5)
                logger.info("第 %d 次重试 %s（状态 %d），等待 %.1fs", attempt, feed_url, resp.status_code, wait)
                time.sleep(wait)
                continue
            logger.warning("非重试状态码 %d: %s", resp.status_code, feed_url)
            return None
        except requests.RequestException as e:
            wait = BACKOFF_BASE ** attempt + random.uniform(0, 0.5)
            logger.warning("请求异常 %s（第 %d 次）：%s，等待 %.1fs", feed_url, attempt, e, wait)
            time.sleep(wait)
    logger.error("放弃 %s，已达最大重试次数", feed_url)
    return None


def parse_entries(resp: requests.Response, source: str) -> list[dict]:
    """解析 RSS/Atom，返回标准化条目列表。"""
    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        logger.warning("解析失败（bozo）：%s", source)
        return []

    entries = []
    for entry in feed.entries:
        link = entry.get("link") or entry.get("id")
        title = entry.get("title", "").strip()
        if not link or not title:
            continue

        published = None
        for key in ("published_parsed", "updated_parsed"):
            parsed = entry.get(key)
            if parsed:
                try:
                    published = datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()
                except (TypeError, ValueError):
                    pass
                break

        summary = entry.get("summary", "")[:500]  # 截断，只存摘要

        entries.append({
            "link": link,
            "title": title,
            "source": source,
            "published": published,
            "summary": summary,
        })
    return entries


# ========================= 主流程 =========================

def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)

        total_new = 0
        total_fetched = 0

        for source, url in FEEDS:
            logger.info("抓取：%s", source)
            resp = fetch_feed_with_retry(url)
            if resp is None:
                continue

            entries = parse_entries(resp, source)
            total_fetched += len(entries)

            for item in entries:
                if upsert_entry(conn, item):
                    total_new += 1

            logger.info("  %s：解析 %d 条", source, len(entries))

        logger.info("完成。共解析 %d 条，新增 %d 条。", total_fetched, total_new)
    finally:
        conn.close()


if __name__ == "__main__":
    main()