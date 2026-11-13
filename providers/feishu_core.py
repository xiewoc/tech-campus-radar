"""飞书招聘（ATSX）通用适配器。

多家公司自建门户都用这套接口，差别只有 host 和 portal-channel：
    POST https://{host}/api/v1/search/job/posts
    GET  https://{host}/api/v1/job/posts/{id}
"""

import asyncio
from dataclasses import dataclass

import httpx

from models import Position

RETRY_STATUS = {403, 405, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class FeishuSource:
    company: str
    host: str
    channel: str
    apply_prefix: str


def _headers(src: FeishuSource) -> dict:
    return {
        "portal-channel": src.channel,
        "portal-platform": "pc",
        "website-path": src.channel,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": f"https://{src.host}",
        "Referer": f"https://{src.host}/{src.channel}",
        "Content-Type": "application/json",
    }


async def fetch(src: FeishuSource, keyword: str = "", max_pages: int = 10,
                page_size: int = 50, request_interval: float = 0.4,
                **_) -> list[Position]:
    results: list[Position] = []
    offset = 0
    async with httpx.AsyncClient(timeout=30, headers=_headers(src)) as client:
        for _ in range(max_pages):
            payload = {
                "keyword": keyword,
                "limit": page_size,
                "offset": offset,
                "portal_type": 3,
                "portal_entrance": 1,
                "language": "zh",
            }
            body = await _post_with_retry(client, src, payload)
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
                    company=src.company,
                    job_id=str(job_id),
                    title=r.get("title", ""),
                    city=_join(r.get("city_list") or r.get("city_info_list") or []),
                    category=_category(r.get("job_category") or r.get("job_function")),
                    url=f"{src.apply_prefix}/{job_id}/detail",
                    responsibilities=r.get("description", "") or "",
                    requirements=r.get("requirement", "") or "",
                )
                if not p.responsibilities and not p.requirements:
                    await _fill_detail(client, src, p, request_interval)
                results.append(p)
            await asyncio.sleep(request_interval)
            offset += page_size
            if offset >= (data.get("count") or 0):
                break
    return results


async def _post_with_retry(client: httpx.AsyncClient, src: FeishuSource,
                           payload: dict, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        resp = await client.post(f"https://{src.host}/api/v1/search/job/posts", json=payload)
        if resp.status_code in RETRY_STATUS:
            await asyncio.sleep(2 ** attempt * 3)
            continue
        resp.raise_for_status()
        return resp.json()
    print(f"[{src.company}] WAF 限流未恢复（keyword={payload.get('keyword')}）")
    return None


async def _fill_detail(client: httpx.AsyncClient, src: FeishuSource,
                       p: Position, interval: float) -> None:
    try:
        resp = await client.get(f"https://{src.host}/api/v1/job/posts/{p.job_id}",
                                headers={"portal-platform": "pc"})
        resp.raise_for_status()
        detail = (resp.json().get("data") or {}).get("job_post_detail") or {}
        p.responsibilities = detail.get("description", "") or p.responsibilities
        p.requirements = detail.get("requirement", "") or p.requirements
    except Exception:
        pass
    await asyncio.sleep(interval)


def _join(items) -> str:
    if not isinstance(items, list):
        return ""
    return ", ".join(x.get("name", "") for x in items if isinstance(x, dict) and x.get("name"))


def _category(cat) -> str:
    if isinstance(cat, dict):
        parent = (cat.get("parent") or {}).get("name") if isinstance(cat.get("parent"), dict) else ""
        return "/".join(x for x in (parent, cat.get("name")) if x)
    if isinstance(cat, list):
        return _join(cat)
    return ""
