import asyncio
import httpx
from models import Position

URL = "https://jobs.bytedance.com/api/v1/search/job/posts"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://jobs.bytedance.com/campus",
    "Origin": "https://jobs.bytedance.com",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}
# 站点 WAF 在请求过密时会返回 405（空 body），退避后恢复
RETRY_STATUS = {403, 405, 429, 500, 502, 503, 504}


async def fetch(keyword: str, max_pages: int = 30, page_size: int = 100,
                request_interval: float = 0.4, **_) -> list[Position]:
    results: list[Position] = []
    offset = 0
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        for _ in range(max_pages):
            payload = {
                "keyword": keyword,
                "limit": page_size,
                "offset": offset,
                "portal_type": 3,
                "portal_entrance": 1,
                "language": "zh",
                "recruitment_id_list": ["201"],
            }
            body = await _post_with_retry(client, payload)
            if body is None or body.get("code") != 0:
                break
            data = body.get("data") or {}
            rows = data.get("job_post_list") or []
            if not rows:
                break
            for r in rows:
                job_id = r.get("id")
                if not job_id:
                    continue
                results.append(Position(
                    company="bytedance",
                    job_id=str(job_id),
                    title=r.get("title", ""),
                    city=_join_names(r.get("city_list") or r.get("city_info_list") or []),
                    category=_category_name(r.get("job_category") or r.get("job_function")),
                    url=f"https://jobs.bytedance.com/campus/position/{job_id}/detail",
                    responsibilities=r.get("description", "") or "",
                    requirements=r.get("requirement", "") or "",
                ))
            await asyncio.sleep(request_interval)
            offset += page_size
            if offset >= data.get("count", 0):
                break
    return results


async def _post_with_retry(client: httpx.AsyncClient, payload: dict,
                           retries: int = 3) -> dict | None:
    for attempt in range(retries):
        resp = await client.post(URL, json=payload)
        if resp.status_code in RETRY_STATUS:
            await asyncio.sleep(2 ** attempt * 3)      # 3s / 6s / 12s
            continue
        resp.raise_for_status()
        return resp.json()
    print(f"[bytedance] WAF 限流未恢复（keyword={payload.get('keyword')}）")
    return None


def _join_names(items) -> str:
    if not isinstance(items, list):
        return ""
    return ", ".join(x.get("name", "") for x in items if isinstance(x, dict) and x.get("name"))


def _category_name(cat) -> str:
    """job_category 是 {id, name, parent:{name}} 结构，拼接成 '研发/算法'。"""
    if isinstance(cat, dict):
        parent = (cat.get("parent") or {}).get("name") if isinstance(cat.get("parent"), dict) else ""
        return "/".join(x for x in (parent, cat.get("name")) if x)
    if isinstance(cat, list):
        return _join_names(cat)
    return ""
