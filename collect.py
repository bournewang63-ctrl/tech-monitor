"""每小時執行：抓取所有來源 → 關鍵字分類 → 合併進 data/。

用法：python collect.py            抓全部來源
      python collect.py arxiv_ai   只抓指定來源（除錯用）
"""
from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

import yaml

from monitor import store
from monitor.classify import Classifier
from monitor.sources import FETCHERS, SourceError, iso, now_utc

ROOT = Path(__file__).resolve().parent


def within_window(pub: dt.datetime | None, seen: dt.datetime) -> str:
    """統計用時間：發布時間若在偵測前 7 天內就用它，否則用首次偵測時間（例如很久以前建立、最近才爆紅的模型）。"""
    if pub and seen - dt.timedelta(days=7) <= pub <= seen + dt.timedelta(hours=1):
        return iso(min(pub, seen))
    return iso(seen)


def main(only: list[str]) -> int:
    cfg = yaml.safe_load((ROOT / "config/sources.yaml").read_text(encoding="utf-8"))["sources"]
    clf = Classifier()
    items = store.load_months(3)
    status = store.read_json("status.json", {})
    seen = now_utc()
    new_total, events = 0, []

    for src in cfg:
        if only and src["id"] not in only:
            continue
        if src.get("enabled", True) is False:
            continue
        st = status.get(src["id"], {})
        st.update({"name": src["name"], "last_run": iso(seen), "type": src["type"]})
        t0 = time.time()
        try:
            raw = FETCHERS[src["type"]](src)
        except (SourceError, Exception) as e:  # 單一來源失敗不影響其他來源
            st.update({"ok": False, "error": str(e)[:300], "fetched": 0, "new": 0})
            status[src["id"]] = st
            print(f"✗ {src['id']:<16} {e}")
            continue

        if src["type"] == "secdeadlines":
            events += raw
            st.update({"ok": True, "error": "", "fetched": len(raw), "new": 0, "last_ok": iso(seen)})
            status[src["id"]] = st
            print(f"✓ {src['id']:<16} {len(raw)} 個會議")
            continue

        n_new = n_keep = 0
        for r in raw:
            text = " ".join([r["title"], r.get("summary", ""), " ".join(r["extra"].get("keywords") or []),
                             " ".join(r["extra"].get("topics") or [])])
            topics, kws = clf.classify(text)
            hints = r["extra"].get("hint_topics") or []
            if src.get("require_match") and not topics:
                continue
            n_keep += 1
            old = items.get(r["id"])
            if old:
                old["score"] = max(old.get("score", 0), r["score"])
                if src["id"] not in old["sources"]:
                    old["sources"].append(src["id"])
                if r["summary"] and len(r["summary"]) > len(old.get("summary", "")) and src["type"] == "hf_papers":
                    old["summary"] = r["summary"]
                continue
            n_new += 1
            items[r["id"]] = {
                "id": r["id"], "title": r["title"], "url": r["url"], "summary": r["summary"],
                "kind": src["kind"], "sources": [src["id"]], "source_name": src["name"],
                "published": iso(r["published"]) if r["published"] else None,
                "first_seen": iso(seen), "t": iso(r["published"]) if src["kind"] == "vuln" and r["published"] else within_window(r["published"], seen),
                "score": r["score"], "topics": topics, "keywords": kws, "hints": hints,
                "defaults": src.get("default_topics", []), "base": src.get("base_topics", []), "orgs": clf.orgs_in(r["title"] + " " + r["summary"]),
                "lang": src.get("lang", "en"), "official": bool(src.get("official")),
                "extra": {k: v for k, v in r["extra"].items() if k != "hint_topics" and v not in (None, [], "")},
            }
        new_total += n_new
        st.update({"ok": True, "error": "", "fetched": len(raw), "kept": n_keep, "new": n_new,
                   "last_ok": iso(seen), "secs": round(time.time() - t0, 1)})
        status[src["id"]] = st
        print(f"✓ {src['id']:<16} 取得 {len(raw):>4}，相關 {n_keep:>4}，新增 {n_new:>4}")

    store.save(items)
    store.write_json("status.json", status)
    if events or not only:
        if events:
            store.write_json("events.json", {"updated": iso(seen), "events": events})
    ok = sum(1 for s in status.values() if s.get("ok"))
    store.append_run({"t": iso(seen), "new": new_total, "ok": ok, "sources": len(status)})
    print(f"完成：新增 {new_total} 筆，來源正常 {ok}/{len(status)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
