import asyncio
import httpx
from models import Position

URL = "https://hr.163.com/api/hr163/position/queryPage"
DETAIL_PAGE = "https://hr.163.com/job-detail.html?id={job_id}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://hr.163.com",
    "Referer": "https://hr.163.com/job-list.html",
}
# 实测 workType: 0=社招（2060） 1=实习/校招（617） 2=派遣外包（60）
# 网易校招走的是「实习生」池子，社招和派遣都不进雷达
CAMPUS_WORK_TYPE = "1"


async def fetch(keyword: str, max_pages: int = 10, page_size: int = 50,
                request_interval: float = 0.4, **_) -> list[Position]:
    results: list[Position] = []
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        for page in range(1, max_pages + 1):
            payload = {
                "pageNum": page,
                "pageSize": page_size,
                "keyword": keyword,
                "workType": CAMPUS_WORK_TYPE,
            }
            resp = await client.post(URL, json=payload)
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 200:
                print(f"[netease] {body.get('msg')}")
                break
            data = body.get("data") or {}
            rows = data.get("list") or []
            if not rows:
                break
            for r in rows:
                job_id = r.get("id")
                if not job_id:
                    continue
                title = r.get("name", "") or ""
                if any(k in title for k in ("派遣", "外包")):
                    continue        # 派遣岗不是校招目标
                results.append(Position(
                    company="netease",
                    job_id=str(job_id),
                    title=title,
                    city=", ".join(r.get("workPlaceNameList") or []),
                    category=r.get("firstPostTypeName", "") or "",
                    url=DETAIL_PAGE.format(job_id=job_id),
                    responsibilities=(r.get("description") or "").strip(),
                    requirements=(r.get("requirement") or "").strip(),
                ))
            await asyncio.sleep(request_interval)
            if page * page_size >= (data.get("total") or 0):
                break
    return results