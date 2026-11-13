"""审校队列：模型产出先落草稿，人工（或二次模型）确认后再发布。

    python review.py list                     # 待审列表
    python review.py show <id>                # 看全文
    python review.py approve <id>             # 通过 → 触发该类型的发布动作
    python review.py reject <id> --reason "…" # 驳回
    python review.py edit <id> --file new.md  # 改完再通过
    python review.py stats                    # 队列统计

草稿落在 review/{pending,approved,rejected}/<id>.json，纯文本 + JSON，方便 diff / 版本管理。
发布动作按 kind 注册：brief → 写 briefs/*.md|json；其它类型可自行 register_publisher。
"""

import argparse
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

TZ = timezone(timedelta(hours=8))
STATUSES = ("pending", "approved", "rejected")


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


@dataclass
class Draft:
    id: str
    kind: str
    title: str = ""
    status: str = "pending"
    created_at: str = ""
    updated_at: str = ""
    model: str = ""
    content: str = ""
    data: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    history: list = field(default_factory=list)


_PUBLISHERS: dict[str, Callable[[Draft], dict]] = {}


def register_publisher(kind: str, fn: Callable[[Draft], dict]) -> None:
    """注册某类草稿「通过后」的发布动作，返回发布结果（路径等）。"""
    _PUBLISHERS[kind] = fn


class ReviewStore:
    def __init__(self, root: str = "review"):
        self.root = Path(root)
        for s in STATUSES:
            (self.root / s).mkdir(parents=True, exist_ok=True)

    # ---------- 基础读写 ----------

    def _path(self, draft: Draft) -> Path:
        return self.root / draft.status / f"{draft.id}.json"

    def _write(self, draft: Draft) -> None:
        draft.updated_at = now_iso()
        self._path(draft).write_text(
            json.dumps(asdict(draft), ensure_ascii=False, indent=2), encoding="utf-8")

    def _find(self, draft_id: str) -> tuple[Draft, Path]:
        for s in STATUSES:
            p = self.root / s / f"{draft_id}.json"
            if p.exists():
                return Draft(**json.loads(p.read_text(encoding="utf-8"))), p
        raise KeyError(f"找不到草稿 {draft_id}")

    # ---------- 对外 API ----------

    def add(self, kind: str, content: str, data: dict | None = None,
            title: str = "", meta: dict | None = None, model: str = "") -> Draft:
        draft = Draft(
            id=f"{datetime.now(TZ):%Y%m%d}-{kind}-{uuid.uuid4().hex[:6]}",
            kind=kind, title=title, status="pending",
            created_at=now_iso(), updated_at=now_iso(), model=model,
            content=content, data=data or {}, meta=meta or {},
            history=[{"at": now_iso(), "action": "create"}],
        )
        self._write(draft)
        return draft

    def list(self, status: str = "pending", kind: str = "") -> list[Draft]:
        drafts = []
        for p in sorted((self.root / status).glob("*.json")):
            d = Draft(**json.loads(p.read_text(encoding="utf-8")))
            if not kind or d.kind == kind:
                drafts.append(d)
        return drafts

    def get(self, draft_id: str) -> Draft:
        return self._find(draft_id)[0]

    def edit(self, draft_id: str, content: str, note: str = "") -> Draft:
        draft, _ = self._find(draft_id)
        draft.content = content
        draft.history.append({"at": now_iso(), "action": "edit", "note": note})
        self._write(draft)
        return draft

    def reject(self, draft_id: str, reason: str = "") -> Draft:
        draft, path = self._find(draft_id)
        path.unlink(missing_ok=True)
        draft.status = "rejected"
        draft.history.append({"at": now_iso(), "action": "reject", "note": reason})
        self._write(draft)
        return draft

    def approve(self, draft_id: str, editor: str = "") -> Draft:
        draft, path = self._find(draft_id)
        if draft.status != "pending":
            raise ValueError(f"草稿 {draft_id} 当前状态是 {draft.status}，不能重复通过")

        publisher = _PUBLISHERS.get(draft.kind)
        if publisher is None and draft.kind == "brief":
            import brief
            publisher = brief.publish_draft
        if publisher is None:
            raise KeyError(f"没有为 kind={draft.kind} 注册发布动作")

        result = publisher(draft)          # 先发布，成功后再改状态
        draft.meta["published"] = result
        path.unlink(missing_ok=True)
        draft.status = "approved"
        draft.history.append({"at": now_iso(), "action": "approve", "note": editor})
        self._write(draft)
        return draft

    def stats(self) -> dict:
        return {s: len(list((self.root / s).glob("*.json"))) for s in STATUSES}


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def _print_list(drafts: list[Draft]) -> None:
    if not drafts:
        print("（空）")
        return
    print(f"{'id':32} {'kind':8} {'model':18} {'created':20} title")
    for d in drafts:
        print(f"{d.id:32} {d.kind:8} {(d.model or '-')[:18]:18} {d.created_at:20} {d.title[:40]}")


def main():
    parser = argparse.ArgumentParser(description="草稿审校队列")
    parser.add_argument("action", choices=["list", "show", "approve", "reject", "edit", "stats"])
    parser.add_argument("draft_id", nargs="?", default="")
    parser.add_argument("--status", default="pending", choices=list(STATUSES))
    parser.add_argument("--kind", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--file", default="", help="edit 时用文件内容替换草稿")
    parser.add_argument("--dir", default="review")
    args = parser.parse_args()

    store = ReviewStore(args.dir)

    if args.action == "list":
        _print_list(store.list(args.status, args.kind))
    elif args.action == "stats":
        print(json.dumps(store.stats(), ensure_ascii=False))
    elif args.action == "show":
        d = store.get(args.draft_id)
        print(f"# {d.title}\n# id={d.id} kind={d.kind} status={d.status} model={d.model} "
              f"created={d.created_at}\n")
        print(d.content)
    elif args.action == "approve":
        d = store.approve(args.draft_id)
        print(f"已通过 {d.id}，发布结果：{d.meta.get('published')}")
    elif args.action == "reject":
        d = store.reject(args.draft_id, args.reason)
        print(f"已驳回 {d.id}：{args.reason or '（无理由）'}")
    elif args.action == "edit":
        if not args.file:
            raise SystemExit("edit 需要 --file")
        d = store.edit(args.draft_id, Path(args.file).read_text(encoding="utf-8"))
        print(f"已更新 {d.id}（{len(d.content)} 字），确认后用 approve 发布")


if __name__ == "__main__":
    main()
