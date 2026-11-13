# 科技 & 校招雷达

把**科技新闻 RSS**、**大厂校招岗位**、**学科前沿情报**合成每日产出：岗位日报、需求画像、前沿简报。

~~~
① 源配置   sources.yaml（news / topics / jobs）+ sources/opml/*.opml + providers/
     ↓
② 调度     GitHub Actions：radar 每 4 小时 · brief 每天 06:00
     ↓
③ 采集     collectors/rss.py（feedparser）· providers/（招聘 API）· tavily.py（前沿检索）
     ↓
④ 过滤     filter.py（关键词前置）+ storage.py（SQLite 去重）+ LLM（可选，最后一步）
     ↓
⑤ 输出     output/markdown.py（日报）+ demand.py（需求画像）+ brief.py（前沿简报）+ push.py
     ↑
⑥ 模型层   llm.py（可插拔 completion，httpx）+ review.py（草稿审校队列）
~~~

核心原则：**过滤尽量前置**——脏数据、重复、广告、无关内容在进 LLM 和推送之前就拦掉，省 Token 也降噪音。

---

## 1. 环境与安装

- Python **3.10+**（实测 3.14）
- 依赖：`httpx`、`pyyaml`、`feedparser`（**不需要 openai SDK**，模型调用走 `llm.py` 的 httpx 实现）

~~~bash
pip install -r requirements.txt
~~~

用到的密钥（都可选）：

| 环境变量 | 用途 | 不配的后果 |
| --- | --- | --- |
| `TAVILY_API_KEY` | 前沿简报检索 | 简报整体跳过，新闻/岗位不受影响 |
| `OPENAI_API_KEY` | LLM 语义筛选 + 简报归纳 | 简报退化成抽取式摘要，筛选跳过 |
| （无）飞书 webhook | 推送 | 只写文件不推送 |


---

## 2. 快速开始

~~~bash
# 1) 全量跑一遍：新闻 + 岗位 → digests/ →（若配了 webhook）推送
python main.py

# 2) 只跑前沿简报（需要 TAVILY_API_KEY）
python main.py --only brief

# 3) 先小范围验证
python main.py --only news --limit-feeds 4
python main.py --only jobs --keywords 算法 --max-pages 1 --no-push --no-store
~~~

产物：

~~~
digests/2026-11-07.md            # 当日日报：新闻 + 需求速览 + 校招岗位 + 招聘 RSS
digests/2026-11-07-demand.md     # 岗位需求画像
briefs/2026-11-07.md             # 学科前沿简报（人读）
briefs/2026-11-07.json           # 同一份简报的结构化数据（机读）
seen.db                          # 去重库（items + seen 两张表）
~~~

---

## 3. 命令行调用指南

`python main.py --help` 的参数如下：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--config` | `config.yaml` | 过滤/推送/LLM 等运行配置 |
| `--sources` | `sources.yaml` | 源配置（RSS、OPML 目录、招聘 provider） |
| `--only` | `all` | `all` / `news` / `jobs` / `brief`，只跑其中一段（`brief` 只出前沿简报） |
| `--max-pages` | `0` | 覆盖 `fetch.job_max_pages`（每家公司每关键词翻页数） |
| `--keywords` | 空 | 逗号分隔，覆盖 `config.keywords` |
| `--limit-feeds` | `0` | 只跑前 N 个 RSS 源，调源时用 |
| `--no-llm` | 关 | 强制跳过 LLM，哪怕配置里开着 |
| `--no-push` | 关 | 不推送，只写文件 |
| `--no-store` | 关 | 不读写 `seen.db`（调试用，不会产生去重副作用） |

### 常用组合

~~~bash
# 只验证岗位链路，缩到 1 个关键词 1 页，不落库
python main.py --only jobs --keywords 算法 --max-pages 1 --no-store --no-push

# 只更新新闻，跳过所有招聘 API（不触发 WAF）
python main.py --only news

# 换一份配置做灰度（比如只留 huawei / 走另一个 webhook）
python main.py --config config.test.yaml --sources sources.test.yaml

# 加到计划任务里，输出重定向到日志
python main.py >> radar.log 2>&1
~~~

### 行为约定

- 退出码恒为 `0`（单源失败只打印日志，不中断流水线）。
- 同一天多次运行会**覆盖** `digests/YYYY-MM-DD.md`，文件是该日的**当次快照**。
- `seen.db` 只有不带 `--no-store` 时才写：RSS 按 URL 哈希、岗位按 `company + job_id`。
- 展示条数由 `output.max_news / max_jobs / max_job_rss` 截断，但**入库是全量**，不会因为没展示就明天重来。

---

## 4. 作为库调用

流水线各层都能单独调。

~~~python
import asyncio

# ③ 采集：任意 RSS 列表
from collectors import rss
feeds = [{"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "kind": "news"}]
items = asyncio.run(rss.collect(feeds, concurrency=4))
print(items[0].title, items[0].url)

# ③ 采集：单家公司（provider 签名统一）
from providers import huawei
jobs = asyncio.run(huawei.fetch(keyword="算法", max_pages=1, page_size=10))
print(len(jobs), jobs[0].title, jobs[0].category)

# ④ 过滤 + 去重
from filter import keyword_filter, dedupe_positions
from storage import Store
jobs = dedupe_positions(jobs)
jobs = keyword_filter(jobs, ["算法", "研发", "技术"])
store = Store("seen.db")
fresh = store.filter_new_positions(jobs)
store.mark_seen(fresh)
store.close()

# ⑤ 需求画像（不落盘，只要统计结果）
import demand
agg = demand.aggregate(fresh, min_count=2)
print(agg["skills"][:10])        # [('大模型/LLM', 235), ('机器学习', 162), ...]
print(agg["cities"][:5])         # [('北京', 274), ('上海', 180), ...]

# ⑤ 推送
from push import push_feishu
asyncio.run(push_feishu("hello", "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"))
~~~

`Position` 和 `NewsItem` 的结构见 `models.py`：

~~~python
Position(company, job_id, title, city, category, url,
         responsibilities="", requirements="", updated_at=None)
NewsItem(kind, source, title, url, summary="", category="", published=None)
~~~

---

## 5. 配置详解

### 5.1 `config.yaml`（④ 过滤 / ③ 节奏 / ⑤ 输出）

| 键 | 说明 |
| --- | --- |
| `keywords` | 岗位搜索关键词，每个词都会去每家 API 查一遍 |
| `category_allow` | 岗位类别白名单，命中任意子串才保留；留空则不过滤 |
| `news_filter.include` | 新闻关键词白名单，空数组=不限制 |
| `news_filter.exclude` | 新闻噪声词（广告/推广/sponsored…） |
| `news_filter.min_title_len` | 标题短于该长度直接丢 |
| `demand.enabled` | 是否生成岗位需求画像 |
| `demand.top_n` / `min_count` | 报告展示条数 / 最低提及次数 |
| `demand.extra_skills` | 追加自定义技能词，如 `["vLLM", "RAGFlow"]` |
| `llm.provider` / `base_url` / `model` | 模型服务商与端点（默认 openai_compatible） |
| `llm.temperature` / `max_tokens` / `timeout` / `retries` | 采样与重试参数 |
| `llm.enabled` | 打开后才会调用模型；失败自动放行，不会漏内容 |
| `tavily.api_key` | Tavily key，留空则读 `TAVILY_API_KEY` |
| `tavily.search_depth` / `days` / `max_results` / `concurrency` | 检索深度、时间窗、每主题条数、并发数 |
| `brief.enabled` / `output_dir` | 是否出简报、产物目录 |
| `brief.max_items_per_topic` / `use_llm` / `language` / `push` | 每主题展示条数、是否 LLM 归纳、语言、是否推飞书 |
| `llm.api_key` / `base_url` / `model` | 兼容 OpenAI 协议的任意端点；`api_key` 为空时读环境变量 `OPENAI_API_KEY` |
| `llm.min_score` | 语义打分阈值（1–10） |
| `fetch.job_max_pages` | 每家公司每关键词翻页数，建议 ≤3 |
| `fetch.request_interval` | 请求间隔（秒），同时也是 RSS 源之间的间隔 |
| `fetch.rss_concurrency` | RSS 并发数 |
| `output.max_news/max_jobs/max_job_rss` | 只截断 Markdown 和推送 |
| `feishu_webhook` | 留空或含 `REPLACE_ME` 时自动跳过推送 |
| `digest_dir` / `db_path` | 产物目录 / 去重库路径 |

### 5.2 `sources.yaml`（① 源配置）

~~~yaml
opml_dir: sources/opml        # 目录里所有 *.opml / *.xml 自动加载

news:                         # 直连 RSS
  - {name: TechCrunch, url: "https://techcrunch.com/feed/", category: 科技}

jobs:
  providers: [huawei, bytedance, tencent, meituan, xiaomi]   # providers/ 下的模块名
  rss:                        # 海外/远程招聘 RSS
    - {name: Jobicy, url: "https://jobicy.com/?feed=job_feed", category: 远程}
~~~

- OPML 与 `news` 列表会按 URL 去重合并，可以直接互相补充。
- 想停掉某个源：注释掉那一行即可（或加 `enabled: false` —— 目前未支持，直接注释最简单）。

---

## 6. 数据源接口调用指南

> 所有接口都是各家官网前端在用的公开 JSON 接口，随时可能变；代码里对单源异常做了隔离。

### 6.1 华为（新官网网关）

| 项 | 值 |
| --- | --- |
| 列表 | `POST https://apigw-dgg-b0.huawei.com/api/apig/channelhw/recruitmentPosition/pub/getJobPage?X-HW-ID=app_000000035886` |
| Body | `{"curPage":1,"pageSize":20,"jobType":"CR","keyWord":"算法"}` |
| 必需头 | `X-HW-ID`、`x-jalor-tenantAlias: hcm`、**`x-language: zh_CN`**、`x-Referer`、`x-alb-gray: prod`、`X-Csrf-Token:` |
| 详情 | `.../recruitmentPosition/pub/getRecruitmentPositionDetail` + `{"advertisementId": 36384}` |
| 真实 JD | `.../recruitmentPosition/pub/getPositionIntentionList` + `{"jobId": 103891}` |

要点：

- 缺 `x-language` 会返回 `{"status":"SUCCESS","data":{"result":null}}`——**不报错但没数据**，最容易误判成「站点没岗位」。
- 列表里的 `mainBusiness` / `jobRequire` 是占位文本「请您详见岗位意向中的岗位职责」，必须再打「岗位意向」接口。
- 老的 portal5 接口（`career.huawei.com/reccampportal/services/.../getJob/newHr/page/...`）已废弃，只会返回 2 条历史岗位。

### 6.2 字节跳动

~~~http
POST https://jobs.bytedance.com/api/v1/search/job/posts
Referer: https://jobs.bytedance.com/campus

{"keyword":"算法","limit":100,"offset":0,"portal_type":3,
 "portal_entrance":1,"language":"zh","recruitment_id_list":["201"]}
~~~

列表直接带 `description` / `requirement`，字段：`city_list`、`job_category.name`（形如「研发/算法」）。

### 6.3 腾讯

~~~http
POST https://join.qq.com/api/v1/position/searchPosition
{"projectMappingIdList":[1],"keyword":"算法","pageIndex":1,"pageSize":100}

GET https://join.qq.com/api/v1/jobDetails/getJobDetailsByPostId?postId=1282707395466077186
~~~

`pageIndex` **必须从 1 开始**（传 0 报「最小不能小于1」）；列表没有 JD，要逐条打详情拿 `desc` / `request` / `tidName`。

### 6.4 美团

~~~http
POST https://zhaopin.meituan.com/api/official/job/getJobList
{"page":{"pageNo":1,"pageSize":100},"jobShareType":"1","keywords":"算法",
 "jobType":[{"code":"1","subCode":[]},{"code":"2","subCode":[]}]}
~~~

列表字段是 `jobDuty` / `jobRequirement`（不是 description），分类用 `jobFamilyGroup`（「算法」）比 `jobFamily`（「技术类」）更贴近筛选白名单。

### 6.5 小米（自建飞书招聘）

~~~http
POST https://xiaomi.jobs.f.mioffice.cn/api/v1/search/job/posts
portal-channel: campus
portal-platform: pc
website-path: campus
~~~

缺这三个头会落到社招池；详情 `GET /api/v1/job/posts/{id}`。

### 6.6 网易与飞书招聘系（蔚来 / MiniMax）

**网易**：`POST https://hr.163.com/api/hr163/position/queryPage`，body `{"pageNum":1,"pageSize":50,"keyword":"算法","workType":"2"}`。`workType` 实测：`0`=社招（约 2000 条）、`1`=实习/校招（约 600 条）、`2`=派遣外包（60 条）。网易校招走的是「实习生」池子，所以 provider 只取 `workType=1`，并过滤标题含「派遣/外包」的岗位。列表直接带 `description` / `requirement`。

**蔚来 / MiniMax**：都是飞书招聘（ATSX）自建门户，共用一套接口：

~~~http
POST https://{host}/api/v1/search/job/posts
portal-channel: campus        # MiniMax 这里是公司路径 379481
portal-platform: pc
website-path: campus
~~~

共同逻辑抽在 `providers/feishu_core.py`，新增同平台公司只要写 10 行：

~~~python
from providers.feishu_core import FeishuSource, fetch as _fetch

SOURCE = FeishuSource(company="nio", host="nio.jobs.feishu.cn",
                      channel="campus",
                      apply_prefix="https://nio.jobs.feishu.cn/campus/position")

async def fetch(keyword="", max_pages=10, page_size=50, request_interval=0.4, **_):
    return await _fetch(SOURCE, keyword=keyword, max_pages=max_pages,
                        page_size=page_size, request_interval=request_interval)
~~~

### 6.7 常见返回码

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 405 空 body | 字节/小米的 WAF 限流 | 等约 1 分钟自愈；代码已做 3s/6s/12s 退避，别把 `job_max_pages` 开太大 |
| SUCCESS + null data | 华为缺 `x-language` 头 | 按 6.1 补齐请求头 |
| 403 Forbidden | 华为旧 portal5 接口无浏览器头 / WWR 在 Cloudflare 后面 | 前者按 6.1 换新接口，后者需要浏览器渲染 |
| 解析为空 | 源本身 XML 非法（如机器之心） | 单源隔离，不影响整体 |

---

## 7. 聚合接口（带来源）

`aggregate.py` 把各家 API 和 RSS 收成同一 schema，每条都带 `source`；`api.py` 提供三种调用方式，走的是同一套核心逻辑，返回结构完全一致：

| 方式 | 入口 | 适用 |
| --- | --- | --- |
| HTTP | `python api.py --port 8000` | 跨进程/跨语言、前端直接调 |
| 命令行 | `python api.py --call jobs ...` | 一次性拿数据、塞进别的脚本 |
| 函数 | `import api; api.get_jobs(...)` | 在 Python 里同步调用 |

### 方式一：HTTP 服务

~~~bash
python api.py --host 127.0.0.1 --port 8000
~~~

### 接口

| 路径 | 参数 | 说明 |
| --- | --- | --- |
| `GET /health` | — | 存活检查 |
| `GET /sources` | — | 全部来源：provider / label / type / site / endpoint |
| `GET /jobs` | `keyword`、`companies`（逗号分隔）、`max_pages`、`page_size`、`limit`、`interval` | 聚合岗位 |
| `GET /news` | `source`（源名模糊匹配）、`limit` | 聚合新闻 |
| `GET /demand` | `keyword`、`companies`、`max_pages`、`min_count`、`top_n` | 一次拿到需求画像（技能/方向/学历/城市 + Markdown） |

### 返回结构（/jobs）

~~~json
{
  "items": [
    {
      "company": "nio",
      "company_label": "蔚来",
      "job_id": "7698198573305252106",
      "title": "校招-音频质量算法工程师",
      "city": "上海",
      "category": "数字技术/算法",
      "url": "https://nio.jobs.feishu.cn/campus/position/7698198573305252106/detail",
      "responsibilities": "…",
      "requirements": "…",
      "source": {
        "provider": "nio",
        "label": "蔚来",
        "type": "api",
        "site": "https://nio.jobs.feishu.cn/campus",
        "endpoint": "https://nio.jobs.feishu.cn/api/v1/search/job/posts",
        "fetched_at": "2026-11-07T14:55:18+08:00"
      }
    }
  ],
  "total": 4,
  "sources": [
    {"provider": "nio", "label": "蔚来", "ok": true, "count": 3, "errors": [], "elapsed_ms": 1621},
    {"provider": "netease", "label": "网易", "ok": true, "count": 1, "errors": [], "elapsed_ms": 1180}
  ],
  "took_ms": 2753,
  "fetched_at": "2026-11-07T14:55:18+08:00"
}
~~~

`sources` 里会逐个报告每家的成功/失败与耗时——某家挂了不会影响整体，`errors` 里能直接看到原因。

### 方式二：命令行直接调

不起服务，跑一次把 JSON 打到 stdout，`--out` 存文件：

~~~bash
python api.py --call jobs --keyword 算法 --companies nio,netease --page-size 3 --limit 5
python api.py --call news --source TechCrunch --limit 10
python api.py --call demand --keyword 算法 --companies nio --top 10
python api.py --call sources
python api.py --call all --keyword 算法 --limit 5          # 岗位 + 新闻一起
python api.py --call jobs --keyword 算法 --out jobs.json
~~~

可用参数：`--keyword` / `--companies` / `--source` / `--max-pages` / `--page-size` / `--limit` / `--interval` / `--min-count` / `--top` / `--out`。

### 方式三：函数调用

同步版（内部自己跑事件循环；在 Jupyter 等已有 loop 的环境里会丢到独立线程，不会报 `asyncio.run() cannot be called from a running event loop`）：

~~~python
import api

api.get_sources()                                    # 全部来源
api.get_health()                                     # {"ok": True, ...}

jobs = api.get_jobs(keyword="算法", companies=["nio", "netease"], limit=5)
news = api.get_news(source="TechCrunch", limit=10)
profile = api.get_demand(keyword="算法", top_n=10)    # 需求画像（结构化 + markdown）

print(jobs["items"][0]["source"]["site"])
print(profile["skills"][:3])

api.save_json(jobs, "jobs.json")                     # 顺手落盘
~~~

一次拿岗位 + 新闻：

~~~python
bundle = api.get_all(keyword="算法", limit=5, news_limit=10)
bundle["jobs"]["total"], bundle["news"]["total"]
~~~

异步版（HTTP 层内部用的就是这套）：

~~~python
import asyncio, api

async def main():
    jobs = await api.aget_jobs(keyword="算法", companies=["nio"])
    profile = await api.aget_demand(keyword="算法")
    return jobs, profile

asyncio.run(main())
~~~

函数签名：

| 函数 | 说明 |
| --- | --- |
| `get_jobs(keyword="", companies=None, max_pages=1, page_size=20, limit=0, interval=0.4)` | 聚合岗位 |
| `get_news(source="", limit=50)` | 聚合新闻 |
| `get_demand(keyword="", companies=None, max_pages=1, page_size=20, min_count=2, top_n=25)` | 需求画像 |
| `get_all(keyword="", companies=None, max_pages=1, limit=20, news_limit=30)` | 岗位 + 新闻 |
| `get_sources()` / `get_health()` | 来源清单 / 存活检查 |
| `save_json(obj, path)` | 结果存文件 |
| `aget_jobs / aget_news / aget_demand / aget_all` | 同名异步版 |

底层仍然可以直接用 `aggregate.collect_positions()`（返回原始 `Position` 对象 + 来源统计），需要自己做二次处理时用它。

### curl 示例

~~~bash
curl -s "http://127.0.0.1:8000/jobs?keyword=%E7%AE%97%E6%B3%95&companies=nio,netease&max_pages=1&limit=5"
curl -s "http://127.0.0.1:8000/news?source=TechCrunch&limit=10"
curl -s "http://127.0.0.1:8000/demand?keyword=%E7%AE%97%E6%B3%95&top_n=10"
~~~

---

## 8. 每日前沿简报（Tavily）

目标：每天自动把「学科前沿」检索一遍，整理成结构化简报——人读 Markdown、机读 JSON。

### 工作流

~~~
sources.yaml: topics（每个主题 = 一条检索式 + 可选站点/时间窗）
        ↓  Tavily search（并发，单主题失败隔离）
   Tavily 原始结果（title / url / content / published / score）
        ↓  跨主题按 URL 去重
        ↓  LLM 归纳（无 key 则退化为抽取式）
briefs/YYYY-MM-DD.md   ← 速览 / 分主题摘要 / 每条要点+意义 / 趋势 / 值得追踪
briefs/YYYY-MM-DD.json ← 同一份数据的结构化版本
        ↓（可选）
   飞书推送（brief.push: true）
~~~

### 配置

`sources.yaml` 里的主题（默认已带 4 个）：

~~~yaml
topics:
  - name: 大模型与推理
    query: "large language model reasoning inference post-training research"
    domains: [arxiv.org, openai.com, anthropic.com, deepmind.google, huggingface.co]
    days: 7
  - name: AI for Science
    query: "AI for science protein structure materials discovery simulation"
    domains: [nature.com, science.org, arxiv.org]
    days: 14
~~~

`config.yaml`：

~~~yaml
tavily:
  api_key: ""               # 或用环境变量 TAVILY_API_KEY
  search_depth: "advanced"  # basic 更快、advanced 更全
  topic: "news"             # news | general
  days: 7
  max_results: 8
  concurrency: 3

brief:
  enabled: true
  output_dir: "briefs"
  max_items_per_topic: 6
  use_llm: true
  language: "zh"
  push: false
~~~

### 运行

~~~bash
export TAVILY_API_KEY=tvly-xxx
export OPENAI_API_KEY=sk-xxx          # 不加也能跑，只是摘要变成抽取式

python main.py --only brief
python main.py --only brief --no-push  # 只写文件
~~~

也可以直接调模块：

~~~python
import asyncio, brief, yaml

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
sources = yaml.safe_load(open("sources.yaml", encoding="utf-8"))
res = asyncio.run(brief.run(cfg, sources))
print(res["stats"], res["paths"]["markdown"])
~~~

### 产物结构

`briefs/YYYY-MM-DD.md`：

~~~markdown
# 学科前沿简报 · 2026-11-13

> Tavily 检索 4 个主题 · 命中 23 条（去重 3） · 生成于 06:02 · 引擎 tavily+llm

## 今日速览
- …（3-5 条一句话）

## 一、大模型与推理
**一句话**：…
- **[标题](url)**
  - 要点：…
  - 为什么重要：…
  - 来源：arxiv.org · 2026-11-11

## 趋势观察
## 值得追踪
~~~

`briefs/YYYY-MM-DD.json`：

~~~json
{
  "date": "2026-11-13",
  "generated_at": "2026-11-13T06:02:11+08:00",
  "engine": "tavily+llm",
  "stats": {"topics": 4, "ok_topics": 4, "items": 23, "duplicates_removed": 3},
  "overview": ["…"],
  "topics": [{"name": "大模型与推理", "summary": "…",
              "items": [{"title": "…", "url": "…", "domain": "arxiv.org",
                         "published": "2026-11-11", "point": "…", "why": "…"}]}],
  "trends": ["…"],
  "watch": ["…"]
}
~~~

### 降级行为

| 情况 | 表现 |
| --- | --- |
| 没有 `TAVILY_API_KEY` | 打印一行提示并跳过，新闻/岗位照常跑 |
| 没有 LLM key，或调用失败 | 自动退化为抽取式摘要（正文截断作「要点」），结构不变 |
| 某个主题检索失败 | 该主题标 `ok:false` + 错误信息，其余主题照常出简报 |
| 主题之间内容重复 | 按 URL 跨主题去重，计入 `duplicates_removed` |
| 所有主题都失败 | 打印每主题错误后直接返回，**不写空文件**（避免库里堆垃圾） |

### 成本与质量

- 每个主题只取 `max_results` 条、喂给模型 `max_items_per_topic` 条，正文截断到 320 字；
- `domains` 限定站点能显著降噪（arxiv / 官方博客 / 期刊）；
- `days` 控制时间窗，日报用 7 天、综述类主题可放 14 天；
- 模型被要求「只用给定材料、原样保留 title/url」，代码侧按 URL 回填，避免改写链接。

---

## 9. 模型层：可插拔 completion + 审校

所有模型调用统一走 `llm.py`——httpx 直连 `POST {base_url}/chat/completions`，`base_url` / `api_key` / `model` 全由配置决定，不绑定任何厂商 SDK。

### 配置

~~~yaml
llm:
  provider: openai_compatible   # 内置：openai_compatible / mock
  base_url: "https://api.openai.com/v1"
  api_key: ""                   # 留空读 OPENAI_API_KEY
  model: "gpt-4o-mini"
  temperature: 0.2
  max_tokens: 2048
  timeout: 60
  json_mode: false
  retries: 3
  extra_headers: {}             # 某些网关需要自定义头
~~~

### 直接调用

~~~python
import asyncio, llm

cfg = llm.config_from({"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"})
res = asyncio.run(llm.complete([{"role": "user", "content": "hi"}], cfg))
print(res.text, res.usage, res.latency_ms)

data = asyncio.run(llm.complete_json([{"role": "user", "content": "给我一段 JSON"}], cfg))
~~~

### 接入自己的服务（可插拔）

~~~python
import llm

@llm.register("my_gateway")
class MyGateway(llm.OpenAICompatible):
    def _headers(self):
        h = super()._headers()
        h["x-tenant"] = "team-a"
        return h

cfg = llm.LLMConfig(provider="my_gateway",
                    base_url="https://gw.internal/v1",
                    api_key="sk-...")
~~~

只要服务兼容 `{base_url}/chat/completions`，继承 `OpenAICompatible` 即可；协议完全不同就继承 `llm.Provider` 实现一个 `chat()`。

### 审校队列

模型产出不直接上线，先落草稿：`brief.review: true` 时，简报生成后进 `review/pending/<id>.json`，`approve` 之后才写 `briefs/`。

~~~bash
python review.py list
python review.py show   20261113-brief-865dc6
python review.py edit   20261113-brief-865dc6 --file draft.md
python review.py approve 20261113-brief-865dc6
python review.py reject 20261113-brief-865dc6 --reason "来源不可靠"
python review.py stats
~~~

草稿是纯 JSON（`content` 是 markdown，`data` 是结构化数据），方便 diff、进 git、二次编辑。

新增一类需要审校的产物：

~~~python
import review
review.register_publisher("my_kind", lambda draft: {"path": "..."})
~~~

`approve()` 会先执行 publisher，成功后才把草稿挪到 `review/approved/`，避免「状态变了但没发出去」。

---

## 10. 输出说明

### 7.1 日报 `digests/YYYY-MM-DD.md`

~~~
# 科技与校招雷达 · 2026-11-07
> 新闻 80 条 · 校招岗位 150 条 · 招聘 RSS 40 条
## 科技新闻        ← 按来源分组
## 岗位需求速览    ← Top 12 技能 + 占比
## 校招岗位        ← 按公司分组，带职责摘要和详情链接
## 招聘 RSS（海外/远程）
~~~

### 7.2 需求画像 `digests/YYYY-MM-DD-demand.md`

技能 Top N（带占比和条形图）、方向分布、学历门槛、专业偏好、城市 Top 15、各公司 Top 6 技能。

抽取是**纯规则匹配**（`demand.py` 的 `SKILLS` / `DIRECTIONS` 词表），不花 Token。方向只从「标题 + 分类」判断，技能从全文判断——否则 JD 里一句「软硬件协同」就会把算法岗算进硬件方向。

### 7.3 去重库 `seen.db`

~~~sql
items(uid TEXT PRIMARY KEY, kind, source, title, url, category, published, first_seen)
seen (company TEXT, job_id TEXT, first_seen, PRIMARY KEY(company, job_id))
~~~

重置去重（想重新推一遍历史）：

~~~bash
rm seen.db                # 或者
sqlite3 seen.db "DELETE FROM items; DELETE FROM seen;"
~~~

---

## 11. 定时调度

### GitHub Actions（仓库里已带两个）

| 工作流 | 频率 | 命令 | 产物 |
| --- | --- | --- | --- |
| `radar.yml` | 每 4 小时 | `python main.py` | `digests/` + `seen.db` |
| `brief.yml` | 每天 06:00（Asia/Shanghai） | `python main.py --only brief` | `briefs/` |

### GitHub Actions 详情

`.github/workflows/radar.yml` 每 4 小时跑一次，并把 `seen.db` 和 `digests/` 提交回仓库。需要：

1. 仓库 Settings → Actions → Workflow permissions 选 **Read and write**；
2. 想用 LLM 的话加 Secret `OPENAI_API_KEY`。

### Windows 计划任务

~~~powershell
schtasks /create /tn "TechRadar" /sc hourly /mo 4 ^
  /tr "cmd /c cd /d E:\Desktop\编程\Python\wtfff && python main.py >> radar.log 2>&1"
~~~

### Linux crontab

~~~bash
0 */4 * * * cd /opt/radar && python main.py >> radar.log 2>&1
~~~

---

## 12. 目录结构

~~~
wtfff/
├── main.py                 # 流水线编排 + CLI
├── config.yaml             # ④⑤ 过滤/LLM/推送/输出
├── sources.yaml            # ① 源配置
├── sources/opml/           # ① OPML 订阅包（自动加载）
├── collectors/
│   ├── rss.py              # ③ RSS 抓取解析（并发/退避/单源隔离）
│   ├── opml.py             # ① OPML 解析
│   └── jobs.py             # ③ 招聘 provider 调度
├── providers/              # ③ 华为/字节/腾讯/美团/小米/网易/蔚来/MiniMax
│   └── feishu_core.py      #    飞书招聘通用适配器
├── filter.py               # ④ 清洗·去重·关键词·LLM
├── storage.py              # ④ SQLite 去重
├── demand.py               # ⑤ 需求画像
├── brief.py                # ⑥ 前沿简报：检索→去重→归纳→渲染
├── tavily.py               # ⑥ Tavily 搜索客户端（纯 httpx）
├── llm.py                  # ⑥ 可插拔 completion 层（httpx，无 SDK）
├── review.py               # ⑥ 审校队列 + CLI
├── aggregate.py            # ⑥ 聚合 + 来源标注
├── api.py                  # ⑥ 聚合 HTTP 接口
├── output/markdown.py      # ⑤ 日报渲染
├── push.py                 # ⑤ 飞书推送
├── models.py               # Position / NewsItem
├── test_providers.py       # 招聘接口连通性自测
└── qykjxw.py               # 历史脚本（已并入 collectors/rss.py）
~~~

---

## 13. 排查 FAQ

**Q：新闻是 0 条？**
先 `python main.py --only news --limit-feeds 2` 看单个源是否报错；再看 `sources.yaml` 的 URL 是否需要代理。被墙的源会在日志里打印 `[rss] 失败 xxx`。

**Q：岗位只有华为有数据？**
检查 `category_allow`——腾讯只暴露到「技术」，字节是「研发/算法」，小米是「算法类」，美团是「算法」；白名单里没有对应子串时这些岗位会被整批过滤掉。

**Q：跑一次要多久？**
16 个 RSS 源约 20–30 秒；岗位按 1 个关键词 × 5 家 × 1 页约 60–90 秒（华为每多一条 JD 就多一次意向请求）。关键词和页数上去后线性增长。

**Q：能不能只存库不生成文件？**
目前不行，Markdown 是必经输出；改 `output` 的条数为 0 也不行（会写空壳）。需要的话可以在 `main.py` 加一个 `--no-digest`。

**Q：`pip install` 后仍报 `ModuleNotFoundError: feedparser`？**
多半是装到了另一个 Python。用 `python -m pip install -r requirements.txt`，并确认 `python -V` 与运行时一致。

---

## 14. 招聘接口连通性自测

~~~bash
python test_providers.py 算法 5 90
~~~

参数依次是：关键词、每页条数、单家超时秒数。脚本会**自动发现** `providers/` 下所有公司（`feishu_core` 除外），输出每家的耗时、条数、JD 长度和样例。