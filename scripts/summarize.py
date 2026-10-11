"""调用 LLM 精选热点、生成中文摘要与日报文案；失败时自动降级为纯聚合。

读取: data/cache/candidates.json
输出: data/YYYY-MM-DD.json （日期按北京时间）
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "candidates.json"
DATA_DIR = ROOT / "data"
TOP_N = 10


def log(msg):
    print(msg, flush=True)


def beijing_now():
    return datetime.now(timezone.utc) + timedelta(hours=8)


def today_str():
    return beijing_now().strftime("%Y-%m-%d")


def load_key(env_names, dsh_key):
    """key 查找顺序：环境变量（Actions secrets）-> ~/.dsh/.credentials.yaml。"""
    for env in env_names:
        v = os.environ.get(env, "").strip()
        if v:
            return v
    try:
        dsh = yaml.safe_load(
            (Path.home() / ".dsh" / ".credentials.yaml").read_text(encoding="utf-8"))
        v = (dsh or {}).get("refs", {}).get(dsh_key, "")
        if v:
            return str(v).strip()
    except Exception:
        pass
    return None


def build_attempts(cfg):
    """生成 (provider, base_url, model, key) 尝试列表，GLM 优先。"""
    attempts = []
    for provider in ("glm", "deepseek"):
        p = cfg.get(provider, {})
        key = load_key(p.get("key_envs", []), p.get("dsh_key", ""))
        if not key:
            continue
        for att in p.get("attempts", []):
            attempts.append((provider, att["base_url"], att["model"], key))
    return attempts


def chat(base_url, model, key, prompt, timeout):
    resp = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        json={"model": model, "temperature": 0.5, "max_tokens": 6000,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def extract_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("响应中没有 JSON")
    return json.loads(text[start:end + 1])


PROMPT_TMPL = """你是资深 AI 行业编辑，正在帮自媒体博主准备"今日AI热点日报"。

下面是今天抓取到的候选新闻（JSON 数组，index 为序号）。请完成：

1. 精选 {top_n} 条最适合中文社交媒体传播的新闻：
   - 优先选择：大厂重大发布、突破性技术、行业大事件、有争议/有话题度、与普通人相关的 AI 应用
   - 去掉：重复话题（同一事件多条只保留信息最全的一条）、软文、过时话题
2. 每条输出：index（候选序号）、title_zh（英文新闻翻译成简短有力的中文标题，中文新闻保持原标题）、
   hot（1-5 热度，5 为全网刷屏级）、summary（1-2 句中文摘要：说清发生了什么 + 为什么重要）
3. 生成整篇日报文案 report_text，要求：
   - 第一行：📅 今日AI热点速览 | {date_cn}
   - 正文每条一行：一个贴题的 emoji + 标题 + 一句话点睛（不要照抄 summary）
   - 结尾一行话题标签：3-5 个，如 #AI日报 #人工智能 #大模型
   - 全文 500-800 字，口语化、有节奏感，可直接粘贴到微博/小红书/朋友圈

只输出一个 JSON 对象，格式：
{{"items": [{{"index": 0, "title_zh": "...", "hot": 5, "summary": "..."}}], "report_text": "..."}}

候选新闻：
{candidates}"""


def llm_select(candidates, llm_cfg):
    attempts = build_attempts(llm_cfg)
    if not attempts:
        raise RuntimeError("未找到可用的 LLM API key")
    brief = [{"index": i, "title": c["title"], "source": c["source"],
              "published": (c.get("published") or "")[:16],
              "summary": (c.get("summary") or "")[:160]}
             for i, c in enumerate(candidates)]
    date_cn = beijing_now().strftime("%Y年%m月%d日")
    prompt = PROMPT_TMPL.format(top_n=TOP_N, date_cn=date_cn,
                                candidates=json.dumps(brief, ensure_ascii=False))
    timeout = llm_cfg.get("timeout", 240)
    errors = []
    for provider, base_url, model, key in attempts:
        try:
            log(f"[llm] 尝试 {provider} / {model} @ {base_url}")
            text = chat(base_url, model, key, prompt, timeout)
            data = extract_json(text)
            items = data.get("items") or []
            if len(items) < 3:
                raise ValueError(f"精选条数过少: {len(items)}")
            return {"provider": provider, "model": model,
                    "items": items, "report_text": data.get("report_text", "")}
        except Exception as exc:
            log(f"[llm] 失败: {exc}")
            errors.append(f"{provider}/{model}: {exc}")
    raise RuntimeError("所有 LLM 通道均失败；" + " | ".join(errors))


def fallback_select(candidates):
    """LLM 不可用时：按候选顺序取前 TOP_N，摘要用原始标题/摘要拼装。"""
    items = []
    for c in candidates[:TOP_N]:
        items.append({
            "title_zh": c["title"],
            "hot": 3,
            "summary": (c.get("summary") or "")[:120],
        })
    lines = [f"📅 今日AI热点速览 | {beijing_now().strftime('%Y年%m月%d日')}"]
    for it in items:
        lines.append(f"• {it['title_zh']}")
    lines.append("#AI日报 #人工智能")
    return {"provider": "fallback", "model": "none",
            "items": items, "report_text": "\n".join(lines)}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    candidates = cache["candidates"]
    log(f"候选 {len(candidates)} 条，开始精选")

    try:
        result = llm_select(candidates, cfg.get("llm", {}))
        log(f"[llm] 成功: {result['provider']} / {result['model']}")
    except Exception as exc:
        log(f"[warn] {exc}，降级为纯聚合模式")
        result = fallback_select(candidates)

    items = []
    for it in result["items"]:
        idx = it.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
            continue
        c = candidates[idx]
        items.append({
            "title": str(it.get("title_zh") or c["title"]).strip(),
            "url": c["url"],
            "source": c["source"],
            "hot": max(1, min(5, int(it.get("hot") or 3))),
            "summary": str(it.get("summary") or "").strip(),
        })
        if len(items) >= TOP_N:
            break

    day = {
        "date": today_str(),
        "generated_at": beijing_now().strftime("%Y-%m-%d %H:%M"),
        "llm": {"provider": result["provider"], "model": result["model"]},
        "report_text": result["report_text"].strip(),
        "items": items,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / f"{day['date']}.json"
    out.write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"已写入 {out}（精选 {len(items)} 条）")


if __name__ == "__main__":
    main()
