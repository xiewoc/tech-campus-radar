"""聚合接口：既能起 HTTP，也能直接当函数调。

三种用法：

    # 1) HTTP 服务（默认）
    python api.py --port 8000
    curl "http://127.0.0.1:8000/jobs?keyword=算法&companies=nio,netease&limit=5"

    # 2) 命令行直接调（结果打印成 JSON，可 --out 存文件）
    python api.py --call jobs --keyword 算法 --companies nio,netease --limit 5
    python api.py --call news --source TechCrunch --limit 5
    python api.py --call demand --keyword 算法 --top 15

    # 3) import 进来当函数用
    import api
    jobs = api.get_jobs(keyword="算法", companies=["nio", "netease"], limit=5)
    news = api.get_news(source="TechCrunch", limit=5)
    allin = api.get_all(keyword="算法")          # jobs + news 一次拿
    rep = api.get_demand(keyword="算法")          # 需求画像（结构化 + markdown）
"""

import argparse
import asyncio
import concurrent.futures
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import aggregate

HELP = {
    "service": "tech-campus-radar",
    "endpoints": {
        "/health": "存活检查",
        "/sources": "所有来源（provider / label / site / endpoint）",
        "/jobs": "聚合岗位：keyword / companies / max_pages / page_size / limit / interval",
        "/news": "聚合新闻：source（源名模糊匹配）/ limit",
        "/demand": "岗位需求画像：keyword / companies / max_pages / min_count / top_n",
    },
    "python": "import api; api.get_jobs(...) / get_news(...) / get_all(...) / get_demand(...)",
    "note": "每条数据都带 source 字段：provider/label/type/site/endpoint/fetched_at",
}


# ==================================================================
# 函数接口：同步版 + 异步版
# ==================================================================

def _run(coro):
    """在同步上下文里跑协程；如果已经在事件循环里，丢到独立线程执行。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _split(value):
    if isinstance(value, str):
        return [x for x in value.replace("，", ",").split(",") if x] or None
    return value


def get_health() -> dict:
    return {"ok": True, "fetched_at": aggregate.now_iso()}


def get_sources() -> list[dict]:
    return aggregate.list_sources()


# ---------------------------- jobs ----------------------------

async def aget_jobs(keyword: str = "", companies=None, max_pages: int = 1,
                    page_size: int = 20, limit: int = 0,
                    interval: float = 0.4) -> dict:
    return await aggregate.aggregate_jobs(
        keywords=keyword, companies=_split(companies), max_pages=max_pages,
        page_size=page_size, request_interval=interval, limit=limit)


def get_jobs(keyword: str = "", companies=None, max_pages: int = 1,
             page_size: int = 20, limit: int = 0, interval: float = 0.4) -> dict:
    """同步拿岗位：api.get_jobs(keyword="算法", companies=["nio"])"""
    return _run(aget_jobs(keyword, companies, max_pages, page_size, limit, interval))


# ---------------------------- news ----------------------------

async def aget_news(source: str = "", limit: int = 50) -> dict:
    return await aggregate.aggregate_news(source=source, limit=limit)


def get_news(source: str = "", limit: int = 50) -> dict:
    """同步拿新闻：api.get_news(source="TechCrunch", limit=10)"""
    return _run(aget_news(source, limit))


# ---------------------------- demand ----------------------------

async def aget_demand(keyword: str = "", companies=None, max_pages: int = 1,
                      page_size: int = 20, min_count: int = 2,
                      top_n: int = 25) -> dict:
    return await aggregate.aggregate_demand(
        keywords=keyword, companies=_split(companies), max_pages=max_pages,
        page_size=page_size, min_count=min_count, top_n=top_n)


def get_demand(keyword: str = "", companies=None, max_pages: int = 1,
               page_size: int = 20, min_count: int = 2, top_n: int = 25) -> dict:
    """同步拿需求画像：api.get_demand(keyword="算法")["skills"]"""
    return _run(aget_demand(keyword, companies, max_pages, page_size, min_count, top_n))


# ---------------------------- all ----------------------------

async def aget_all(keyword: str = "", companies=None, max_pages: int = 1,
                   limit: int = 20, news_limit: int = 30) -> dict:
    jobs, news = await asyncio.gather(
        aget_jobs(keyword=keyword, companies=companies, max_pages=max_pages, limit=limit),
        aget_news(limit=news_limit),
    )
    return {"jobs": jobs, "news": news, "fetched_at": aggregate.now_iso()}


def get_all(keyword: str = "", companies=None, max_pages: int = 1,
            limit: int = 20, news_limit: int = 30) -> dict:
    """一次拿岗位 + 新闻：api.get_all(keyword="算法", limit=5)"""
    return _run(aget_all(keyword, companies, max_pages, limit, news_limit))


def save_json(obj: dict, path: str) -> str:
    """把任意接口结果写到文件，中文不转义。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


# ==================================================================
# HTTP 层
# ==================================================================

class Handler(BaseHTTPRequestHandler):
    server_version = "RadarAPI/1.1"

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/":
                self._send(HELP)
            elif path == "/health":
                self._send(get_health())
            elif path == "/sources":
                self._send({"jobs": get_sources()})
            elif path == "/jobs":
                self._send(asyncio.run(aget_jobs(
                    keyword=q.get("keyword", ""),
                    companies=q.get("companies"),
                    max_pages=int(q.get("max_pages", 1)),
                    page_size=int(q.get("page_size", 20)),
                    limit=int(q.get("limit", 0)),
                    interval=float(q.get("interval", 0.4)),
                )))
            elif path == "/news":
                self._send(asyncio.run(aget_news(
                    source=q.get("source", ""),
                    limit=int(q.get("limit", 50)),
                )))
            elif path == "/demand":
                self._send(asyncio.run(aget_demand(
                    keyword=q.get("keyword", ""),
                    companies=q.get("companies"),
                    max_pages=int(q.get("max_pages", 1)),
                    min_count=int(q.get("min_count", 2)),
                    top_n=int(q.get("top_n", 25)),
                )))
            else:
                self._send({"error": "not found", "path": path, "help": HELP}, 404)
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {str(e)[:300]}"}, 500)

    def log_message(self, fmt, *args):
        print(f"[api] {self.address_string()} {fmt % args}")


# ==================================================================
# CLI
# ==================================================================

def _call(args) -> dict:
    if args.call == "health":
        return get_health()
    if args.call == "sources":
        return {"jobs": get_sources()}
    if args.call == "jobs":
        return get_jobs(keyword=args.keyword, companies=args.companies,
                        max_pages=args.max_pages, page_size=args.page_size,
                        limit=args.limit, interval=args.interval)
    if args.call == "news":
        return get_news(source=args.source, limit=args.limit or 50)
    if args.call == "demand":
        return get_demand(keyword=args.keyword, companies=args.companies,
                          max_pages=args.max_pages, min_count=args.min_count,
                          top_n=args.top)
    if args.call == "all":
        return get_all(keyword=args.keyword, companies=args.companies,
                       max_pages=args.max_pages, limit=args.limit or 20)
    raise SystemExit(f"unknown --call {args.call}")


def main():
    parser = argparse.ArgumentParser(description="聚合接口：HTTP / 命令行 / 函数")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--call", choices=["health", "sources", "jobs", "news", "demand", "all"],
                        help="不起服务，直接调一次并输出 JSON")
    parser.add_argument("--keyword", default="")
    parser.add_argument("--companies", default="", help="逗号分隔，如 nio,netease")
    parser.add_argument("--source", default="", help="新闻源名模糊匹配")
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--interval", type=float, default=0.4)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--out", default="", help="配合 --call，把结果写到文件")
    args = parser.parse_args()

    if args.call:
        result = _call(args)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"[api] 已写入 {args.out}")
        else:
            print(text)
        return

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[api] listening on http://{args.host}:{args.port}")
    print("[api] try: /jobs?keyword=算法&companies=nio,netease&max_pages=1&page_size=3")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[api] bye")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
