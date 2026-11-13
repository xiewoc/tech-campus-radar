"""招聘采集：调度 providers/ 下的各公司 API。"""

import asyncio
import importlib

from models import Position


def load_provider(name: str):
    return importlib.import_module(f"providers.{name}")


async def collect(names: list[str], keywords: list[str], max_pages: int = 3,
                  request_interval: float = 0.4) -> list[Position]:
    positions: list[Position] = []
    for keyword in keywords:
        for name in names:
            try:
                mod = load_provider(name)
            except Exception as e:
                print(f"[jobs] 加载 {name} 失败：{e}")
                continue
            try:
                got = await mod.fetch(keyword=keyword, max_pages=max_pages,
                                      request_interval=request_interval)
                positions.extend(got)
                print(f"[jobs] {name} keyword={keyword} -> {len(got)}")
            except Exception as e:
                print(f"[jobs] {name} keyword={keyword} 失败：{type(e).__name__} {e}")
            await asyncio.sleep(request_interval)
    return positions
