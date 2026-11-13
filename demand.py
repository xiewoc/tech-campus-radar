"""从岗位 JD 里抽取「需求」，聚合成需求画像。

输入是 collectors 采到的 Position（职责 + 要求），输出：
- extract_requirements()  单个岗位的结构化需求（技能 / 方向 / 学历 / 城市）
- aggregate()             全量统计（技能词频、方向分布、学历门槛、城市热度、各公司差异）
- render_report()         一份 Markdown 需求报告
"""

import re
from collections import Counter
from datetime import datetime

from filter import clean_text
from models import Position

# ------------------------------------------------------------------
# 词表：canonical -> 别名。英文按词边界匹配，中文按子串匹配。
# ------------------------------------------------------------------
SKILLS: dict[str, list[str]] = {
    # 语言 / 工具
    "Python": ["python"],
    "C++": ["c++", "cpp"],
    "C": ["c语言"],
    "Java": ["java"],
    "Go": ["golang", "go语言"],
    "Rust": ["rust"],
    "JavaScript": ["javascript", "js"],
    "TypeScript": ["typescript"],
    "Scala": ["scala"],
    "SQL": ["sql"],
    "Shell": ["shell", "bash"],
    "CUDA": ["cuda"],
    "Verilog": ["verilog"],
    "VHDL": ["vhdl"],
    "MATLAB": ["matlab"],
    # AI / 算法
    "PyTorch": ["pytorch"],
    "TensorFlow": ["tensorflow"],
    "MindSpore": ["mindspore"],
    "PaddlePaddle": ["paddlepaddle", "飞桨"],
    "Transformer": ["transformer"],
    "大模型/LLM": ["大模型", "llm", "large language model"],
    "机器学习": ["机器学习", "machine learning"],
    "深度学习": ["深度学习", "deep learning"],
    "强化学习": ["强化学习", "reinforcement learning", "rlhf"],
    "计算机视觉": ["计算机视觉", "cv方向", "视觉算法", "computer vision"],
    "NLP": ["nlp", "自然语言处理", "natural language processing"],
    "推荐系统": ["推荐系统", "推荐算法", "搜推", "推荐模型"],
    "多模态": ["多模态", "multimodal"],
    "语音": ["语音识别", "语音合成", "asr", "tts"],
    "知识图谱": ["知识图谱"],
    "AIGC": ["aigc", "生成式"],
    "RAG": ["rag", "检索增强"],
    "Agent": ["agent", "智能体"],
    "微调": ["微调", "fine-tune", "finetune", "lora"],
    "预训练": ["预训练", "pretrain"],
    "推理优化": ["推理优化", "推理加速", "推理性能", "inference"],
    "算子": ["算子", "operator"],
    "编译器": ["编译器", "compiler", "tvm", "mlir"],
    # 系统 / 工程
    "分布式": ["分布式", "distributed"],
    "高并发": ["高并发", "高可用"],
    "Linux": ["linux"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Docker": ["docker"],
    "数据库": ["数据库", "mysql", "postgres", "tidb"],
    "Redis": ["redis"],
    "Kafka": ["kafka"],
    "微服务": ["微服务", "microservice"],
    "云原生": ["云原生", "cloud native"],
    "Spark": ["spark"],
    "Flink": ["flink"],
    "Hadoop": ["hadoop", "hive"],
    # 硬件 / 芯片
    "芯片设计": ["芯片设计", "数字ic", "模拟ic", "soc设计"],
    "SoC": ["soc"],
    "芯片验证": ["芯片验证", "dv验证", "uvm"],
    "版图": ["版图", "layout"],
    "射频": ["射频", "rf电路"],
    "电源": ["电源设计", "dcdc", "电源管理"],
    "PCB": ["pcb"],
    "硬件设计": ["硬件设计", "硬件开发", "单板", "原理图"],
    "EMC": ["emc", "emc设计"],
    "信号完整性": ["信号完整性", "si仿真"],
    "热设计": ["热设计", "散热设计", "热仿真"],
    "结构设计": ["结构设计", "结构工程师"],
    # 安全 / 数据
    "网络安全": ["网络安全", "渗透测试", "漏洞", "安全攻防"],
    "密码学": ["密码学", "密码算法"],
    "逆向": ["逆向", "reverse engineering"],
    "数据挖掘": ["数据挖掘", "数据科学"],
    "大数据": ["大数据", "big data"],
    "数据分析": ["数据分析", "数据建模"],
}

DIRECTIONS: dict[str, list[str]] = {
    "算法": ["算法", "algorithm"],
    "后端": ["后端", "服务端", "后台开发"],
    "前端": ["前端", "web前端"],
    "客户端": ["客户端", "android", "ios"],
    "芯片": ["芯片", "ic设计", "半导体"],
    "硬件": ["硬件", "单板", "结构", "热设计"],
    "测试": ["测试", "质量保障", "qa"],
    "运维": ["运维", "sre", "devops"],
    "数据": ["数据工程", "数据分析", "大数据"],
    "安全": ["安全", "隐私"],
    "产品": ["产品经理", "产品设计"],
    "研究": ["研究员", "研究", "research"],
}

DEGREES = {"博士": ["博士", "phd"], "硕士": ["硕士", "研究生", "master"], "本科": ["本科", "学士", "bachelor"]}
MAJORS = ["计算机", "软件工程", "电子信息", "通信", "自动化", "数学", "物理", "微电子", "人工智能", "统计"]
CITY_SPLIT = re.compile(r"[,，/;；\s]+")


def _compile(alias: str) -> re.Pattern:
    esc = re.escape(alias)
    if re.search(r"[A-Za-z]", alias):
        return re.compile(rf"(?<![A-Za-z0-9_]){esc}(?![A-Za-z0-9_])", re.I)
    return re.compile(esc)


_SKILL_PATTERNS = {k: [_compile(a) for a in v] for k, v in SKILLS.items()}
_DIRECTION_PATTERNS = {k: [_compile(a) for a in v] for k, v in DIRECTIONS.items()}
_DEGREE_PATTERNS = {k: [_compile(a) for a in v] for k, v in DEGREES.items()}
_MAJOR_PATTERNS = [_compile(m) for m in MAJORS]


def register_skills(names: list[str]) -> None:
    """把自定义技能词追加进词表（config.yaml 的 demand.extra_skills）。"""
    for name in names or []:
        name = (name or "").strip()
        if not name or name in SKILLS:
            continue
        SKILLS[name] = [name.lower()]
        _SKILL_PATTERNS[name] = [_compile(n) for n in SKILLS[name]]


def _hits(text: str, patterns) -> list[str]:
    return [name for name, pats in patterns.items() if any(p.search(text) for p in pats)]


_CITY_FIX = {"深圳总部": "深圳", "北京总部": "北京", "上海总部": "上海", "杭州总部": "杭州"}


def _norm_city(name: str) -> str:
    name = _CITY_FIX.get(name, name).strip()
    return re.sub(r"(市|省|特别行政区)$", "", name)


def extract_requirements(p: Position) -> dict:
    text = clean_text(f"{p.title} {p.category} {p.responsibilities} {p.requirements}")
    # 方向只看标题和分类，避免 JD 正文里「软硬件协同」这类顺带提及把方向带偏
    head = clean_text(f"{p.title} {p.category}")
    cities = list(dict.fromkeys(
        _norm_city(c) for c in CITY_SPLIT.split(p.city or "") if len(c) >= 2
    ))
    return {
        "company": p.company,
        "title": p.title,
        "skills": _hits(text, _SKILL_PATTERNS),
        "directions": _hits(head, _DIRECTION_PATTERNS) or ([p.category] if p.category else []),
        "degrees": _hits(text, _DEGREE_PATTERNS),
        "majors": [m for m, pat in zip(MAJORS, _MAJOR_PATTERNS) if pat.search(text)],
        "cities": cities,
        "has_jd": bool((p.responsibilities or p.requirements or "").strip()),
    }


def aggregate(positions: list[Position], min_count: int = 2) -> dict:
    rows = [extract_requirements(p) for p in positions]
    n = len(rows) or 1

    skills = Counter(s for r in rows for s in r["skills"])
    directions = Counter(d for r in rows for d in r["directions"])
    degrees = Counter(d for r in rows for d in r["degrees"])
    majors = Counter(m for r in rows for m in r["majors"])
    cities = Counter(c for r in rows for c in r["cities"])
    companies = Counter(r["company"] for r in rows)

    company_skills: dict[str, Counter] = {}
    for r in rows:
        company_skills.setdefault(r["company"], Counter()).update(r["skills"])

    return {
        "total": len(rows),
        "with_jd": sum(1 for r in rows if r["has_jd"]),
        "companies": companies,
        "skills": [(k, v) for k, v in skills.most_common() if v >= min_count],
        "directions": directions.most_common(),
        "degrees": degrees.most_common(),
        "majors": majors.most_common(10),
        "cities": cities.most_common(15),
        "company_skills": {c: s.most_common(6) for c, s in company_skills.items()},
        "total_for_ratio": n,
    }


def _bar(count: int, total: int, width: int = 20) -> str:
    filled = round(width * count / total) if total else 0
    return "█" * filled + "·" * (width - filled)


def render_report(date_str: str, agg: dict, top_n: int = 25) -> str:
    total = agg["total_for_ratio"]
    lines = [
        f"# 岗位需求画像 · {date_str}",
        "",
        f"> 样本 {agg['total']} 个岗位（含 JD {agg['with_jd']} 个）"
        f" · 来源：{', '.join(f'{c} {n}' for c, n in agg['companies'].most_common())}",
        "",
        f"## 技能需求 Top {top_n}",
        "",
        "| # | 技能 | 提及岗位 | 占比 | |",
        "| --- | --- | --- | --- | --- |",
    ]
    for i, (skill, count) in enumerate(agg["skills"][:top_n], 1):
        lines.append(f"| {i} | {skill} | {count} | {count / total:.0%} | {_bar(count, total)} |")

    lines += ["", "## 岗位方向分布", "", "| 方向 | 岗位数 | 占比 |", "| --- | --- | --- |"]
    for name, count in agg["directions"][:12]:
        lines.append(f"| {name} | {count} | {count / total:.0%} |")

    if agg["degrees"]:
        lines += ["", "## 学历门槛（JD 中提及）", "", "| 学历 | 岗位数 |", "| --- | --- |"]
        for name, count in agg["degrees"]:
            lines.append(f"| {name} | {count} |")

    if agg["majors"]:
        lines += ["", "## 专业偏好（JD 中提及）", ""]
        lines.append(" · ".join(f"{m}（{c}）" for m, c in agg["majors"]))

    if agg["cities"]:
        lines += ["", "## 城市热度 Top 15", "", "| 城市 | 岗位数 |", "| --- | --- |"]
        for name, count in agg["cities"]:
            lines.append(f"| {name} | {count} |")

    lines += ["", "## 各公司需求差异（Top 6 技能）", ""]
    for company, skills in agg["company_skills"].items():
        joined = "、".join(f"{s}（{c}）" for s, c in skills) or "JD 中未命中已知技能词"
        lines.append(f"- **{company}**：{joined}")

    lines += ["", "---", "", "_词表见 demand.py 的 SKILLS / DIRECTIONS，可在 config.yaml 的 demand.extra_skills 里补充。_", ""]
    return "\n".join(lines)


def write_report(directory, date_str: str, content: str):
    from pathlib import Path

    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{date_str}-demand.md"
    path.write_text(content, encoding="utf-8")
    return path


def summary_table(agg: dict, top_n: int = 12) -> list[tuple[str, int]]:
    return agg["skills"][:top_n]