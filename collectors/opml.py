"""OPML 解析：把订阅包拆成 {name, url, category} 列表。"""

import xml.etree.ElementTree as ET
from pathlib import Path


def load_opml(path: str | Path) -> list[dict]:
    feeds: list[dict] = []
    try:
        root = ET.parse(str(path)).getroot()
    except Exception as e:
        print(f"[opml] 解析失败 {path}: {e}")
        return feeds

    def walk(node, category: str = ""):
        for child in node:
            tag = (child.tag or "").lower()
            if tag == "head":
                continue
            text = child.get("text") or child.get("title") or ""
            xml_url = child.get("xmlUrl")
            if xml_url:
                feeds.append({
                    "name": text or xml_url,
                    "url": xml_url,
                    "category": child.get("category") or category,
                    "html_url": child.get("htmlUrl", ""),
                })
            else:
                # body 或者分类分组，继续往下找
                walk(child, text or category)

    walk(root)
    return feeds


def load_opml_dir(directory: str | Path) -> list[dict]:
    """一次性读取目录下所有 .opml / .xml 订阅包。"""
    d = Path(directory)
    if not d.is_dir():
        return []
    feeds: list[dict] = []
    for f in sorted(d.glob("*.opml")) + sorted(d.glob("*.xml")):
        feeds.extend(load_opml(f))
    return feeds
