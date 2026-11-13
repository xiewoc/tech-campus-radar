#!/usr/bin/env python3
"""招聘接口连通性测试：自动发现 providers/ 下所有公司，只测列表接口。

用法:
    python test_providers.py [keyword] [page_size] [timeout_sec]
"""

import asyncio
import importlib
import sys
import time
import traceback
from pathlib import Path

# feishu_core 是通用适配器，不是公司，跳过
SKIP = {"__init__", "feishu_core"}
NAMES = sorted(p.stem for p in Path(__file__).parent.joinpath("providers").glob("*.py")
               if p.stem not in SKIP)
PROVIDERS = [importlib.import_module(f"providers.{n}") for n in NAMES]


async def test_one(mod, keyword, page_size, timeout):
    name = mod.__name__.split(".")[-1]
    t0 = time.perf_counter()
    try:
        positions = await asyncio.wait_for(
            mod.fetch(keyword=keyword, max_pages=1, page_size=page_size,
                      request_interval=0.2),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        print(f"[{name:9}] TIMEOUT >{timeout}s")
        return None
    except Exception as e:
        cost = time.perf_counter() - t0
        print(f"[{name:9}] FAIL  {cost:5.1f}s  {type(e).__name__}: {e}")
        print("".join("           " + ln for ln in traceback.format_exc(limit=3).splitlines(True)))
        return None

    cost = time.perf_counter() - t0
    with_jd = sum(1 for p in positions if p.responsibilities or p.requirements)
    print(f"[{name:9}] OK    {cost:5.1f}s  条数={len(positions):3d}  有JD={with_jd}")
    for p in positions[:2]:
        print(f"           - {p.title} | {p.city} | {p.category} | jd={len(p.responsibilities)}/{len(p.requirements)}")
    return positions


async def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "算法"
    page_size = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 60
    print(f"keyword={keyword!r} page_size={page_size} providers={NAMES}\n")
    for mod in PROVIDERS:
        await test_one(mod, keyword, page_size, timeout)
        print()


if __name__ == "__main__":
    asyncio.run(main())
