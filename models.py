from dataclasses import dataclass
from datetime import datetime


@dataclass
class Position:
    """招聘岗位（来自各公司招聘 API）。"""
    company: str
    job_id: str
    title: str
    city: str
    category: str
    url: str
    responsibilities: str = ""   # 岗位职责
    requirements: str = ""       # 岗位要求
    updated_at: datetime | None = None


@dataclass
class NewsItem:
    """RSS 条目，也用来装招聘 RSS 的岗位。kind: news | job。"""
    kind: str
    source: str
    title: str
    url: str
    summary: str = ""
    category: str = ""
    published: datetime | None = None
    feed_url: str = ""           # 该条来自哪个订阅源，聚合接口用来标注来源