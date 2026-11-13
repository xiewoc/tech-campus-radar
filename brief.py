"""每日学科前沿简报：Tavily 检索 → 跨主题去重 → LLM 归纳 → 结构化 Markdown + JSON。

    python main.py --only brief

没有 TAVILY_API_KEY 时直接跳过；没有 LLM key 时退化成抽取式摘要，结构不变。
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import llm
import tavily
from filter import clean_text

TZ = timezone(timedelta(hours=8))

DEFAULT_TOPICS = [
    {"name": "学科前沿", "query": "artificial intelligence research breakthrough", "days": 7},
]


def now() -> datetime:
    return datetime.now(TZ)


def load_topics(sources: dict, cfg: dict | None = None) -> list[dict]:
    topics = (sources or {}).get("topics") or DEFAULT_TOPICS
    cfg = cfg or {}
    return [{
        "name": t.get("name") or t.get("query", "topic"),
        "query": t["query"],
        "domains": t.get("domains"),
        "days": t.get("days", cfg.get("days", 7)),
        "max_results": t.get("max_results", cfg.get("max_results", 8)),
        "search_depth": t.get("search_depth", cfg.get("search_depth", "advanced")),
        "topic": t.get("topic", cfg.get("topic", "news")),
    } for t in topics]


def _dedupe(topics: list[dict]) -> tuple[list[dict], int]:
    """跨主题按 URL 去重，保留第一次出现的归属。"""
    seen: set[str] = set()
    removed = 0
    for t in topics:
        kept = []
        for item in t["results"]:
            key = item["url"].split("#")[0].rstrip("/")
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            kept.append(item)
        t["results"] = kept
    return topics, removed


async def collect(sources: dict, tcfg: dict) -> tuple[list[dict], dict]:
    topics = load_topics(sources, tcfg)
    results = await tavily.search_many(
        topics,
        concurrency=int(tcfg.get("concurrency", 3)),
        api_key=tcfg.get("api_key", ""),
        max_results=int(tcfg.get("max_results", 8)),
        search_depth=tcfg.get("search_depth", "advanced"),
        topic=tcfg.get("topic", "news"),
        days=int(tcfg.get("days", 7)),
    )
    results, removed = _dedupe(results)
    stats = {
        "topics": len(results),
        "ok_topics": sum(1 for t in results if t["ok"]),
        "items": sum(len(t["results"]) for t in results),
        "duplicates_removed": removed,
    }
    return results, stats


def _extractive(topics: list[dict], max_items: int) -> dict:
    sections, overview = [], []
    for t in topics:
        items = []
        for r in t["results"][:max_items]:
            point = clean_text(r["content"], 120)
            items.append({
                "title": r["title"] or r["url"],
                "url": r["url"],
                "domain": r["domain"],
                "published": r["published"],
                "point": point,
                "why": "",
            })
        if items:
            overview.append(f"{t['name']}：{len(t['results'])} 条，最值得看的是《{items[0]['title']}》。")
        sections.append({
            "name": t["name"],
            "query": t["query"],
            "ok": t["ok"],
            "error": t["error"],
            "summary": f"共检索到 {len(t['results'])} 条，" + (f"来自 {len({i['domain'] for i in items})} 个站点。" if items else "无结果。"),
            "items": items,
        })
    return {"overview": overview, "topics": sections, "trends": [], "watch": []}


async def _llm(topics: list[dict], llm_cfg: dict, max_items: int, language: str = "zh") -> dict | None:
    """走 llm 抽象层：provider/base_url/api_key 全由配置决定，这里不关心是哪家服务。"""
    if not llm.available(llm_cfg):
        return None

    payload = []
    for t in topics:
        if not t["ok"] or not t["results"]:
            continue                      # 没结果的主题不喂模型，省 token
        payload.append({
            "topic": t["name"],
            "items": [{
                "title": r["title"],
                "domain": r["domain"],
                "published": r["published"],
                "content": clean_text(r["content"], 320),
                "url": r["url"],
            } for r in t["results"][:max_items]],
        })

    prompt = f"""你是科研情报分析师。下面是今天按主题检索到的前沿信息，请归纳成一份{language}简报。

要求：
1. overview：3-5 条今日速览，每条一句话，说清"发生了什么"。
2. topics：每个主题给 2-3 句 summary，并为其中最重要的 2-4 条写 point（≤40字，讲清结论）和 why（≤30字，讲清为什么重要）。
3. trends：2-4 条跨主题的趋势观察。
4. watch：2-3 条接下来值得追踪的线索。
5. 只能使用给定 materials 里的信息，不要编造；标题和 url 必须原样保留。

只输出 JSON，格式：
{{"overview": ["..."], "topics": [{{"name": "主题名", "summary": "...", "items": [{{"title": "...", "url": "...", "point": "...", "why": "..."}}]}}], "trends": ["..."], "watch": ["..."]}}

materials:
{json.dumps(payload, ensure_ascii=False)}"""

    try:
        return await llm.complete_json(
            [{"role": "user", "content": prompt}], llm_cfg,
            temperature=0.2, max_tokens=2000,
        )
    except Exception as e:
        print(f"[brief] LLM 归纳失败，退回抽取式：{type(e).__name__} {e}")
        return None


def _merge(topics: list[dict], llm_out: dict | None, max_items: int) -> tuple[list[dict], dict]:
    """把 LLM 结果按 URL 回填到原始条目上，确保 title/url 不被模型改写。"""
    by_url: dict[str, dict] = {}
    if llm_out:
        for t in llm_out.get("topics") or []:
            for it in t.get("items") or []:
                if it.get("url"):
                    by_url[it["url"].split("#")[0].rstrip("/")] = it

    sections = []
    for t in topics:
        llm_topic = next((x for x in (llm_out or {}).get("topics") or []
                          if x.get("name") == t["name"]), {})
        items = []
        for r in t["results"][:max_items]:
            extra = by_url.get(r["url"].split("#")[0].rstrip("/"), {})
            items.append({
                "title": r["title"] or extra.get("title") or r["url"],
                "url": r["url"],
                "domain": r["domain"],
                "published": r["published"],
                "point": extra.get("point") or clean_text(r["content"], 120),
                "why": extra.get("why", ""),
            })
        sections.append({
            "name": t["name"],
            "query": t["query"],
            "ok": t["ok"],
            "error": t["error"],
            "summary": llm_topic.get("summary") or f"共检索到 {len(t['results'])} 条。",
            "items": items,
        })
    return sections, {
        "overview": (llm_out or {}).get("overview") or [],
        "trends": (llm_out or {}).get("trends") or [],
        "watch": (llm_out or {}).get("watch") or [],
    }


def render_markdown(brief: dict) -> str:
    meta = brief["stats"]
    lines = [
        f"# 学科前沿简报 · {brief['date']}",
        "",
        f"> Tavily 检索 {meta['topics']} 个主题 · 命中 {meta['items']} 条"
        f"（去重 {meta['duplicates_removed']}） · 生成于 {brief['generated_at'][11:16]}"
        + (f" · 引擎 {meta['engine']}" if meta.get("engine") else ""),
        "",
    ]

    if brief["overview"]:
        lines += ["## 今日速览", ""]
        lines += [f"- {clean_text(x, 160)}" for x in brief["overview"]]
        lines.append("")

    for idx, t in enumerate(brief["topics"], 1):
        lines.append(f"## {'一二三四五六七八九十'[idx - 1] if idx <= 10 else idx}、{t['name']}")
        lines.append("")
        if not t["ok"]:
            lines += [f"_检索失败：{t['error']}_", ""]
            continue
        if t.get("summary"):
            lines += [f"**一句话**：{clean_text(t['summary'], 300)}", ""]
        if not t["items"]:
            lines += ["_本主题今天没有新内容_", ""]
            continue
        for it in t["items"]:
            lines.append(f"- **[{clean_text(it['title'], 120)}]({it['url']})**")
            if it.get("point"):
                lines.append(f"  - 要点：{clean_text(it['point'], 160)}")
            if it.get("why"):
                lines.append(f"  - 为什么重要：{clean_text(it['why'], 120)}")
            origin = " · ".join(x for x in (it.get("domain"), it.get("published")) if x)
            lines.append(f"  - 来源：{origin}")
        lines.append("")

    if brief["trends"]:
        lines += ["## 趋势观察", ""] + [f"- {clean_text(x, 160)}" for x in brief["trends"]] + [""]
    if brief["watch"]:
        lines += ["## 值得追踪", ""] + [f"- {clean_text(x, 160)}" for x in brief["watch"]] + [""]

    lines += ["---", "", "_由 Tavily + 大模型自动生成，链接均指向原始出处。_", ""]
    return "\n".join(lines)


def write(brief: dict, out_dir: str = "briefs") -> tuple[Path, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    md_path = d / f"{brief['date']}.md"
    json_path = d / f"{brief['date']}.json"
    md_path.write_text(brief["markdown"], encoding="utf-8")
    payload = {k: v for k, v in brief.items() if k != "markdown"}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def publish_draft(draft) -> dict:
    """审校通过后由 review.approve() 调用：把草稿落成 briefs/*.md|json。"""
    out_dir = (draft.meta or {}).get("out_dir", "briefs")
    brief = dict(draft.data or {})
    brief["markdown"] = draft.content
    md_path, json_path = write(brief, out_dir)
    print(f"[brief] 审校通过，发布到 {md_path} / {json_path}")
    return {"markdown": str(md_path), "json": str(json_path)}


async def run(cfg: dict, sources: dict, date_str: str | None = None) -> dict:
    bcfg = cfg.get("brief") or {}
    tcfg = cfg.get("tavily") or {}
    llm_cfg = cfg.get("llm") or {}
    date_str = date_str or now().strftime("%Y-%m-%d")
    max_items = int(bcfg.get("max_items_per_topic", 6))

    if not tavily.get_api_key(tcfg.get("api_key", "")):
        print("[brief] 未配置 TAVILY_API_KEY，跳过前沿简报")
        return {"skipped": True, "reason": "no tavily key", "date": date_str}

    topics, stats = await collect(sources, tcfg)
    print(f"[brief] 主题 {stats['topics']} 个，命中 {stats['items']} 条"
          f"（去重 {stats['duplicates_removed']}，成功 {stats['ok_topics']}）")

    for t in topics:
        if not t["ok"]:
            print(f"[brief] 主题「{t['name']}」失败：{t['error']}")
    if stats["ok_topics"] == 0:
        print("[brief] 所有主题都失败（检查 TAVILY_API_KEY / 网络），不生成文件")
        return {"skipped": True, "reason": "all topics failed", "date": date_str,
                "sources": [{"name": t["name"], "error": t["error"]} for t in topics]}

    llm_out = None
    if bcfg.get("use_llm", True):
        llm_out = await _llm(topics, llm_cfg, max_items, bcfg.get("language", "zh"))

    sections, extra = _merge(topics, llm_out, max_items)
    engine = "tavily+llm" if llm_out else "tavily"
    if not llm_out and not extra["overview"]:
        extra = _extractive(topics, max_items)

    brief = {
        "date": date_str,
        "generated_at": now().isoformat(timespec="seconds"),
        "engine": engine,
        "stats": {**stats, "engine": engine},
        **extra,
        "topics": sections,
    }
    brief["markdown"] = render_markdown(brief)

    # 审校模式：先进队列，人工 approve 后才写 briefs/
    if bcfg.get("review", False):
        from review import ReviewStore
        store = ReviewStore((cfg.get("review") or {}).get("dir", "review"))
        draft = store.add(
            kind="brief",
            title=f"学科前沿简报 {date_str}",
            content=brief["markdown"],
            data={k: v for k, v in brief.items() if k != "markdown"},
            meta={"out_dir": bcfg.get("output_dir", "briefs"), "engine": engine,
                  "date": date_str},
            model=llm_cfg.get("model", ""),
        )
        print(f"[brief] 已进入审校队列：{draft.id}")
        print(f"[brief] 预览：python review.py show {draft.id}    通过：python review.py approve {draft.id}")
        brief["review"] = {"draft_id": draft.id, "status": "pending"}
        return brief

    md_path, json_path = write(brief, bcfg.get("output_dir", "briefs"))
    print(f"[brief] 写入 {md_path} / {json_path}")
    brief["paths"] = {"markdown": str(md_path), "json": str(json_path)}
    return brief