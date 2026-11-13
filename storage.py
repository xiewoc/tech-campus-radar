import hashlib
import sqlite3

from models import NewsItem, Position


def item_uid(item: NewsItem) -> str:
    """链接优先，没有链接就用标题；统一小写避免大小写差异造成的重复。"""
    raw = (item.url or "").strip().lower() or (item.title or "").strip().lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class Store:
    """统一去重层：RSS 条目按 uid 去重，岗位按 (company, job_id) 去重。"""

    def __init__(self, db_path: str = "seen.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS items (
                uid        TEXT PRIMARY KEY,
                kind       TEXT NOT NULL,
                source     TEXT,
                title      TEXT,
                url        TEXT,
                category   TEXT,
                published  TEXT,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS seen (
                company    TEXT NOT NULL,
                job_id     TEXT NOT NULL,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (company, job_id)
            )
        """)
        self.conn.commit()

    # ---------- RSS ----------

    def is_new_item(self, item: NewsItem) -> bool:
        cur = self.conn.execute("SELECT 1 FROM items WHERE uid = ? LIMIT 1", (item_uid(item),))
        return cur.fetchone() is None

    def filter_new_items(self, items: list[NewsItem]) -> list[NewsItem]:
        return [i for i in items if self.is_new_item(i)]

    def mark_items(self, items: list[NewsItem]) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO items (uid, kind, source, title, url, category, published)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (item_uid(i), i.kind, i.source, i.title, i.url, i.category,
                 i.published.isoformat() if i.published else None)
                for i in items
            ],
        )
        self.conn.commit()

    # ---------- 岗位 ----------

    def is_seen(self, p: Position) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM seen WHERE company = ? AND job_id = ? LIMIT 1",
            (p.company, p.job_id),
        )
        return cur.fetchone() is not None

    def filter_new_positions(self, positions: list[Position]) -> list[Position]:
        return [p for p in positions if not self.is_seen(p)]

    def mark_seen(self, positions: list[Position]) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO seen (company, job_id) VALUES (?, ?)",
            [(p.company, p.job_id) for p in positions],
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
