"""生成归档清单 manifest.json（前端据此展示日期导航）。

读取: data/*.json（排除 cache 与 manifest 本身）
输出: data/manifest.json
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    days = []
    for f in sorted(DATA_DIR.glob("????-??-??.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[warn] 跳过损坏文件 {f.name}: {exc}")
            continue
        report = d.get("report_text", "")
        days.append({
            "date": d.get("date", f.stem),
            "count": len(d.get("items", [])),
            "preview": report.split("\n")[0][:60],
        })
    manifest = {
        "updated_at": (datetime.now(timezone.utc)
                       + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M"),
        "days": days,
    }
    out = DATA_DIR / "manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"manifest 更新完成，共 {len(days)} 天归档")


if __name__ == "__main__":
    main()
