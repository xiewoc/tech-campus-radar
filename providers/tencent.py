import asyncio
import httpx
from models import Position

URL = "https://join.qq.com/api/v1/position/searchPosition"
DETAIL_URL = "https://join.qq.com/api/v1/jobDetails/getJobDetailsByPostId"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://join.qq.com",
    "Referer": "https://join.qq.com/post.html",
    "Content-Type": "application/json",
}


async def fetch(keyword: str, max_pages: int = 30, page_size: int = 100,
                request_interval: float = 0.4, **_) -> list[Position]:
    results: list[Position] = []
    page_index = 1                     # 接口要求 pageIndex >= 1，传 0 会报「最小不能小于1」
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        for _ in range(max_pages):
            payload = {
                "projectMappingIdList": [1],   # 校招应届
                "keyword": keyword,
                "pageIndex": page_index,
                "pageSize": page_size,
            }
            resp = await client.post(URL, json=payload)
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") != 0:
                print(f"[tencent] {body.get('message') or body.get('status')}")
                break
            data = body.get("data") or {}
            rows = data.get("positionList") or []
            if not rows:
                break
            for r in rows:
                post_id = r.get("postId") or r.get("positionId")
                if not post_id:
                    continue
                p = Position(
                    company="tencent",
                    job_id=str(post_id),
                    title=r.get("positionTitle", ""),
                    city=_split_cities(r.get("workCities")),
                    url=f"https://join.qq.com/post_detail.html?postid={post_id}",
                    category="",
                    responsibilities="",
                    requirements="",
                )
                await _fill_detail(client, p, request_interval)
                results.append(p)
            await asyncio.sleep(request_interval)
            total = data.get("count") or data.get("total") or 0
            if page_index * page_size >= total:
                break
            page_index += 1
    return results


async def _fill_detail(client: httpx.AsyncClient, p: Position, interval: float) -> None:
    """列表不带 JD，详情接口才有 desc/request 与岗位族名。"""
    try:
        resp = await client.get(DETAIL_URL, params={"postId": p.job_id})
        resp.raise_for_status()
        body = resp.json()
        detail = body.get("data") or {}
        p.responsibilities = detail.get("desc", "") or ""
        p.requirements = detail.get("request", "") or ""
        p.category = detail.get("tidName", "") or ""
        cities = detail.get("workCityList") or []
        if isinstance(cities, list) and cities:
            p.city = ", ".join(str(x) for x in cities if x)
    except Exception:
        pass
    await asyncio.sleep(interval)


def _split_cities(text) -> str:
    """列表里的 workCities 是 '深圳总部 北京 上海 广州 ' 这种空格分隔串。"""
    if not isinstance(text, str):
        return ""
    return ", ".join(text.split())
