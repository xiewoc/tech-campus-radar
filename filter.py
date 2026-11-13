"""④ 清洗 · 去重 · 筛选。

原则：能前置的绝不留到后面。顺序是
    基础清洗 → 关键词/噪声过滤 → 跨源去重 → （可选）LLM 语义打分
LLM 永远放在最后一步，且失败时放行，避免漏掉内容。
"""

import re

import llm
from models import NewsItem, Position


# ---------------------------- 基础清洗 ----------------------------

def clean_text(text: str, limit: int = 0) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))          # 去 HTML 标签
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)       # 去 HTML 实体
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _norm_title(title: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", (title or "").lower())


# ---------------------------- RSS 过滤 ----------------------------

def prefilter_news(items: list[NewsItem], include: list[str] | None = None,
                   exclude: list[str] | None = None,
                   min_title_len: int = 6) -> list[NewsItem]:
    include = [k.lower() for k in (include or []) if k]
    exclude = [k.lower() for k in (exclude or []) if k]
    kept: list[NewsItem] = []
    for item in items:
        if not item.title or not item.url:
            continue
        if len(item.title.strip()) < min_title_len:
            continue
        haystack = f"{item.title} {item.summary}".lower()
        if exclude and any(k in haystack for k in exclude):
            continue
        if include and not any(k in haystack for k in include):
            continue
        kept.append(item)
    return kept


def dedupe_news(items: list[NewsItem]) -> list[NewsItem]:
    """同一批里按 URL / 归一化标题去重（跨源重复的转载也会被合并）。"""
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    kept: list[NewsItem] = []
    for item in items:
        url_key = (item.url or "").strip().lower()
        title_key = _norm_title(item.title)
        if url_key and url_key in seen_urls:
            continue
        if title_key and title_key in seen_titles:
            continue
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        kept.append(item)
    return kept


def dedupe_positions(positions: list[Position]) -> list[Position]:
    seen: set[tuple[str, str]] = set()
    kept: list[Position] = []
    for p in positions:
        key = (p.company, p.job_id)
        if key in seen:
            continue
        seen.add(key)
        kept.append(p)
    return kept


# ---------------------------- 岗位筛选 ----------------------------

def keyword_filter(positions: list[Position], allow: list[str]) -> list[Position]:
    if not allow:
        return positions
    allow = [a.lower() for a in allow]
    return [
        p for p in positions
        if any(a in (p.category or "").lower() for a in allow)
    ]


# ---------------------------- LLM（最后一步） ----------------------------

def has_key(cfg: dict) -> bool:
    return llm.available(cfg)


async def _judge(cfg: dict, prompt: str) -> dict | None:
    """统一走 llm 层（httpx），provider / base_url / api_key 都由配置决定。"""
    return await llm.complete_json([{"role": "user", "content": prompt}], cfg, temperature=0)


async def llm_filter(positions: list[Position], cfg: dict) -> list[Position]:
    if not cfg.get("enabled"):
        return positions
    if not has_key(cfg):
        print("[llm] 没有 api key，跳过")
        return positions

    min_score = cfg.get("min_score", 6)
    kept: list[Position] = []
    for p in positions:
        jd = (p.responsibilities + "\n" + p.requirements).strip()[:2000]
        prompt = f"""你是校招岗位筛选器。判断以下岗位是否属于“前沿科技校招”方向（算法、软件、芯片、数据、安全等）。
返回 JSON，格式：{{"tech": true/false, "score": 1-10}}

公司：{p.company}
标题：{p.title}
类别：{p.category}
城市：{p.city}
岗位职责与要求：
{jd or "（JD 缺失，仅根据标题和类别判断）"}
"""
        try:
            data = await _judge(cfg, prompt) or {}
            if data.get("tech") and data.get("score", 0) >= min_score:
                kept.append(p)
        except Exception as e:
            print(f"[llm] {p.company}/{p.job_id} 失败：{e}")
            kept.append(p)          # LLM 失败放行
    return kept


async def llm_filter_news(items: list[NewsItem], cfg: dict) -> list[NewsItem]:
    if not cfg.get("enabled") or not items:
        return items
    if not has_key(cfg):
        return items

    min_score = cfg.get("min_score", 6)
    kept: list[NewsItem] = []
    for item in items:
        prompt = f"""判断下面这条科技资讯是否值得推送给关注 AI / 大模型 / 芯片 / 工程技术的读者。
返回 JSON：{{"relevant": true/false, "score": 1-10}}

来源：{item.source}
标题：{item.title}
摘要：{item.summary[:600]}
"""
        try:
            data = await _judge(client, model, prompt) or {}
            if data.get("relevant") and data.get("score", 0) >= min_score:
                kept.append(item)
        except Exception as e:
            print(f"[llm] news {item.title[:30]} 失败：{e}")
            kept.append(item)
    return kept