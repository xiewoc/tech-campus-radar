import asyncio
import re
import httpx
from models import Position

# 华为校招新官网（career.huawei.com/cn）走的是 API 网关，不再是老的 portal5 接口。
# 老接口 /reccampportal/services/portal/portalpub/getJob/newHr/page/... 现在只回 2 条历史岗位。
API = "https://apigw-dgg-b0.huawei.com/api/apig/channelhw"
APP_ID = "app_000000035886"          # 官网前端 window.careerConfigData.appId
LIST_URL = f"{API}/recruitmentPosition/pub/getJobPage?X-HW-ID={APP_ID}"
DETAIL_URL = f"{API}/recruitmentPosition/pub/getRecruitmentPositionDetail?X-HW-ID={APP_ID}"
INTENT_URL = f"{API}/recruitmentPosition/pub/getPositionIntentionList?X-HW-ID={APP_ID}"
DETAIL_PAGE = "https://career.huawei.com/cn/job-details?advertisementId={adv_id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://career.huawei.com",
    "Referer": "https://career.huawei.com/cn/campus-recruitment-job-list",
    "X-HW-ID": APP_ID,
    "x-jalor-tenantAlias": "hcm",
    "x-language": "zh_CN",           # 缺这个头网关会返回空数据
    "x-Referer": "https://career.huawei.com/cn",
    "x-alb-gray": "prod",
    "X-Csrf-Token": "",
}
# 列表里的 JD 是占位文本，真实 JD 在「岗位意向」里
PLACEHOLDER = "请您详见岗位意向"


async def fetch(keyword: str = "", max_pages: int = 10, page_size: int = 20,
                request_interval: float = 0.4, **_) -> list[Position]:
    results: list[Position] = []
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        for page in range(1, max_pages + 1):
            payload = {"curPage": page, "pageSize": page_size, "jobType": "CR"}
            if keyword:
                payload["keyWord"] = keyword
            resp = await client.post(LIST_URL, json=payload)
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") != "SUCCESS":
                print(f"[huawei] {body.get('errors')}")
                break
            data = body.get("data") or {}
            rows = data.get("result") or []
            if not rows:
                break
            for item in rows:
                p = await _build_position(client, item, request_interval)
                if p:
                    results.append(p)
                await asyncio.sleep(request_interval)
            total = (data.get("pageVO") or {}).get("totalRows", 0)
            if page * page_size >= total:
                break
    return results


async def _build_position(client: httpx.AsyncClient, item: dict,
                          interval: float) -> Position | None:
    adv_id = item.get("advertisementId")
    if not adv_id:
        return None

    responsibilities = _clean(item.get("mainBusiness", ""))
    requirements = _clean(item.get("jobRequire", ""))
    title = item.get("jobName") or item.get("jobNameNew") or ""

    if PLACEHOLDER in responsibilities or PLACEHOLDER in requirements or not requirements:
        job_id = item.get("jobId")
        if job_id:
            intents = await _get_intents(client, job_id)
            if intents:
                first = intents[0]
                responsibilities = _clean(first.get("jobResponsibilities", "")) or responsibilities
                requirements = _clean(first.get("jobDemand", "")) or requirements

    return Position(
        company="huawei",
        job_id=str(item.get("jobId") or adv_id),
        title=title,
        city=item.get("workPlace", "") or "",
        category=item.get("categoryName", "") or "",
        url=DETAIL_PAGE.format(adv_id=adv_id),
        responsibilities=responsibilities,
        requirements=requirements,
    )


async def _get_intents(client: httpx.AsyncClient, job_id) -> list[dict]:
    try:
        resp = await client.post(INTENT_URL, json={"jobId": job_id})
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") == "SUCCESS":
            return body.get("data") or []
    except Exception:
        pass
    return []


async def get_detail(client: httpx.AsyncClient, adv_id) -> dict:
    """岗位详情（含 jobId / 部门等），需要时再调用。"""
    resp = await client.post(DETAIL_URL, json={"advertisementId": adv_id})
    resp.raise_for_status()
    body = resp.json()
    return body.get("data") or {}


def _clean(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<br\s*/?>", "\n", str(text)).strip()
