"""各資料來源的抓取與解析。每個 fetch_* 回傳「原始項目」清單（尚未分類）。

原始項目欄位：id, title, url, summary, published(UTC ISO), score, extra(dict)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import os
import re
import time
from pathlib import Path

import feedparser
import requests
import yaml
from dateutil import parser as dparser

UTC = dt.timezone.utc
UA = "AI-Tech-Monitor/1.0 (+https://github.com; hourly research dashboard)"
FIXTURES = os.environ.get("AIMON_FIXTURES")  # 離線測試：從資料夾讀取 <source_id>.<ext>
TAG_RE = re.compile(r"<[^>]+>")


class SourceError(RuntimeError):
    pass


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


def iso(d: dt.datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(text: str | None, n: int = 400) -> str:
    t = html.unescape(TAG_RE.sub(" ", text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n] + ("…" if len(t) > n else "")


def get(src: dict, url: str, *, params=None, headers=None, as_json=False, ext="txt"):
    if FIXTURES:
        p = Path(FIXTURES) / f"{src['id']}.{ext}"
        if not p.exists():
            raise SourceError(f"fixture 不存在：{p.name}")
        txt = p.read_text(encoding="utf-8")
        import json
        return json.loads(txt) if as_json else txt
    h = {"User-Agent": UA, **(headers or {})}
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=h, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                raise SourceError(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                raise SourceError(f"HTTP {r.status_code}：{r.text[:120]}")
            return r.json() if as_json else r.text
        except (requests.RequestException, SourceError) as e:
            if attempt == 2 or (isinstance(e, SourceError) and "HTTP 4" in str(e) and "429" not in str(e)):
                raise SourceError(str(e)) from e
            time.sleep(3 * (attempt + 1))


def parse_date(value, tz: str | None = None) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, time.struct_time):
        return dt.datetime(*value[:6], tzinfo=UTC)
    try:
        d = dparser.parse(str(value))
    except (ValueError, OverflowError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dparser.parse(f"2000-01-01T00:00:00{tz}").tzinfo if tz else UTC)
    return d.astimezone(UTC)


def _hid(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- RSS / Atom
def fetch_rss(src: dict) -> list[dict]:
    text = get(src, src["url"], ext="xml")
    feed = feedparser.parse(text)
    if feed.bozo and not feed.entries:
        raise SourceError(f"RSS 解析失敗：{feed.bozo_exception}")
    out = []
    for e in feed.entries:
        raw_date = e.get("published") or e.get("updated")
        if src.get("tz") and raw_date:  # 無時區的日期（如 iThome）以來源時區解讀，不能交給 feedparser 當 UTC
            d = parse_date(raw_date, src["tz"])
        else:
            d = parse_date(e.get("published_parsed") or e.get("updated_parsed")) or parse_date(raw_date)
        link = e.get("link") or ""
        title = clean(e.get("title"), 300)
        if not title:
            continue
        out.append({"id": f"{src['id']}:{_hid(e.get('id') or link or title)}", "title": title, "url": link,
                    "summary": clean(e.get("summary") or e.get("description")), "published": d,
                    "score": 0, "extra": {"tags": [t.get("term") for t in e.get("tags", []) if t.get("term")][:5]}})
    return out


# ---------------------------------------------------------------- arXiv
ARXIV = "https://export.arxiv.org/api/query"


def fetch_arxiv(src: dict) -> list[dict]:
    text = get(src, ARXIV, params={"search_query": src["query"], "sortBy": "submittedDate",
                                   "sortOrder": "descending", "max_results": src.get("max", 100)}, ext="xml")
    feed = feedparser.parse(text)
    out = []
    for e in feed.entries:
        aid = e.get("id", "").rsplit("/abs/", 1)[-1]
        aid = re.sub(r"v\d+$", "", aid)
        if not aid:
            continue
        out.append({"id": f"arxiv:{aid}", "title": clean(e.get("title"), 300), "url": f"https://arxiv.org/abs/{aid}",
                    "summary": clean(e.get("summary")), "published": parse_date(e.get("published_parsed")),
                    "score": 0, "extra": {"cats": [t.get("term") for t in e.get("tags", [])][:6],
                                          "authors": [a.get("name") for a in e.get("authors", [])][:4]}})
    time.sleep(0 if FIXTURES else 3)  # arXiv API 要求間隔 3 秒
    return out


# ---------------------------------------------------------------- Hugging Face
def fetch_hf_papers(src: dict) -> list[dict]:
    data = get(src, src["url"], as_json=True, ext="json")
    out = []
    for it in data:
        p = it.get("paper", {})
        pid = p.get("id")
        if not pid:
            continue
        org = (p.get("organization") or it.get("organization") or {}).get("fullname")
        out.append({"id": f"arxiv:{pid}", "title": clean(p.get("title") or it.get("title"), 300),
                    "url": f"https://huggingface.co/papers/{pid}",
                    "summary": clean(p.get("ai_summary") or p.get("summary")),
                    "published": parse_date(it.get("publishedAt") or p.get("publishedAt")),
                    "score": int(p.get("upvotes") or 0),
                    "extra": {"keywords": (p.get("ai_keywords") or [])[:8], "org": org,
                              "github": p.get("githubRepo"), "stars": p.get("githubStars")}})
    return out


PIPE_TOPIC = {"text-generation": "llm", "text2text-generation": "llm", "image-text-to-text": "multimodal",
              "text-to-image": "multimodal", "text-to-video": "multimodal", "image-to-video": "multimodal",
              "text-to-speech": "multimodal", "automatic-speech-recognition": "multimodal",
              "any-to-any": "multimodal", "robotics": "robotics", "reinforcement-learning": "ai_general"}


def fetch_hf_models(src: dict) -> list[dict]:
    data = get(src, src["url"], as_json=True, ext="json")
    out = []
    for m in data:
        mid = m.get("id") or m.get("modelId")
        if not mid:
            continue
        pipe = m.get("pipeline_tag") or ""
        out.append({"id": f"hf:{mid}", "title": mid, "url": f"https://huggingface.co/{mid}",
                    "summary": f"{pipe}｜下載 {m.get('downloads', 0):,}｜❤ {m.get('likes', 0):,}",
                    "published": parse_date(m.get("createdAt")), "score": int(m.get("likes") or 0),
                    "extra": {"pipeline": pipe, "trending": m.get("trendingScore"), "downloads": m.get("downloads"),
                              "hint_topics": [PIPE_TOPIC[pipe]] if pipe in PIPE_TOPIC else []}})
    return out


# ---------------------------------------------------------------- GitHub
def fetch_github(src: dict) -> list[dict]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    since = (now_utc() - dt.timedelta(days=src.get("days", 7))).date().isoformat()
    out, errors = [], []
    for topic, q in src["queries"].items():
        try:
            data = get({**src, "id": f"{src['id']}_{topic}"}, "https://api.github.com/search/repositories",
                       params={"q": f"{q} created:>={since}", "sort": "stars", "order": "desc",
                               "per_page": src.get("per_query", 15)}, headers=headers, as_json=True, ext="json")
        except SourceError as e:
            errors.append(f"{topic}: {e}")
            continue
        for r in data.get("items", []):
            out.append({"id": f"gh:{r['full_name']}", "title": r["full_name"], "url": r["html_url"],
                        "summary": clean(r.get("description")), "published": parse_date(r.get("created_at")),
                        "score": int(r.get("stargazers_count") or 0),
                        "extra": {"language": r.get("language"), "topics": (r.get("topics") or [])[:8],
                                  "hint_topics": [topic]}})
        time.sleep(0 if FIXTURES else 2.5)  # 搜尋 API 每分鐘 30 次
    if errors and not out:
        raise SourceError("；".join(errors))
    return out


# ---------------------------------------------------------------- Hacker News
def fetch_hn(src: dict) -> list[dict]:
    if src.get("mode") == "front_page":
        url, params = "https://hn.algolia.com/api/v1/search", {"tags": "front_page", "hitsPerPage": 60}
    else:
        since = int((now_utc() - dt.timedelta(hours=src.get("hours", 3))).timestamp())
        url = "https://hn.algolia.com/api/v1/search_by_date"
        params = {"tags": "story", "numericFilters": f"created_at_i>{since}", "hitsPerPage": 500}
    data = get(src, url, params=params, as_json=True, ext="json")
    out = []
    for h in data.get("hits", []):
        if not h.get("title"):
            continue
        oid = h["objectID"]
        out.append({"id": f"hn:{oid}", "title": clean(h["title"], 300),
                    "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
                    "summary": f"HN {h.get('points') or 0} 分・{h.get('num_comments') or 0} 則留言",
                    "published": parse_date(h.get("created_at")), "score": int(h.get("points") or 0),
                    "extra": {"hn": f"https://news.ycombinator.com/item?id={oid}",
                              "comments": h.get("num_comments") or 0}})
    return out


# ---------------------------------------------------------------- CISA KEV
def fetch_kev(src: dict) -> list[dict]:
    data = get(src, src["url"], as_json=True, ext="json")
    since = (now_utc() - dt.timedelta(days=src.get("days", 30))).date()
    out = []
    for v in data.get("vulnerabilities", []):
        added = parse_date(v.get("dateAdded"))
        if not added or added.date() < since:
            continue
        cve = v.get("cveID", "")
        out.append({"id": f"kev:{cve}", "title": f"{cve} {v.get('vulnerabilityName', '')}".strip(),
                    "url": f"https://nvd.nist.gov/vuln/detail/{cve}", "summary": clean(v.get("shortDescription")),
                    "published": added.replace(hour=12), "score": 0,
                    "extra": {"vendor": v.get("vendorProject"), "product": v.get("product"),
                              "ransomware": v.get("knownRansomwareCampaignUse"), "due": v.get("dueDate")}})
    return out


# ---------------------------------------------------------------- sec-deadlines（活動，不進時間序列）
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


def _conf_dates(date_str: str, year: int) -> tuple[dt.date | None, dt.date | None]:
    """把 'November 15-19'、'May 30 - June 3' 轉成日期。"""
    if not date_str:
        return None, None
    s = date_str.replace("–", "-").replace(",", " ")
    m = re.match(r"\s*([A-Za-z]+)\.?\s+(\d{1,2})(?:\s*-\s*(?:([A-Za-z]+)\.?\s+)?(\d{1,2}))?", s)
    if not m:
        return None, None
    mon = next((v for k, v in MONTHS.items() if k.startswith(m.group(1).lower()[:3])), None)
    if not mon:
        return None, None
    try:
        start = dt.date(year, mon, int(m.group(2)))
        mon2 = next((v for k, v in MONTHS.items() if m.group(3) and k.startswith(m.group(3).lower()[:3])), mon)
        end = dt.date(year + (1 if mon2 < mon else 0), mon2, int(m.group(4))) if m.group(4) else start
    except ValueError:
        return None, None
    return start, end


def fetch_secdeadlines(src: dict) -> list[dict]:
    text = get(src, src["url"], ext="yml")
    confs = yaml.safe_load(text) or []
    out = []
    for c in confs:
        year = int(c.get("year") or 0)
        start, end = _conf_dates(str(c.get("date") or ""), year) if year else (None, None)
        deadlines = c.get("deadline") or []
        if isinstance(deadlines, str):
            deadlines = [deadlines]
        dls = [str(parse_date(d, "-12:00").date()) for d in deadlines if parse_date(d, "-12:00")]
        out.append({"name": f"{c.get('name')} {year}".strip(), "desc": c.get("description", ""),
                    "start": str(start) if start else None, "end": str(end) if end else None,
                    "place": c.get("place", ""), "link": c.get("link", ""), "deadlines": dls,
                    "tags": c.get("tags", []), "source": "sec-deadlines"})
    return out


FETCHERS = {"rss": fetch_rss, "arxiv": fetch_arxiv, "hf_papers": fetch_hf_papers, "hf_models": fetch_hf_models,
            "github": fetch_github, "hn": fetch_hn, "kev": fetch_kev, "secdeadlines": fetch_secdeadlines}
