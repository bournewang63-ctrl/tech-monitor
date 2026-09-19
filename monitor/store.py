"""資料存放：data/items/YYYY-MM.jsonl（依首次偵測月份分檔，文字格式方便 git 追蹤）。"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("AIMON_DATA", ROOT / "data"))
ITEMS = DATA / "items"


def month_key(ts: str) -> str:
    return ts[:7]


def load_months(n: int = 3, today: dt.date | None = None) -> dict[str, dict]:
    """讀取最近 n 個月的項目，回傳 {id: item}。"""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    keys, y, m = [], today.year, today.month
    for _ in range(n):
        keys.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    out = {}
    for k in reversed(keys):
        p = ITEMS / f"{k}.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    it = json.loads(line)
                    out[it["id"]] = it
    return out


def save(items: dict[str, dict]) -> None:
    ITEMS.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list] = {}
    for it in items.values():
        by_month.setdefault(month_key(it["first_seen"]), []).append(it)
    for k, lst in by_month.items():
        lst.sort(key=lambda x: (x["first_seen"], x["id"]))
        (ITEMS / f"{k}.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in lst) + "\n", encoding="utf-8")


def read_json(name: str, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def write_json(name: str, obj) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def append_run(rec: dict, keep: int = 24 * 120) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    p = DATA / "runs.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    lines.append(json.dumps(rec, ensure_ascii=False))
    p.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")
