"""流水线编排：① 源配置 → ③ 采集 → ④ 清洗/去重/筛选 → ⑤ 输出。

用法：
    python main.py                      # 全量跑一遍（新闻 + 岗位）
    python main.py --only news          # 只跑新闻
    python main.py --only jobs --max-pages 1 --keywords 算法
    python main.py --no-push            # 不推送（只写 Markdown）
    python main.py --limit-feeds 5      # 只取前 5 个源，调试用
    python main.py --only brief         # 只跑学科前沿简报（Tavily）
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import yaml

import brief
import demand
from collectors import jobs as jobs_collector
from collectors import rss as rss_collector
from collectors.opml import load_opml_dir
from filter import (dedupe_news, dedupe_positions, keyword_filter,
                    llm_filter, llm_filter_news, prefilter_news)
from output.markdown import render_digest, write_digest
from push import push_feishu
from storage import Store

TZ = timezone(timedelta(hours=8))


def load_yaml(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or (default if default is not None else {})
    except FileNotFoundError:
        print(f"[config] 找不到 {path}，使用默认值")
        return default if default is not None else {}


def build_feeds(sources: dict, limit: int = 0) -> list[dict]:
    """news 直连源 + OPML 包 + 招聘 RSS，统一成 {name,url,category,kind}。"""
    feeds: list[dict] = []

    for f in sources.get("news") or []:
        feeds.append({"name": f.get("name") or f["url"], "url": f["url"],
                      "category": f.get("category", ""), "kind": "news"})

    opml_dir = sources.get("opml_dir")
    if opml_dir:
        for f in load_opml_dir(opml_dir):
            feeds.append({"name": f["name"], "url": f["url"],
                          "category": f.get("category", ""), "kind": "news"})

    for f in (sources.get("jobs") or {}).get("rss") or []:
        feeds.append({"name": f.get("name") or f["url"], "url": f["url"],
                      "category": f.get("category", ""), "kind": "job"})

    # 同一个 URL 只留一次（OPML 和 yaml 里可能重复）
    deduped, seen = [], set()
    for f in feeds:
        key = f["url"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)

    return deduped[:limit] if limit else deduped


async def run(args) -> int:
    cfg = load_yaml(args.config)
    sources = load_yaml(args.sources)
    fetch_cfg = cfg.get("fetch") or {}
    interval = float(fetch_cfg.get("request_interval", 0.4))

    store = None if args.no_store else Store(cfg.get("db_path", "seen.db"))
    today = datetime.now(TZ).strftime("%Y-%m-%d")

    news_items, job_feed_items, positions = [], [], []
    raw_total = 0

    # ---------- ③ 采集 ----------
    if args.only in ("all", "news"):
        feeds = build_feeds(sources, limit=args.limit_feeds)
        print(f"[main] 订阅源 {len(feeds)} 个")
        raw = await rss_collector.collect(
            feeds,
            concurrency=int(fetch_cfg.get("rss_concurrency", 6)),
            request_interval=interval,
        )
        raw_total += len(raw)
        news_raw = [i for i in raw if i.kind == "news"]
        job_raw = [i for i in raw if i.kind == "job"]

        # ---------- ④ 清洗 → 去重 → 未见 ----------
        ncfg = cfg.get("news_filter") or {}
        news_items = prefilter_news(
            news_raw,
            include=ncfg.get("include"),
            exclude=ncfg.get("exclude"),
            min_title_len=int(ncfg.get("min_title_len", 6)),
        )
        news_items = dedupe_news(news_items)
        job_feed_items = dedupe_news(job_raw)
        if store:
            news_items = store.filter_new_items(news_items)
            job_feed_items = store.filter_new_items(job_feed_items)
        print(f"[main] 新闻 {len(news_raw)} → 过滤后 {len(news_items)}；招聘 RSS {len(job_feed_items)}")

    if args.only in ("all", "jobs"):
        job_cfg = sources.get("jobs") or {}
        keywords = args.keywords.split(",") if args.keywords else (cfg.get("keywords") or [])
        max_pages = args.max_pages or int(fetch_cfg.get("job_max_pages", 3))
        positions = await jobs_collector.collect(
            job_cfg.get("providers") or [],
            keywords,
            max_pages=max_pages,
            request_interval=interval,
        )
        raw_total += len(positions)

        positions = dedupe_positions(positions)
        if store:
            positions = store.filter_new_positions(positions)
        positions = keyword_filter(positions, cfg.get("category_allow") or [])
        print(f"[main] 岗位 {len(positions)} 条待 LLM")

    # ---------- ⑥ 学科前沿简报（Tavily，独立产物） ----------
    brief_result = None
    bcfg = cfg.get("brief") or {}
    if args.only in ("all", "brief") and bcfg.get("enabled", True):
        brief_result = await brief.run(cfg, sources, date_str=today)
        if (not args.no_push and bcfg.get("push")
                and brief_result.get("markdown")):
            sent = await push_feishu(brief_result["markdown"], cfg.get("feishu_webhook", ""))
            print(f"[main] 简报推送：{'已发送' if sent else '跳过（未配置 webhook）'}")

    if args.only == "brief":
        if store:
            store.close()
        return 0

    # ---------- ④ LLM（最后一步，可选） ----------
    llm_cfg = cfg.get("llm") or {}
    if not args.no_llm and llm_cfg.get("enabled"):
        if news_items:
            news_items = await llm_filter_news(news_items, llm_cfg)
        if positions:
            positions = await llm_filter(positions, llm_cfg)
        print(f"[main] LLM 后：新闻 {len(news_items)} · 岗位 {len(positions)}")

    # ---------- ⑤ 需求画像 ----------
    dcfg = cfg.get("demand") or {}
    demand_skills, demand_total, demand_path = None, 0, None
    if dcfg.get("enabled", True) and positions:
        demand.register_skills(dcfg.get("extra_skills") or [])
        agg = demand.aggregate(positions, min_count=int(dcfg.get("min_count", 2)))
        report = demand.render_report(today, agg, top_n=int(dcfg.get("top_n", 25)))
        demand_path = demand.write_report(cfg.get("digest_dir", "digests"), today, report)
        demand_skills, demand_total = demand.summary_table(agg, 12), agg["total"]
        print(f"[main] 需求画像 {demand_path}（样本 {agg['total']}）")

    # ---------- ⑤ 输出 ----------
    # 只截断展示/推送，入库仍然用全量，避免明天又冒出来
    ocfg = cfg.get("output") or {}
    shown_news = news_items[:int(ocfg.get("max_news", 80))]
    shown_jobs = positions[:int(ocfg.get("max_jobs", 150))]
    shown_feeds = job_feed_items[:int(ocfg.get("max_job_rss", 40))]
    stats = {"采集": raw_total, "展示": f"{len(shown_news)}/{len(shown_jobs)}"}
    content = render_digest(today, shown_news, shown_jobs, shown_feeds, stats=stats,
                            demand_skills=demand_skills, demand_total=demand_total)
    path = write_digest(cfg.get("digest_dir", "digests"), today, content)
    print(f"[main] 写入 {path}")

    if not args.no_push:
        sent = await push_feishu(content, cfg.get("feishu_webhook", ""))
        print(f"[main] 推送：{'已发送' if sent else '跳过（未配置 webhook）'}")

    if store:
        store.mark_items(news_items + job_feed_items)
        store.mark_seen(positions)
        store.close()

    print(f"[main] 完成：新闻 {len(shown_news)}/{len(news_items)} · 岗位 {len(shown_jobs)}/{len(positions)}"
          f" · 招聘 RSS {len(shown_feeds)}/{len(job_feed_items)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="科技新闻 + 校招岗位雷达")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--sources", default="sources.yaml")
    parser.add_argument("--only", choices=["all", "news", "jobs", "brief"], default="all")
    parser.add_argument("--max-pages", type=int, default=0, help="覆盖每家公司每关键词的翻页数")
    parser.add_argument("--keywords", default="", help="逗号分隔，覆盖 config 里的 keywords")
    parser.add_argument("--limit-feeds", type=int, default=0, help="只跑前 N 个订阅源")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--no-store", action="store_true", help="不写去重库（调试用）")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())