"""带来源的聚合层：把各家招聘 API / RSS 统一成同一种 JSON，并标注来源。

被 api.py（HTTP + 函数接口）和主流水线共用。每条数据都带 source 字段：
    provider / label / type / site / endpoint / fetched_at
"""

import asyncio
import importlib
import time
from datetime import datetime, timedelta, timezone

import yaml

import demand
from collectors import rss as rss_collector
from collectors.opml import load_opml_dir
from filter import clean_text
from models import NewsItem, Position

TZ = timezone(timedelta(hours=8))

# 招聘来源注册表：label 给人看，endpoint 给排查用
REGISTRY: dict[str, dict] = {
    "huawei": {
        "label": "华为", "type": "api", "campus": True,
        "site": "https://career.huawei.com/cn/",
        "endpoint": "https://apigw-dgg-b0.huawei.com/api/apig/channelhw/recruitmentPosition/pub/getJobPage",
    },
    "bytedance": {
        "label": "字节跳动", "type": "api", "campus": True,
        "site": "https://jobs.bytedance.com/campus",
        "endpoint": "https://jobs.bytedance.com/api/v1/search/job/posts",
    },
    "tencent": {
        "label": "腾讯", "type": "api", "campus": True,
        "site": "https://join.qq.com/",
        "endpoint": "https://join.qq.com/api/v1/position/searchPosition",
    },
    "meituan": {
        "label": "美团", "type": "api", "campus": True,
        "site": "https://zhaopin.meituan.com/web/position",
        "endpoint": "https://zhaopin.meituan.com/api/official/job/getJobList",
    },
    "xiaomi": {
        "label": "小米", "type": "api", "campus": True,
        "site": "https://xiaomi.jobs.f.mioffice.cn/campus",
        "endpoint": "https://xiaomi.jobs.f.mioffice.cn/api/v1/search/job/posts",
    },
    "netease": {
        "label": "网易", "type": "api", "campus": True,
        "site": "https://hr.163.com/job-list.html",
        "endpoint": "https://hr.163.com/api/hr163/position/queryPage",
    },
    "nio": {
        "label": "蔚来", "type": "api", "campus": True,
        "site": "https://nio.jobs.feishu.cn/campus",
        "endpoint": "https://nio.jobs.feishu.cn/api/v1/search/job/posts",
    },
    "minimax": {
        "label": "MiniMax", "type": "api", "campus": True,
        "site": "https://vrfi1sk8a0.jobs.feishu.cn/379481",
        "endpoint": "https://vrfi1sk8a0.jobs.feishu.cn/api/v1/search/job/posts",
    },
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def list_sources() -> list[dict]:
    return [{"provider": k, **v} for k, v in REGISTRY.items()]


def position_to_dict(p: Position, provider: str) -> dict:
    meta = REGISTRY.get(provider, {})
    return {
        "company": p.company,
        "company_label": meta.get("label", p.company),
        "job_id": p.job_id,
        "title": clean_text(p.title, 200),
        "city": p.city,
        "category": p.category,
        "url": p.url,
        "responsibilities": p.responsibilities,
        "requirements": p.requirements,
        "source": {
            "provider": provider,
            "label": meta.get("label", provider),
            "type": meta.get("type", "api"),
            "site": meta.get("site", ""),
            "endpoint": meta.get("endpoint", ""),
            "fetched_at": now_iso(),
        },
    }


def news_to_dict(item: NewsItem) -> dict:
    return {
        "kind": item.kind,
        "title": clean_text(item.title, 300),
        "url": item.url,
        "summary": clean_text(item.summary, 500),
        "category": item.category,
        "published": item.published.isoformat() if item.published else None,
        "source": {
            "name": item.source,
            "type": item.kind,
            "feed_url": item.feed_url,
            "fetched_at": now_iso(),
        },
    }


# ------------------------------------------------------------------
# 采集：返回原始 Position + 每家来源的状态，供 jobs / demand 复用
# ------------------------------------------------------------------

async def collect_positions(keywords: list[str] | str = "",
                            companies: list[str] | None = None,
                            max_pages: int = 1, page_size: int = 20,
                            request_interval: float = 0.4,
                            timeout: float = 120) -> dict:
    if isinstance(keywords, str):
        keywords = [keywords] if keywords else [""]
    keywords = keywords or [""]
    names = companies or list(REGISTRY)
    started = time.perf_counter()

    async def run_one(name: str, keyword: str):
        t0 = time.perf_counter()
        try:
            mod = importlib.import_module(f"providers.{name}")
            jobs = await asyncio.wait_for(
                mod.fetch(keyword=keyword, max_pages=max_pages, page_size=page_size,
                          request_interval=request_interval),
                timeout=timeout,
            )
            return {"provider": name, "keyword": keyword, "ok": True, "count": len(jobs),
                    "error": None, "elapsed_ms": int((time.perf_counter() - t0) * 1000)}, jobs
        except Exception as e:
            return {"provider": name, "keyword": keyword, "ok": False, "count": 0,
                    "error": f"{type(e).__name__}: {str(e)[:200]}",
                    "elapsed_ms": int((time.perf_counter() - t0) * 1000)}, []

    results = await asyncio.gather(*(run_one(n, kw) for kw in keywords for n in names))

    positions: list[tuple[str, Position]] = []
    seen: set[tuple[str, str]] = set()
    stats: dict[str, dict] = {}
    for stat, jobs in results:
        s = stats.setdefault(stat["provider"], {
            "provider": stat["provider"],
            "label": REGISTRY.get(stat["provider"], {}).get("label", stat["provider"]),
            "ok": True, "count": 0, "errors": [], "elapsed_ms": 0,
        })
        s["elapsed_ms"] = max(s["elapsed_ms"], stat["elapsed_ms"])
        if not stat["ok"]:
            s["ok"] = False
            s["errors"].append(stat["error"])
        for p in jobs:
            key = (p.company, p.job_id)
            if key in seen:
                continue
            seen.add(key)
            positions.append((stat["provider"], p))
            s["count"] += 1

    return {
        "positions": positions,
        "sources": list(stats.values()),
        "took_ms": int((time.perf_counter() - started) * 1000),
        "fetched_at": now_iso(),
    }


async def aggregate_jobs(keywords: list[str] | str = "", companies: list[str] | None = None,
                         max_pages: int = 1, page_size: int = 20,
                         request_interval: float = 0.4, limit: int = 0) -> dict:
    """聚合岗位，返回带来源标注的列表。"""
    raw = await collect_positions(keywords=keywords, companies=companies,
                                  max_pages=max_pages, page_size=page_size,
                                  request_interval=request_interval)
    items = [position_to_dict(p, provider) for provider, p in raw["positions"]]
    if limit:
        items = items[:limit]
    return {
        "items": items,
        "total": len(items),
        "sources": raw["sources"],
        "took_ms": raw["took_ms"],
        "fetched_at": raw["fetched_at"],
    }


async def aggregate_demand(keywords: list[str] | str = "", companies: list[str] | None = None,
                           max_pages: int = 1, page_size: int = 20,
                           request_interval: float = 0.4,
                           min_count: int = 2, top_n: int = 25) -> dict:
    """同一批岗位直接出需求画像（结构化 + Markdown）。"""
    raw = await collect_positions(keywords=keywords, companies=companies,
                                  max_pages=max_pages, page_size=page_size,
                                  request_interval=request_interval)
    positions = [p for _, p in raw["positions"]]
    if not positions:
        return {"skills": [], "total": 0, "report": "", "sources": raw["sources"],
                "took_ms": raw["took_ms"], "fetched_at": raw["fetched_at"]}

    agg = demand.aggregate(positions, min_count=min_count)
    report = demand.render_report(now_iso()[:10], agg, top_n=top_n)
    return {
        "total": agg["total"],
        "with_jd": agg["with_jd"],
        "skills": [{"name": k, "count": v, "ratio": round(v / agg["total_for_ratio"], 3)}
                   for k, v in agg["skills"]],
        "directions": [{"name": k, "count": v} for k, v in agg["directions"]],
        "degrees": [{"name": k, "count": v} for k, v in agg["degrees"]],
        "majors": [{"name": k, "count": v} for k, v in agg["majors"]],
        "cities": [{"name": k, "count": v} for k, v in agg["cities"]],
        "company_skills": {c: [{"name": k, "count": v} for k, v in s]
                           for c, s in agg["company_skills"].items()},
        "report": report,
        "sources": raw["sources"],
        "took_ms": raw["took_ms"],
        "fetched_at": raw["fetched_at"],
    }


# ------------------------------------------------------------------
# 新闻
# ------------------------------------------------------------------

def build_feeds(sources: dict) -> list[dict]:
    feeds = [{"name": f.get("name") or f["url"], "url": f["url"],
              "category": f.get("category", ""), "kind": "news"}
             for f in sources.get("news") or []]
    if sources.get("opml_dir"):
        feeds += [{"name": f["name"], "url": f["url"], "category": f.get("category", ""),
                   "kind": "news"} for f in load_opml_dir(sources["opml_dir"])]
    feeds += [{"name": f.get("name") or f["url"], "url": f["url"],
               "category": f.get("category", ""), "kind": "job"}
              for f in (sources.get("jobs") or {}).get("rss") or []]
    deduped, seen = [], set()
    for f in feeds:
        if f["url"] in seen:
            continue
        seen.add(f["url"])
        deduped.append(f)
    return deduped


async def aggregate_news(sources_file: str = "sources.yaml", source: str = "",
                         limit: int = 50, concurrency: int = 6) -> dict:
    try:
        with open(sources_file, "r", encoding="utf-8") as f:
            sources = yaml.safe_load(f) or {}
    except FileNotFoundError:
        sources = {}

    feeds = build_feeds(sources)
    if source:
        feeds = [f for f in feeds if source.lower() in f["name"].lower()]

    started = time.perf_counter()
    items = await rss_collector.collect(feeds, concurrency=concurrency, request_interval=0.2)
    if limit:
        items = items[:limit]

    stats: dict[str, dict] = {}
    for f in feeds:
        stats[f["name"]] = {"name": f["name"], "url": f["url"], "category": f.get("category", ""),
                            "kind": f.get("kind", "news"), "count": 0}
    for i in items:
        s = stats.get(i.source)
        if s:
            s["count"] += 1

    return {
        "items": [news_to_dict(i) for i in items],
        "total": len(items),
        "sources": list(stats.values()),
        "took_ms": int((time.perf_counter() - started) * 1000),
        "fetched_at": now_iso(),
    }
