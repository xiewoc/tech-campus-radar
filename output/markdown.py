"""把过滤后的内容渲染成一份按日期归档的 Markdown。"""

from datetime import datetime
from pathlib import Path

from filter import clean_text
from models import NewsItem, Position


def _link(title: str, url: str) -> str:
    return f"[{clean_text(title, 120)}]({url})"


def _news_section(items: list[NewsItem]) -> list[str]:
    if not items:
        return ["_今天没有新内容_", ""]
    by_source: dict[str, list[NewsItem]] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)

    lines: list[str] = []
    for source, group in by_source.items():
        lines.append(f"### {source}")
        for item in group:
            summary = clean_text(item.summary, 160)
            line = f"- {_link(item.title, item.url)}"
            if summary:
                line += f" — {summary}"
            lines.append(line)
        lines.append("")
    return lines


def _job_section(jobs: list[Position]) -> list[str]:
    if not jobs:
        return ["_今天没有新岗位_", ""]
    by_company: dict[str, list[Position]] = {}
    for job in jobs:
        by_company.setdefault(job.company, []).append(job)

    lines: list[str] = []
    for company, group in by_company.items():
        lines.append(f"### {company}（{len(group)}）")
        for job in group:
            meta = " · ".join(x for x in (job.city, job.category) if x)
            lines.append(f"- **{clean_text(job.title, 80)}**" + (f" — {meta}" if meta else ""))
            duty = clean_text(job.responsibilities, 120)
            if duty:
                lines.append(f"  - 职责：{duty}")
            lines.append(f"  - [职位详情]({job.url})")
        lines.append("")
    return lines


def _demand_section(skills: list[tuple[str, int]] | None, total: int) -> list[str]:
    if not skills:
        return []
    total = total or 1
    lines = ["## 岗位需求速览", "", "| 技能 | 提及岗位 | 占比 |", "| --- | --- | --- |"]
    for name, count in skills:
        lines.append(f"| {name} | {count} | {count / total:.0%} |")
    lines.append("")
    return lines


def render_digest(date_str: str, news: list[NewsItem], jobs: list[Position],
                  job_feeds: list[NewsItem] | None = None,
                  stats: dict | None = None,
                  demand_skills: list[tuple[str, int]] | None = None,
                  demand_total: int = 0) -> str:
    job_feeds = job_feeds or []
    head = [
        f"# 科技与校招雷达 · {date_str}",
        "",
        f"> 新闻 {len(news)} 条 · 校招岗位 {len(jobs)} 条 · 招聘 RSS {len(job_feeds)} 条"
        + (f" · {stats}" if stats else ""),
        "",
        "## 科技新闻",
        "",
        *_news_section(news),
        *_demand_section(demand_skills, demand_total),
        "## 校招岗位",
        "",
        *_job_section(jobs),
    ]
    if job_feeds:
        head += ["## 招聘 RSS（海外/远程）", "", *_news_section(job_feeds)]
    return "\n".join(head).rstrip() + "\n"


def write_digest(directory: str | Path, date_str: str, content: str) -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{date_str}.md"
    path.write_text(content, encoding="utf-8")
    return path