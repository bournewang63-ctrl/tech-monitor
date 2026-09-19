"""資料存放：data/items/YYYY-MM.jsonl（依首次偵測月份分檔，文字格式方便 git 追蹤）。"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("AIMON_DATA", ROOT / "data"))
ITEMS = DATA / "items"


BACKLOG_DAYS = 14  # 首次偵測時已發布超過 14 天的舊文（RSS 第一次抓到的整批歷史文章）不列入統計


def month_key(ts: str) -> str:
    return ts[:7]


def _ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def effective_time(it: dict) -> str | None:
    """統計用時間（UTC ISO）；回傳 None 表示是舊文，不列入。
    - 一般項目：用發布時間；比首次偵測早超過 14 天就視為舊文
    - 模型：很久以前建立、最近才上熱門榜 → 用首次偵測時間
    - 漏洞：用 CISA 加入日"""
    seen, pub = _ts(it.get("first_seen")), _ts(it.get("published"))
    if seen is None:
        return None
    fmt = lambda d: d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if it.get("kind") == "vuln":
        return fmt(pub or seen)
    if pub is None or pub > seen + dt.timedelta(hours=1):
        return fmt(seen)
    if pub < seen - dt.timedelta(days=BACKLOG_DAYS):
        return fmt(seen) if it.get("kind") == "model" else None
    if it.get("kind") == "model" and pub < seen - dt.timedelta(days=7):
        return fmt(seen)
    return fmt(pub)


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
                    t = effective_time(it)
                    if t is None:  # 舊文：不載入，下次存檔時會一併清掉
                        continue
                    it["t"] = t
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
