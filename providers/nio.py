from providers.feishu_core import FeishuSource, fetch as _fetch

SOURCE = FeishuSource(
    company="nio",
    host="nio.jobs.feishu.cn",
    channel="campus",
    apply_prefix="https://nio.jobs.feishu.cn/campus/position",
)


async def fetch(keyword: str = "", max_pages: int = 10, page_size: int = 50,
                request_interval: float = 0.4, **kwargs):
    return await _fetch(SOURCE, keyword=keyword, max_pages=max_pages,
                        page_size=page_size, request_interval=request_interval)
