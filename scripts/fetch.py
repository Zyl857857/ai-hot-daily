"""抓取多源 AI 资讯，去重合并为候选列表。

输出: data/cache/candidates.json
"""
import calendar
import difflib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "cache"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def log(msg):
    print(msg, flush=True)


def clean_html(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def http_get(url, direct=False, params=None):
    """direct=True 时强制直连（国内源走代理反而握手失败）。"""
    kw = {"headers": {"User-Agent": UA}, "timeout": 30, "params": params}
    if direct:
        kw["proxies"] = {"http": None, "https": None}
    return requests.get(url, **kw)


def parse_feed(name, url, direct=False):
    resp = http_get(url, direct=direct)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    for e in feed.entries:
        title = clean_html(e.get("title", ""))
        link = e.get("link", "")
        if not title or not link:
            continue
        published = None
        for key in ("published_parsed", "updated_parsed"):
            if e.get(key):
                published = datetime.fromtimestamp(
                    calendar.timegm(e[key]), tz=timezone.utc)
                break
        items.append({
            "title": title,
            "url": link,
            "source": name,
            "summary": clean_html(e.get("summary", ""))[:300],
            "published": published.isoformat() if published else None,
        })
    return items


def fetch_hn(name, queries, min_points):
    since = int((datetime.now(timezone.utc) - timedelta(hours=36)).timestamp())
    items = []
    seen_ids = set()
    for q in queries:
        try:
            resp = requests.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={"tags": "story", "query": q,
                        "numericFilters": f"points>{min_points},created_at_i>{since}",
                        "hitsPerPage": 30},
                headers={"User-Agent": UA}, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            log(f"  [warn] HN 查询 {q} 失败: {exc}")
            continue
        for h in resp.json().get("hits", []):
            hid = h.get("objectID")
            title = h.get("title") or ""
            if not title or hid in seen_ids:
                continue
            seen_ids.add(hid)
            link = h.get("url") or f"https://news.ycombinator.com/item?id={hid}"
            items.append({
                "title": title,
                "url": link,
                "source": name,
                "summary": f"Hacker News 讨论热度：{h.get('points', 0)} 分 / {h.get('num_comments', 0)} 评论",
                "published": h.get("created_at"),
            })
    return items


def collect_sources(cfg, max_age_hours):
    all_items = []
    for src in cfg["sources"]:
        name = src["name"]
        time.sleep(1.0)  # 请求间隔，降低被限流概率
        try:
            if src["type"] == "hn":
                items = fetch_hn(name, src.get("queries", ["AI"]),
                                 src.get("min_points", 50))
            else:
                items = parse_feed(name, src["url"],
                                   direct=bool(src.get("direct")))
            # Google News 的标题形如 "标题 - 媒体名"，拆出真实来源
            if src.get("google_news"):
                for it in items:
                    parts = it["title"].rsplit(" - ", 1)
                    if len(parts) == 2 and len(parts[1]) <= 20:
                        it["title"], it["source"] = parts[0], parts[1].strip()
            log(f"[ok] {name}: {len(items)} 条")
            all_items.extend(items)
        except Exception as exc:
            log(f"[warn] {name} 抓取失败，跳过: {exc}")
    return all_items


def filter_by_age(items, max_age_hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    kept, undated = [], []
    for it in items:
        if not it.get("published"):
            undated.append(it)
            continue
        try:
            pub = datetime.fromisoformat(it["published"].replace("Z", "+00:00"))
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            if pub >= cutoff:
                kept.append(it)
        except ValueError:
            undated.append(it)
    return kept + undated


def dedupe(items):
    """标题归一化 + 相似度去重。"""

    def norm(t):
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", t.lower())

    kept, keys = [], []
    for it in items:
        k = norm(it["title"])
        if not k:
            continue
        dup = any(difflib.SequenceMatcher(None, k, prev).ratio() > 0.82
                  for prev in keys)
        if dup:
            continue
        keys.append(k)
        kept.append(it)
    return kept


def select_candidates(items, per_source_cap, total):
    """按时间倒序，限制单一来源占比后截断。"""

    def sort_key(it):
        pub = it.get("published")
        if not pub:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            p = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            return p if p.tzinfo else p.replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    items.sort(key=sort_key, reverse=True)
    by_source, out = {}, []
    for it in items:
        c = by_source.get(it["source"], 0)
        if c >= per_source_cap:
            continue
        by_source[it["source"]] = c + 1
        out.append(it)
        if len(out) >= total:
            break
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    fcfg = cfg.get("fetch", {})

    log("== 开始抓取 ==")
    items = collect_sources(cfg, fcfg.get("max_age_hours", 36))
    if not items:
        log("[error] 所有源都抓取失败")
        sys.exit(1)

    items = filter_by_age(items, fcfg.get("max_age_hours", 36))
    log(f"时间过滤后: {len(items)} 条")

    items = dedupe(items)
    log(f"去重后: {len(items)} 条")

    candidates = select_candidates(items, fcfg.get("per_source_cap", 10),
                                   fcfg.get("candidate_count", 40))
    log(f"最终候选: {len(candidates)} 条（来自 {len({c['source'] for c in candidates})} 个来源）")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates": candidates,
    }
    (OUT_DIR / "candidates.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"已写入 {OUT_DIR / 'candidates.json'}")


if __name__ == "__main__":
    main()
