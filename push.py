"""⑤ 推送层：飞书 webhook。没有配置就静默跳过。"""

import httpx

MAX_LEN = 20000      # 飞书单条文本上限 30KB，留点余量


def _chunks(text: str, size: int = MAX_LEN) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks, buf = [], []
    for line in text.splitlines(keepends=True):
        if sum(len(x) for x in buf) + len(line) > size:
            chunks.append("".join(buf))
            buf = []
        buf.append(line)
    if buf:
        chunks.append("".join(buf))
    return chunks


async def push_feishu(text: str, webhook: str) -> bool:
    if not text or not webhook or "REPLACE_ME" in webhook:
        return False
    ok = True
    async with httpx.AsyncClient(timeout=30) as client:
        for part in _chunks(text):
            payload = {"msg_type": "text", "content": {"text": part}}
            try:
                resp = await client.post(webhook, json=payload)
                if resp.status_code >= 400:
                    print(f"[push] 飞书错误 {resp.status_code}: {resp.text[:200]}")
                    ok = False
            except Exception as e:
                print(f"[push] 飞书异常：{e}")
                ok = False
    return ok
