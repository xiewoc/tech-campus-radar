from providers.feishu_core import FeishuSource, fetch as _fetch

# MiniMax 的 portal-channel 是公司路径，不是 "campus"
SOURCE = FeishuSource(
    company="minimax",
    host="vrfi1sk8a0.jobs.feishu.cn",
    channel="379481",
    apply_prefix="https://vrfi1sk8a0.jobs.feishu.cn/379481/position",
)


async def fetch(keyword: str = "", max_pages: int = 10, page_size: int = 50,
                request_interval: float = 0.4, **kwargs):
    return await _fetch(SOURCE, keyword=keyword, max_pages=max_pages,
                        page_size=page_size, request_interval=request_interval)
