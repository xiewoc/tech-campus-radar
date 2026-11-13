import asyncio
import httpx
from models import Position

URL = "https://zhaopin.meituan.com/api/official/job/getJobList"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://zhaopin.meituan.com",
    "Referer": "https://zhaopin.meituan.com/web/position",
    "Content-Type": "application/json",
}


async def fetch(keyword: str, max_pages: int = 30, page_size: int = 100,
                request_interval: float = 0.4, **_) -> list[Position]:
    results: list[Position] = []
    page_no = 1
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        while page_no <= max_pages:
            payload = {
                "page": {"pageNo": page_no, "pageSize": page_size},
                "jobShareType": "1",
                "keywords": keyword[:30],
                "jobType": [
                    {"code": "1", "subCode": []},   # 校招正式
                    {"code": "2", "subCode": []},   # 实习
                ],
            }
            rows, data = [], {}
            for attempt in range(3):                 # 空页退避重试
                resp = await client.post(URL, json=payload)
                resp.raise_for_status()
                body = resp.json()
                if body.get("status") != 1:
                    return results
                data = body.get("data") or {}
                rows = data.get("list") or []
                if rows:
                    break
                await asyncio.sleep(1.5 * (attempt + 1))
            if not rows:
                break
            for r in rows:
                job_id = r.get("jobUnionId")
                if not job_id:
                    continue
                results.append(Position(
                    company="meituan",
                    job_id=str(job_id),
                    title=r.get("name", ""),
                    city=_join_cities(r.get("cityList") or []),
                    # 列表里的 category 是「技术类」，用 familyGroup（如「算法」）更贴近筛选白名单
                    category=(r.get("jobFamilyGroup") or r.get("jobFamily") or ""),
                    url=f"https://zhaopin.meituan.com/web/position/detail?jobUnionId={job_id}",
                    responsibilities=r.get("jobDuty", "") or "",
                    requirements=r.get("jobRequirement", "") or "",
                ))
            await asyncio.sleep(request_interval)
            total = data.get("page", {}).get("totalCount", 0) or data.get("totalCount", 0)
            if page_no * page_size >= total:
                break
            page_no += 1
    return results


def _join_cities(items) -> str:
    if not isinstance(items, list):
        return ""
    return ", ".join(x.get("name", "") for x in items if isinstance(x, dict) and x.get("name"))
