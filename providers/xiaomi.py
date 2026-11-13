import asyncio
import httpx
from models import Position

# 小米自建的飞书招聘（ATSX）站点，接口形状与 jobs.bytedance.com 完全一致，
# 靠 portal-channel / website-path 切换「校招 / 实习」池子。
URL = "https://xiaomi.jobs.f.mioffice.cn/api/v1/search/job/posts"
DETAIL_URL = "https://xiaomi.jobs.f.mioffice.cn/api/v1/job/posts/{post_id}"
HEADERS = {
    "portal-channel": "campus",
    "portal-platform": "pc",
    "website-path": "campus",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://xiaomi.jobs.f.mioffice.cn",
    "Referer": "https://xiaomi.jobs.f.mioffice.cn/campus",
    "Content-Type": "application/json",
}
RETRY_STATUS = {403, 405, 429, 500, 502, 503, 504}


async def fetch(keyword: str, max_pages: int = 10, page_size: int = 100,
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
                p = Position(
                    company="xiaomi",
                    job_id=str(job_id),
                    title=r.get("title", ""),
                    city=_join_names(r.get("city_list") or r.get("city_info_list") or []),
                    category=_category_name(r.get("job_category") or r.get("job_function")
                                            or r.get("job_function_list")),
                    url=f"https://xiaomi.jobs.f.mioffice.cn/campus/position/{job_id}/detail",
                    responsibilities=r.get("description", "") or "",
                    requirements=r.get("requirement", "") or "",
                )
                if not p.responsibilities and not p.requirements:
                    await _fill_detail(client, p, request_interval)
                results.append(p)
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
            await asyncio.sleep(2 ** attempt * 3)
            continue
        resp.raise_for_status()
        return resp.json()
    print(f"[xiaomi] WAF 限流未恢复（keyword={payload.get('keyword')}）")
    return None


async def _fill_detail(client: httpx.AsyncClient, p: Position, interval: float) -> None:
    try:
        resp = await client.get(
            DETAIL_URL.format(post_id=p.job_id),
            headers={"portal-platform": "pc"},      # 详情接口不需要 portal-channel
        )
        resp.raise_for_status()
        body = resp.json()
        detail = (body.get("data") or {}).get("job_post_detail") or {}
        p.responsibilities = detail.get("description", "") or p.responsibilities
        p.requirements = detail.get("requirement", "") or p.requirements
    except Exception:
        pass
    await asyncio.sleep(interval)


def _join_names(items) -> str:
    if not isinstance(items, list):
        return ""
    return ", ".join(x.get("name", "") for x in items if isinstance(x, dict) and x.get("name"))


def _category_name(cat) -> str:
    if isinstance(cat, dict):
        parent = (cat.get("parent") or {}).get("name") if isinstance(cat.get("parent"), dict) else ""
        return "/".join(x for x in (parent, cat.get("name")) if x)
    if isinstance(cat, list):
        return _join_names(cat)
    return ""
