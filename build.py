"""讀取 data/ → 計算統計 → 產生 site/index.html（靜態網頁，可直接放 GitHub Pages）。

用法：python build.py
"""
from __future__ import annotations

import collections
import datetime as dt
import html
import json
import math
import os
import re
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.offline
import yaml

from monitor import store
from monitor.classify import Classifier

ROOT = Path(__file__).resolve().parent
SITE = Path(os.environ.get("AIMON_SITE", ROOT / "site"))
TPE = dt.timezone(dt.timedelta(hours=8))
KIND_NAME = {"paper": "論文", "model": "模型", "repo": "開源專案", "discussion": "社群討論", "news": "新聞",
             "blog": "官方發布", "vuln": "已遭利用漏洞"}
TEXT, GRID, MUTED = "#cbd5e1", "rgba(148,163,184,0.12)", "#7c8aa5"
STOP = set("""The This That These Those With From Into Over Under About After Before When Where What Which Why How
Your Our Their They You We Its It's Show HN Ask New How Why Using Use Used Via And For Are Not Now Can Will Just More
Most Than Then Here There Some All One Two First Last Best Top Big Make Made Get Got Has Have Had Was Were Been
Being Does Did Done Into Onto Toward Towards Against Between Among Within Without Report Reports Says Said Update
Updates Launch Launches Launched Introducing Announces Announced Release Released Releases Week Today Year Years
Day Days Time Data Research Study Paper Towards Based Model Models Learning Large Language Via Efficient Scalable
Toward Beyond Across Through While Should Could Would Might Must Also Every Each Other Many Much Very Still Yet
Inc LLC Ltd Co Corp Mr Ms Dr""".split())
# 太籠統、每天都大量出現的詞，不列入爆量關鍵字
GENERIC = {"ai", "llm", "llms", "gpt-", "agent", "agents", "cve", "iot", "5g", "gpu", "gpus", "model", "models",
           "資安", "漏洞", "駭客", "晶片", "加密", "語言模型", "人工智慧", "exploit", "exploited", "vulnerability",
           "vulnerabilities", "security", "attack", "attacks", "breach", "malware", "robot", "robots", "transformer",
           "hugging", "face", "open", "hub", "api", "building", "new"}
TERM_RE = re.compile(r"\b(?:[A-Z][A-Za-z0-9]*[\-\.]?[A-Za-z0-9]+(?:[\-\.][A-Za-z0-9]+)*|[a-z]+[0-9][A-Za-z0-9\.\-]*)\b")


# ================================================================ 資料
def load(clf: Classifier) -> pd.DataFrame:
    items = store.load_months(3)
    rows = []
    for it in items.values():
        text = " ".join([it["title"], it.get("summary", ""), " ".join(it.get("extra", {}).get("keywords") or []),
                         " ".join(it.get("extra", {}).get("topics") or [])])
        topics, kws = clf.classify(text)  # 以最新關鍵字設定重新分類
        topics = list(dict.fromkeys(topics + it.get("hints", []) + it.get("base", [])))
        by_sem = False
        if not topics and it.get("sem"):  # 關鍵字沒分到類時，才採用語意層的判定
            topics, by_sem = list(it["sem"]), True
        if not topics:
            topics = it.get("defaults", [])
        topics = [t for t in topics if t in clf.topics]
        rows.append({**it, "topics": topics, "by_sem": by_sem, "keywords": kws, "orgs": clf.orgs_in(it["title"] + " " + it.get("summary", ""))})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df["first_seen"] = pd.to_datetime(df["first_seen"], utc=True)
    df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0)
    return df.sort_values("t", ascending=False).reset_index(drop=True)


def explode_topics(df: pd.DataFrame) -> pd.DataFrame:
    e = df.explode("topics").dropna(subset=["topics"])
    return e.rename(columns={"topics": "topic"})


# ================================================================ 小工具
def tpe(ts) -> str:
    return pd.Timestamp(ts).tz_convert(TPE).strftime("%m/%d %H:%M")


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def pct(a, b) -> float:
    return (a / b - 1) * 100 if b else (100.0 if a else 0.0)


def base_layout(fig: go.Figure, h: int, legend=False) -> go.Figure:
    fig.update_layout(template="plotly_dark", height=h, margin=dict(l=8, r=8, t=30 if legend else 8, b=8),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=TEXT, size=11, family="Noto Sans TC, Microsoft JhengHei, sans-serif"),
                      showlegend=legend, legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0, font=dict(size=10)),
                      hoverlabel=dict(font_size=12))
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


# ================================================================ 統計
def compute(df: pd.DataFrame, clf: Classifier, now: pd.Timestamp) -> dict:
    T = clf.topics
    S: dict = {"now": now}
    h1, h24, d7, d8 = now - pd.Timedelta(hours=1), now - pd.Timedelta(hours=24), now - pd.Timedelta(days=7), now - pd.Timedelta(days=8)
    last24 = df[df["t"] >= h24]
    prev7 = df[(df["t"] < h24) & (df["t"] >= d8)]  # 前 7 天（不含最近 24h）
    week = df[df["t"] >= d7]
    S["n1h"] = int((df["first_seen"] >= h1).sum())
    S["n24"], S["n7d"] = len(last24), len(week)
    S["kind24"] = last24["kind"].value_counts().to_dict()
    S["kev7"] = int(((df["kind"] == "vuln") & (df["t"] >= d7)).sum())
    S["heat"] = len(last24) / max(len(prev7) / 7, 1)
    S["sem24"] = int(last24["by_sem"].sum()) if "by_sem" in last24 else 0

    ex = explode_topics(df)
    ex24, exprev = ex[ex["t"] >= h24], ex[(ex["t"] < h24) & (ex["t"] >= d8)]
    c24 = ex24["topic"].value_counts()
    cprev = exprev["topic"].value_counts() / 7
    S["topic24"] = {k: int(c24.get(k, 0)) for k in T}
    S["topic_avg"] = {k: float(cprev.get(k, 0)) for k in T}
    S["growth"] = {k: pct(S["topic24"][k], S["topic_avg"][k]) for k in T}
    S["hot_topic"] = max(T, key=lambda k: S["topic24"][k]) if len(last24) else None
    cand = [k for k in T if S["topic24"][k] >= 3]
    S["rising"] = max(cand, key=lambda k: S["growth"][k]) if cand else S["hot_topic"]

    # 關鍵字爆量：命中關鍵字 + 英文標題專有名詞
    def terms(frame):
        cnt = collections.Counter()
        for _, r in frame.iterrows():
            toks = {k.lower() for k in r["keywords"]}
            if r.get("lang", "en") == "en":
                toks |= {m.lower() for m in TERM_RE.findall(r["title"]) if m not in STOP and len(m) >= 3}
            cnt.update(toks - GENERIC)
        return cnt
    tc24, tcp = terms(last24), terms(prev7)
    burst = []
    for term, c in tc24.items():
        if c < 2:
            continue
        base = tcp.get(term, 0) / 7
        burst.append((term, c, (c - base) / math.sqrt(base + 1)))
    burst.sort(key=lambda x: -x[2])
    S["burst"] = burst[:12]

    orgs = collections.Counter(o for lst in week["orgs"] for o in lst)
    orgs24 = collections.Counter(o for lst in last24["orgs"] for o in lst)
    S["orgs"] = [(o, n, orgs24.get(o, 0)) for o, n in orgs.most_common(12)]

    # 資安
    sec24 = ex24[ex24["topic"] == "security"]
    kev = df[df["kind"] == "vuln"].sort_values("t", ascending=False)
    cve_re = re.compile(r"CVE-\d{4}-\d{4,}")
    S["sec"] = {"kev7": S["kev7"], "kev30": len(kev),
                "ransom": int((kev["extra"].apply(lambda x: (x or {}).get("ransomware") == "Known")).sum()),
                "news24": len(sec24), "cve24": int(last24["title"].str.count(cve_re.pattern).sum()),
                "zeroday": int(last24["title"].str.contains(r"zero-day|0-day|零時差", case=False, regex=True).sum()),
                "latest": kev.head(6).to_dict("records")}

    def sub_counts(frame, groups):
        out = {}
        for name, rx in groups.items():
            r = re.compile(rx, re.I)
            out[name] = int(frame.apply(lambda x: bool(r.search(x["title"] + " " + x.get("summary", ""))), axis=1).sum())
        return out
    S["crypto_sub"] = sub_counts(week, {
        "PQC 標準 (ML-KEM/ML-DSA…)": r"ML-KEM|ML-DSA|SLH-DSA|FN-DSA|\bHQC\b|FIPS 20[3-6]|post-quantum|後量子|抗量子",
        "量子金鑰分發 QKD": r"\bQKD\b|quantum key distribution|量子金鑰", "同態加密 FHE": r"\bFHE\b|homomorphic|同態",
        "零知識證明 ZK": r"zero-knowledge|\bZK|SNARK|零知識", "多方計算 MPC": r"\bMPC\b|multi-?party computation",
        "旁通道攻擊": r"side-channel|旁通道", "格密碼 Lattice": r"lattice", "量子運算": r"quantum comput|qubit|量子運算|量子電腦"})
    S["iot_sub"] = sub_counts(week, {
        "Edge AI / TinyML": r"edge AI|TinyML|邊緣", "ESP32 / MCU": r"ESP32|\bMCU\b|microcontroller|Arduino",
        "LoRa / LoRaWAN": r"LoRa", "5G / 6G": r"\b[56]G\b", "Matter / 智慧家庭": r"Matter|smart home|智慧家庭",
        "Raspberry Pi": r"Raspberry Pi", "工業 IoT / OT": r"IIoT|industrial|\bOT\b|工業", "數位孿生": r"digital twin|數位孿生",
        "IoT 資安": r"(IoT|firmware|物聯網).*(vulnerab|attack|漏洞|攻擊|fuzz)"})
    S["crypto_latest"] = week[week["topics"].apply(lambda t: "crypto" in t or "pqc_quantum" in t)].head(5).to_dict("records")
    S["iot_latest"] = week[week["topics"].apply(lambda t: "iot" in t)].head(4).to_dict("records")

    # 熱門排行（有分數的來源）
    sc = week[week["kind"].isin(["discussion", "repo", "paper", "model"]) & (week["score"] > 0)].copy()
    if len(sc):
        mx = sc.groupby("kind")["score"].transform("max")
        age_h = (now - sc["t"]).dt.total_seconds() / 3600
        sc["hot"] = (sc["score"].apply(math.log1p) / mx.apply(math.log1p) * 80 + (1 - age_h / 168).clip(0) * 20)
        S["top"] = sc.sort_values("hot", ascending=False).head(10).to_dict("records")
    else:
        S["top"] = []
    S["gh"] = week[week["kind"] == "repo"].sort_values("score", ascending=False).head(8).to_dict("records")
    S["hfp"] = week[(week["kind"] == "paper") & week["sources"].apply(lambda s: "hf_papers" in s)] \
        .sort_values("score", ascending=False).head(5).to_dict("records")
    S["hfm"] = df[(df["kind"] == "model") & (df["first_seen"] >= d7)].sort_values("score", ascending=False).head(5).to_dict("records")

    # 重大警示
    alerts = []
    for r in last24[last24["official"] & last24["topics"].apply(lambda t: bool(set(t) & {"llm", "agent", "multimodal", "robotics", "ai_chip"}))].head(5).itertuples():
        alerts.append(("red", "官方發布", r.title, r.url, r.source_name))
    for r in week[(week["kind"] == "discussion") & (week["score"] >= 300)].sort_values("score", ascending=False).head(4).itertuples():
        alerts.append(("yellow", f"HN {int(r.score)} 分", r.title, r.url, "Hacker News"))
    for r in week[(week["kind"] == "repo") & (week["score"] >= 1000)].sort_values("score", ascending=False).head(3).itertuples():
        alerts.append(("yellow", f"⭐ {int(r.score):,}", r.title, r.url, "GitHub"))
    for r in week[(week["kind"] == "paper") & (week["score"] >= 100)].sort_values("score", ascending=False).head(2).itertuples():
        alerts.append(("yellow", f"▲ {int(r.score)}", r.title, r.url, "HF 每日論文"))
    for r in kev[kev["t"] >= now - pd.Timedelta(hours=48)].head(4).itertuples():
        alerts.append(("red", "KEV 新增", r.title, r.url, "CISA"))
    pqc = last24[last24["title"].str.contains(r"FIPS 20[3-6]|ML-KEM|ML-DSA|SLH-DSA|FN-DSA|\bHQC\b", regex=True)]
    for r in pqc.head(2).itertuples():
        alerts.append(("blue", "PQC 標準", r.title, r.url, r.source_name))
    S["alerts"] = alerts

    # 焦點判讀
    heat = S["heat"]
    S["badge"] = "HOT" if heat >= 1.5 else "RISING" if heat >= 1.15 else "STEADY" if heat >= 0.85 else "COOLING"
    S["heat_word"] = {"HOT": "過熱", "RISING": "升溫", "STEADY": "平穩", "COOLING": "降溫"}[S["badge"]]
    return S


def load_events(now: pd.Timestamp) -> tuple[list, list]:
    today = now.tz_convert(TPE).date()
    evs = store.read_json("events.json", {}).get("events", [])
    custom = yaml.safe_load((ROOT / "config/events.yaml").read_text(encoding="utf-8")) or {}
    for e in custom.get("events") or []:
        evs.append({"name": e["name"], "start": str(e.get("start")), "end": str(e.get("end") or e.get("start")),
                    "place": e.get("place", ""), "link": e.get("link", ""), "deadlines": [], "source": "自訂"})
    upcoming, dls = [], []
    for e in evs:
        if e.get("start"):
            try:
                s = dt.date.fromisoformat(e["start"])
                end = dt.date.fromisoformat(e.get("end") or e["start"])
            except ValueError:
                continue
            if end >= today:
                upcoming.append({**e, "days": (s - today).days})
        for d in e.get("deadlines", []):
            dd = dt.date.fromisoformat(d)
            if 0 <= (dd - today).days <= 90:
                dls.append({"name": e["name"], "date": d, "days": (dd - today).days, "link": e.get("link", "")})
    upcoming.sort(key=lambda x: x["start"])
    dls.sort(key=lambda x: x["date"])
    return upcoming[:10], dls[:8]


# ================================================================ 圖表
def figs(df: pd.DataFrame, S: dict, clf: Classifier) -> dict:
    T = clf.topics
    now = S["now"]
    F = {}
    ex = explode_topics(df)
    # 01 每小時新項目（72h，依主題堆疊）
    hstart = (now - pd.Timedelta(hours=71)).floor("h")
    e72 = ex[ex["t"] >= hstart].copy()
    hours = pd.date_range(hstart, now.floor("h"), freq="h")
    e72["h"] = e72["t"].dt.floor("h")
    piv = e72.pivot_table(index="h", columns="topic", values="id", aggfunc="count").reindex(hours).fillna(0)
    x = [h.tz_convert(TPE).strftime("%m/%d %H時") for h in hours]
    f = go.Figure()
    for k, t in T.items():
        if k in piv:
            f.add_bar(x=x, y=piv[k], name=t.short, marker_color=t.color, hovertemplate=f"{t.name} %{{y}}<extra></extra>")
    tot = df[df["t"] >= hstart].assign(h=lambda d: d["t"].dt.floor("h")).groupby("h").size().reindex(hours).fillna(0)
    f.add_scatter(x=x, y=tot.rolling(6, min_periods=1).mean(), name="6小時均線(不重複)", line=dict(color="#f8fafc", width=1.5))
    f.update_layout(barmode="stack")
    f.update_xaxes(type="category", nticks=6, tickangle=0)
    base_layout(f, 330, legend=True)
    f.update_layout(legend=dict(y=-0.1, yanchor="top", font=dict(size=9.5)), margin=dict(t=8, b=8))
    F["hourly"] = f

    # 03 雷達
    keys = list(T)
    mx = max(max(S["topic24"].values()), max(S["topic_avg"].values()), 1)
    f = go.Figure()
    for name, vals, col in [("7日日均", [S["topic_avg"][k] for k in keys], "#64748b"),
                            ("最近24小時", [S["topic24"][k] for k in keys], "#f97316")]:
        r = [v / mx * 100 for v in vals]
        f.add_scatterpolar(r=r + r[:1], theta=[T[k].short for k in keys] + [T[keys[0]].short], fill="toself", name=name,
                           line=dict(color=col, width=2), customdata=vals + vals[:1],
                           hovertemplate="%{theta}: %{customdata:.1f} 則<extra>" + name + "</extra>")
    f.update_layout(polar=dict(bgcolor="rgba(0,0,0,0)", radialaxis=dict(showticklabels=False, gridcolor=GRID, range=[0, 100]),
                               angularaxis=dict(gridcolor=GRID, tickfont=dict(size=10))))
    base_layout(f, 290, legend=True)
    f.update_layout(margin=dict(l=40, r=40, t=30, b=20))
    F["radar"] = f

    # 04 主題×日期熱圖（14 天）
    days = pd.date_range((now.tz_convert(TPE) - pd.Timedelta(days=13)).normalize(), now.tz_convert(TPE).normalize(), freq="D")
    ex["d"] = ex["t"].dt.tz_convert(TPE).dt.normalize()
    hp = ex[ex["d"] >= days[0]].pivot_table(index="topic", columns="d", values="id", aggfunc="count") \
        .reindex(index=keys, columns=days).fillna(0)
    f = go.Figure(go.Heatmap(z=hp.values, x=[d.strftime("%m/%d") for d in days], y=[T[k].short for k in keys],
                             colorscale=[[0, "#0b1535"], [0.3, "#1e3a8a"], [0.6, "#0ea5e9"], [0.85, "#facc15"], [1, "#ef4444"]],
                             showscale=False, hovertemplate="%{y}<br>%{x}：%{z} 則<extra></extra>"))
    f.update_xaxes(nticks=7)
    F["heat"] = base_layout(f, 290)

    # 05 來源類型（7 天）
    kc = df[df["t"] >= now - pd.Timedelta(days=7)]["kind"].value_counts()
    f = go.Figure(go.Pie(labels=[KIND_NAME.get(k, k) for k in kc.index], values=kc.values, hole=0.6, sort=False,
                         marker=dict(colors=["#3b82f6", "#f97316", "#22c55e", "#eab308", "#8b5cf6", "#ef4444", "#14b8a6"]),
                         textinfo="none", hovertemplate="%{label}：%{value} 則<extra></extra>"))
    f.add_annotation(text=f"<b>{S['n7d']}</b><br><span style='font-size:10px'>7日總量</span>", showarrow=False,
                     font=dict(size=18, color="#f1f5f9"))
    F["kinds"] = base_layout(f, 170)

    # 06 成長動能
    g = sorted(keys, key=lambda k: S["growth"][k])
    f = go.Figure(go.Bar(x=[max(min(S["growth"][k], 300), -100) for k in g], y=[T[k].short for k in g], orientation="h",
                         marker_color=["#ef4444" if S["growth"][k] >= 0 else "#22c55e" for k in g],
                         customdata=[[S["topic24"][k], S["topic_avg"][k]] for k in g],
                         hovertemplate="%{y}<br>24h %{customdata[0]} 則，7日日均 %{customdata[1]:.1f}<br>%{x:+.0f}%<extra></extra>"))
    f.update_xaxes(ticksuffix="%")
    F["growth"] = base_layout(f, 290)

    # 07 爆量關鍵字
    b = S["burst"][::-1]
    f = go.Figure(go.Bar(x=[x[2] for x in b], y=[x[0] for x in b], orientation="h", marker_color="#f59e0b",
                         customdata=[x[1] for x in b], hovertemplate="%{y}<br>24h 出現 %{customdata} 次<br>爆量分數 %{x:.1f}<extra></extra>"))
    F["burst"] = base_layout(f, 290)

    # 08 機構
    o = S["orgs"][::-1]
    f = go.Figure()
    f.add_bar(x=[x[1] for x in o], y=[x[0] for x in o], orientation="h", name="7日", marker_color="#3b82f6")
    f.add_bar(x=[x[2] for x in o], y=[x[0] for x in o], orientation="h", name="24小時", marker_color="#f97316")
    f.update_layout(barmode="overlay")
    F["orgs"] = base_layout(f, 290, legend=True)
    return F


# ================================================================ HTML
CSS = """
:root{--bg:#070d1a;--panel:#0e1729;--line:#1e2b45;--text:#dbe4f3;--muted:#7c8aa5;--red:#ef4444;--green:#22c55e;--amber:#f59e0b;--blue:#3b82f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:"Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;font-size:13px}
a{color:#93c5fd;text-decoration:none}a:hover{text-decoration:underline}
.wrap{padding:12px 16px 40px;max-width:1800px;margin:auto}
.disc{font-size:11px;color:var(--muted);margin-bottom:8px}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px}
.title{font-size:20px;font-weight:900;letter-spacing:.5px}.title small{font-size:12px;color:var(--muted);font-weight:400;margin-left:8px}
.tags{display:flex;gap:14px;flex-wrap:wrap}.tag{font-size:11px;font-weight:700;color:#93c5fd;letter-spacing:.5px;display:flex;align-items:center;gap:6px}
.tag i{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 6px var(--green)}.tag.w i{background:var(--red);box-shadow:0 0 6px var(--red)}.tag.w{color:#fca5a5}
.kpis{display:grid;grid-template-columns:repeat(10,minmax(0,1fr));gap:8px;margin-bottom:10px}
.kpi{background:#0b1426;border:1px solid var(--line);border-radius:8px;padding:7px 8px;text-align:center}
.kpi .l{font-size:11px;color:var(--muted)}.kpi .v{font-size:20px;font-weight:800;white-space:nowrap}
.grid{display:grid;gap:10px;margin-bottom:10px}
.r1{grid-template-columns:2.2fr 1.1fr 1.2fr 1.3fr 1fr}.r2{grid-template-columns:1fr 1fr 1fr 1.2fr}
.r3{grid-template-columns:1.1fr 1.1fr 1.3fr 1.3fr 1.1fr 1.1fr}.r4{grid-template-columns:1.2fr 1.2fr 2fr}
.p{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;min-width:0}
.ph{display:flex;align-items:center;gap:8px;font-weight:700;font-size:13px;margin-bottom:6px;color:#e2e8f0}
.ph .n{background:#1d4ed8;color:#fff;border-radius:4px;padding:0 6px;font-size:11px}.ph .s{color:var(--muted);font-weight:400;font-size:11px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:6px 10px;font-size:12.5px}.kv .k{color:var(--muted)}.kv .v{font-weight:700;text-align:right}
.badge{display:block;text-align:center;font-weight:800;border-radius:6px;padding:5px;margin-bottom:8px}
.b-HOT{background:rgba(239,68,68,.15);color:#fca5a5;border:1px solid #b91c1c}.b-RISING{background:rgba(245,158,11,.15);color:#fbbf24;border:1px solid #b45309}
.b-STEADY{background:rgba(148,163,184,.12);color:#cbd5e1;border:1px solid #475569}.b-COOLING{background:rgba(59,130,246,.12);color:#93c5fd;border:1px solid #1d4ed8}
.bar{display:grid;grid-template-columns:150px 1fr 34px;gap:8px;align-items:center;font-size:12px;margin:5px 0}
.bar .t{height:8px;border-radius:5px;background:rgba(148,163,184,.14);overflow:hidden}.bar .t>div{height:100%;border-radius:5px}.bar .c{text-align:right;font-weight:700}
.small{font-size:11.5px;color:var(--muted)}.up{color:var(--red)}.down{color:var(--green)}
ul.list{list-style:none;margin:0;padding:0}ul.list li{padding:5px 0;border-bottom:1px solid var(--line);font-size:12.5px;line-height:1.45}
ul.list li:last-child{border-bottom:none}.meta{font-size:11px;color:var(--muted)}
.chip{display:inline-block;font-size:10.5px;padding:0 6px;border-radius:9px;margin-right:4px;border:1px solid;white-space:nowrap}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle}
.d-red{background:var(--red);box-shadow:0 0 6px var(--red)}.d-yellow{background:#facc15;box-shadow:0 0 6px #facc15}.d-blue{background:var(--blue)}.d-green{background:var(--green)}
table.t{width:100%;border-collapse:collapse;font-size:12px}table.t td,table.t th{padding:4px;border-bottom:1px solid var(--line);text-align:left}
table.t th{color:var(--muted);font-weight:500}.num{text-align:right!important}
.health{display:grid;grid-template-columns:1fr;gap:2px;font-size:12px}.health div{display:flex;align-items:center;padding:3px 0;border-bottom:1px solid var(--line)}
.health b{margin-left:auto;color:#e2e8f0}.r3 .p{max-height:560px;overflow:auto}
.stat{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:8px}.stat div{background:#0b1426;border:1px solid var(--line);border-radius:6px;text-align:center;padding:5px}
.stat b{display:block;font-size:18px}.stat span{font-size:10.5px;color:var(--muted)}
.sem{font-size:30px;font-weight:900;color:var(--red)}
.pill{display:inline-block;border-radius:6px;padding:3px 10px;margin:3px;font-size:12px;font-weight:700;border:1px solid #b45309;color:#fbbf24;background:rgba(245,158,11,.12)}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-top:6px;flex-wrap:wrap}
.tabs button{background:none;border:none;color:var(--muted);padding:8px 12px;cursor:pointer;font-size:13px;border-bottom:2px solid transparent;font-family:inherit}
.tabs button.on{color:#fca5a5;border-color:var(--red)}.tab{display:none;padding:12px 2px}.tab.on{display:block}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;align-items:center}
.filters button{background:#0b1426;border:1px solid var(--line);color:var(--text);border-radius:14px;padding:3px 10px;cursor:pointer;font-size:12px;font-family:inherit}
.filters button.on{border-color:#f59e0b;color:#fbbf24}.filters input,.filters select{background:#0b1426;border:1px solid var(--line);color:var(--text);border-radius:6px;padding:5px 8px;font-family:inherit}
.feed li{display:grid;grid-template-columns:92px 1fr;gap:10px}
.report h3{color:#93c5fd;margin:16px 0 6px}.report p,.report li{line-height:1.7}
@media(max-width:1300px){.r1,.r2,.r3,.r4{grid-template-columns:1fr 1fr}.kpis{grid-template-columns:repeat(5,1fr)}}
@media(max-width:700px){.r1,.r2,.r3,.r4{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(2,1fr)}.feed li{grid-template-columns:1fr}}
"""


def chips(topics, clf) -> str:
    return "".join(f'<span class="chip" style="color:{clf.topics[t].color};border-color:{clf.topics[t].color}55">'
                   f'{esc(clf.topics[t].name)}</span>' for t in topics if t in clf.topics)


def li(r, clf, extra="") -> str:
    return (f'<li><a href="{esc(r["url"])}" target="_blank" rel="noopener">{esc(r["title"])}</a><br>'
            f'<span class="meta">{esc(r["source_name"])}・{tpe(r["t"])}{extra}</span> {chips(r["topics"], clf)}</li>')


def bars(d: dict, color: str) -> str:
    mx = max(list(d.values()) + [1])
    return "".join(f'<div class="bar"><span>{esc(k)}</span><div class="t"><div style="width:{v / mx * 100:.0f}%;'
                   f'background:{color}"></div></div><span class="c">{v}</span></div>' for k, v in d.items())


def panel(n, title, sub, body) -> str:
    return f'<section class="p"><div class="ph"><span class="n">{n}</span>{title}<span class="s">{sub}</span></div>{body}</section>'


def fig_div(key) -> str:
    return f'<div id="fig_{key}"></div>'


def render(df, S, F, clf, events, dls, status, runs) -> str:
    T = clf.topics
    now = S["now"]
    ok = sum(1 for s in status.values() if s.get("ok"))
    hot = T[S["hot_topic"]].name if S["hot_topic"] else "—"
    rising = T[S["rising"]].name if S["rising"] else "—"
    last_run = runs[-1]["t"] if runs else None
    kpis = [("最後更新", pd.Timestamp(last_run).tz_convert(TPE).strftime("%m/%d %H:%M") if last_run else "—", ""),
            ("本次新增", S["n1h"], ""), ("24 小時", S["n24"], ""), ("7 天", S["n7d"], ""),
            ("論文 24h", S["kind24"].get("paper", 0), ""),
            ("模型+專案 24h", S["kind24"].get("model", 0) + S["kind24"].get("repo", 0), ""),
            ("新聞+官方 24h", S["kind24"].get("news", 0) + S["kind24"].get("blog", 0), ""),
            ("KEV 漏洞 7日", S["kev7"], "up" if S["kev7"] else ""), ("最熱領域", hot, "up"),
            ("來源正常", f"{ok}/{len(status)}", "down" if ok == len(status) else "up")]
    kpi_html = "".join(f'<div class="kpi"><div class="l">{l}</div><div class="v {c}" '
                       f'style="{"font-size:14px" if len(str(v)) > 7 else ""}">{esc(v)}</div></div>' for l, v, c in kpis)

    # 02 焦點判讀
    top1 = S["top"][0] if S["top"] else None
    burst1 = S["burst"][0][0] if S["burst"] else "—"
    p02 = (f'<span class="badge b-{S["badge"]}">{S["badge"]}｜整體{S["heat_word"]}</span><div class="kv">'
           f'<span class="k">熱度指數</span><span class="v">{S["heat"]:.2f} 倍</span>'
           f'<span class="k">最熱領域</span><span class="v">{esc(hot)}</span>'
           f'<span class="k">升溫最快</span><span class="v up">{esc(rising)}（{S["growth"].get(S["rising"], 0):+.0f}%）</span>'
           f'<span class="k">爆量關鍵字</span><span class="v">{esc(burst1)}</span>'
           f'<span class="k">官方發布 24h</span><span class="v">{S["kind24"].get("blog", 0)} 則</span>'
           f'<span class="k">語意層補抓 24h</span><span class="v">{S["sem24"]} 則</span>'
           f'<span class="k">重大警示</span><span class="v up">{len(S["alerts"])} 項</span>'
           f'<span class="k">最熱項目</span><span class="v" style="font-weight:500">{esc(top1["title"][:38]) if top1 else "—"}</span></div>'
           f'<div class="small" style="margin-top:8px">熱度指數＝近 24 小時項目數 ÷ 前 7 日日均</div>')

    sec = S["sec"]
    kc = df[df["t"] >= now - pd.Timedelta(days=7)]["kind"].value_counts()
    kcol = ["#3b82f6", "#f97316", "#22c55e", "#eab308", "#8b5cf6", "#ef4444", "#14b8a6"]
    p05 = fig_div("kinds") + "".join(
        f'<div class="bar" style="grid-template-columns:14px 1fr 44px 40px"><span class="dot" style="background:{kcol[i % 7]}"></span>'
        f'<span>{KIND_NAME.get(k, k)}</span><span class="c">{v}</span><span class="c small">{v / max(kc.sum(), 1):.0%}</span></div>'
        for i, (k, v) in enumerate(kc.items()))
    p09 = (f'<div class="stat"><div><b class="up">{sec["kev7"]}</b><span>KEV 新增 7日</span></div>'
           f'<div><b>{sec["kev30"]}</b><span>KEV 30日</span></div><div><b class="up">{sec["ransom"]}</b><span>勒索軟體已利用</span></div>'
           f'<div><b>{sec["news24"]}</b><span>資安新聞 24h</span></div><div><b>{sec["cve24"]}</b><span>CVE 提及 24h</span></div>'
           f'<div><b>{sec["zeroday"]}</b><span>零時差提及 24h</span></div></div>'
           '<table class="t"><tr><th>CVE</th><th>廠商／產品</th><th>加入日</th></tr>' + "".join(
               f'<tr><td><a href="{esc(r["url"])}" target="_blank">{esc(r["title"].split(" ")[0])}</a></td>'
               f'<td>{esc((r["extra"] or {}).get("vendor", ""))} {esc((r["extra"] or {}).get("product", ""))}'
               f'{" 🔒勒索" if (r["extra"] or {}).get("ransomware") == "Known" else ""}</td>'
               f'<td>{pd.Timestamp(r["t"]).strftime("%m/%d")}</td></tr>' for r in sec["latest"]) + "</table>")

    p10 = bars(S["crypto_sub"], "#06b6d4") + '<ul class="list">' + "".join(li(r, clf) for r in S["crypto_latest"][:3]) + "</ul>"
    p11 = bars(S["iot_sub"], "#a3a3a3") + '<ul class="list">' + "".join(li(r, clf) for r in S["iot_latest"][:2]) + "</ul>"
    p12 = '<ul class="list">' + ("".join(
        f'<li><span class="dot d-{c}"></span><b>{esc(tag)}</b>　<a href="{esc(u)}" target="_blank">{esc(t[:90])}</a>'
        f'<br><span class="meta">{esc(src)}</span></li>' for c, tag, t, u, src in S["alerts"][:9])
        or '<li><span class="dot d-green"></span>目前沒有觸發重大警示</li>') + "</ul>"
    p13 = '<ul class="list">' + "".join(
        f'<li><b style="color:#fbbf24">{i}.</b> <a href="{esc(r["url"])}" target="_blank">{esc(r["title"][:80])}</a>'
        f'<br><span class="meta">{esc(r["source_name"])}・{"⭐" if r["kind"] == "repo" else "▲" if r["kind"] in ("paper", "discussion") else "❤"}'
        f' {int(r["score"]):,}・熱度 {r["hot"]:.0f}</span></li>' for i, r in enumerate(S["top"], 1)) + "</ul>"
    p14 = ('<ul class="list">' + "".join(
        f'<li><a href="{esc(e.get("link"))}" target="_blank"><b>{esc(e["name"])}</b></a>'
        f'<span style="float:right" class="{"up" if 0 <= e["days"] <= 30 else ""}">{"進行中" if e["days"] < 0 else str(e["days"]) + " 天後"}</span>'
        f'<br><span class="meta">{esc(e["start"])}{" ~ " + esc(e["end"]) if e.get("end") and e["end"] != e["start"] else ""}'
        f'・{esc(e.get("place"))}・{esc(e.get("source"))}</span></li>' for e in events[:7]) + "</ul>"
        + ('<div class="small" style="margin-top:8px"><b>投稿截止（90 天內）</b></div><ul class="list">' + "".join(
            f'<li>{esc(d["name"])}<span style="float:right" class="up">{d["date"]}（{d["days"]} 天）</span></li>' for d in dls[:5])
           + "</ul>" if dls else ""))
    p15 = (f'<div class="small" style="margin-bottom:6px">正常 {ok} / {len(status)}　滑鼠移到失敗來源可看錯誤訊息</div>'
           '<div class="health">' + "".join(
               f'<div title="{esc(s.get("error", "")) or "最後成功：" + esc(s.get("last_ok", ""))}">'
               f'<span class="dot d-{"green" if s.get("ok") else "red"}"></span>{esc(s["name"])}'
               f'<b>{s.get("new", 0) if s.get("ok") else "✗"}</b></div>'
               for _, s in sorted(status.items(), key=lambda kv: (kv[1].get("ok", False), kv[1]["name"]))) + "</div>")
    p16 = '<ul class="list">' + "".join(
        f'<li><a href="{esc(r["url"])}" target="_blank"><b>{esc(r["title"])}</b></a> <span class="up">⭐ {int(r["score"]):,}</span>'
        f'<br><span class="meta">{esc(r["summary"][:90])}</span> {chips(r["topics"], clf)}</li>' for r in S["gh"]) + "</ul>"
    p17 = ('<div class="small"><b>每日論文（按讚數）</b></div><ul class="list">' + "".join(
        f'<li><a href="{esc(r["url"])}" target="_blank">{esc(r["title"][:80])}</a> <span class="up">▲{int(r["score"])}</span></li>'
        for r in S["hfp"]) + '</ul><div class="small" style="margin-top:6px"><b>熱門模型（本週新進榜）</b></div><ul class="list">' + "".join(
        f'<li><a href="{esc(r["url"])}" target="_blank">{esc(r["title"])}</a> <span class="up">❤ {int(r["score"]):,}</span>'
        f'<span class="meta">　{esc((r["extra"] or {}).get("pipeline", ""))}</span></li>' for r in S["hfm"]) + "</ul>")

    # 18 總評
    tags = []
    if S["kev7"] >= 5:
        tags.append("漏洞利用增加")
    if any(a[1] == "官方發布" for a in S["alerts"]):
        tags.append("大廠發布")
    if any(a[1] == "PQC 標準" for a in S["alerts"]):
        tags.append("PQC 動態")
    if S["badge"] in ("HOT", "RISING"):
        tags.append("整體升溫")
    headline = f"{rising}{'爆量' if S['growth'].get(S['rising'], 0) >= 100 else '升溫'}" if S["rising"] else "資料累積中"
    summary = (f"近 24 小時共偵測 {S['n24']} 則新技術動態（前 7 日日均 {S['n24'] / max(S['heat'], 0.01):.0f} 則，熱度 {S['heat']:.2f} 倍）。"
               f"數量最多的是「{hot}」（{S['topic24'].get(S['hot_topic'], 0)} 則），成長最快的是「{rising}」"
               f"（{S['growth'].get(S['rising'], 0):+.0f}%）。爆量關鍵字：{', '.join(b[0] for b in S['burst'][:5]) or '—'}。"
               f"資安面 7 日內 CISA 新增 {S['kev7']} 個已遭利用漏洞。")
    p18 = (f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
           f'<div><span style="font-size:17px;font-weight:800">本日焦點：</span><span class="sem">{esc(headline)}</span></div>'
           f'<div>{"".join(f"<span class=pill>{esc(t)}</span>" for t in tags)}</div></div>'
           f'<p style="line-height:1.8;margin:8px 0 0">{esc(summary)}</p>')

    # 列表資料（7 天）
    week = df[df["t"] >= now - pd.Timedelta(days=7)]
    feed = [{"t": tpe(r["t"]), "ts": pd.Timestamp(r["t"]).isoformat(), "title": r["title"], "url": r["url"],
             "src": r["source_name"], "kind": KIND_NAME.get(r["kind"], r["kind"]), "topics": r["topics"],
             "score": int(r["score"]), "sum": r["summary"][:200]} for r in week.head(2000).to_dict("records")]
    topic_meta = {k: {"name": t.name, "color": t.color} for k, t in T.items()}

    # 報告
    rep = [f"<h3>AI 與資安新技術監測日報｜{now.tz_convert(TPE):%Y-%m-%d %H:%M}（台北時間）</h3><p>{esc(summary)}</p>",
           "<h3>各領域 24 小時動態</h3><table class='t'><tr><th>領域</th><th class='num'>24h</th><th class='num'>7日日均</th><th class='num'>變化</th></tr>"
           + "".join(f"<tr><td>{esc(T[k].name)}</td><td class='num'>{S['topic24'][k]}</td><td class='num'>{S['topic_avg'][k]:.1f}</td>"
                     f"<td class='num {'up' if S['growth'][k] >= 0 else 'down'}'>{S['growth'][k]:+.0f}%</td></tr>" for k in T) + "</table>"]
    for k, t in T.items():
        sub = week[week["topics"].apply(lambda x: k in x)].head(5)
        if len(sub):
            rep.append(f"<h3>{esc(t.name)}：近期重點</h3><ul class='list'>" + "".join(li(r, clf) for r in sub.to_dict("records")) + "</ul>")
    rep.append("<p class='small'>本頁所有分類均為關鍵字規則比對，可能有誤判或漏判；關鍵字設定見 config/topics.yaml。</p>")

    plotly_src = (f"<script>{plotly.offline.get_plotlyjs()}</script>" if os.environ.get("AIMON_INLINE_JS")
                  else f'<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@{plotly.offline.get_plotlyjs_version()}/plotly.min.js"></script>')
    fig_json = {k: json.loads(f.to_json()) for k, f in F.items()}

    body = f"""
<div class="wrap">
<div class="disc">⚠️ 每小時自動更新｜分類與熱度皆為關鍵字規則統計，非 AI 判讀，僅供追蹤參考。資料來源：arXiv、IACR ePrint、Hugging Face、GitHub、Hacker News、官方部落格、資安新聞、CISA KEV、sec-deadlines、iThome、科技新報。</div>
<div class="top"><div class="title">AI・資安・量子・物聯網　新技術發布監測儀表板<small>台北時間 {now.tz_convert(TPE):%Y-%m-%d %H:%M}</small></div>
<div class="tags"><span class="tag"><i></i>KEYWORD SCAN ACTIVE</span><span class="tag"><i></i>{len(status)} SOURCES</span>
<span class="tag"><i></i>HOURLY UPDATE</span><span class="tag w"><i></i>{len(S["alerts"])} ALERTS</span></div></div>
<div class="kpis">{kpi_html}</div>
<div class="grid r1">
 {panel("01", "每小時新項目", "｜近 72 小時，依領域堆疊", fig_div("hourly"))}
 {panel("02", "焦點判讀", "｜規則引擎", p02)}
 {panel("03", "領域熱度雷達", "｜24h vs 7日日均", fig_div("radar"))}
 {panel("04", "領域 × 日期熱圖", "｜14 天", fig_div("heat"))}
 {panel("05", "來源類型分布", "｜7 天", p05)}
</div>
<div class="grid r2">
 {panel("06", "領域成長動能", "｜24h 相對 7日日均", fig_div("growth"))}
 {panel("07", "爆量關鍵字", "｜24h 突增分數", fig_div("burst"))}
 {panel("08", "機構提及排行", "｜7 天 vs 24 小時", fig_div("orgs"))}
 {panel("09", "資安威脅指標", "｜CISA KEV", p09)}
</div>
<div class="grid r3">
 {panel("10", "密碼學・量子密碼", "｜7 天子題", p10)}
 {panel("11", "物聯網・邊緣運算", "｜7 天子題", p11)}
 {panel("12", "重大發布警示", "｜規則觸發", p12)}
 {panel("13", "熱門排行", "｜7 天，依互動數", p13)}
 {panel("14", "資安會議與活動", "｜即將舉行", p14)}
 {panel("15", "來源健康狀態", "｜本次執行", p15)}
</div>
<div class="grid r4">
 {panel("16", "GitHub 新興專案", "｜近 7 天建立", p16)}
 {panel("17", "Hugging Face 熱門", "", p17)}
 {panel("18", "本日焦點總評", "｜規則語意", p18)}
</div>
<div class="tabs"><button class="on" data-t="feed">📋 最新發布列表</button><button data-t="report">📰 每日報告</button><button data-t="dl">⬇ 資料下載</button></div>
<div class="tab on" id="tab-feed"><div class="filters" id="filters"></div><ul class="list feed" id="feed"></ul></div>
<div class="tab report" id="tab-report">{"".join(rep)}</div>
<div class="tab" id="tab-dl"><p><a href="items.csv" download>items.csv</a>（近 30 天，Excel 可開）｜<a href="items.json" download>items.json</a>｜<a href="status.json">status.json</a>（來源狀態）</p>
<p class="small">原始資料累積在 GitHub 倉庫的 data/items/ 資料夾（每月一個 jsonl 檔）。</p></div>
</div>
{plotly_src}
<script>
const FIGS={json.dumps(fig_json, ensure_ascii=False)};
const FEED={json.dumps(feed, ensure_ascii=False)};
const TOPICS={json.dumps(topic_meta, ensure_ascii=False)};
for(const [k,f] of Object.entries(FIGS)){{const el=document.getElementById('fig_'+k);if(el&&window.Plotly)Plotly.newPlot(el,f.data,f.layout,{{displaylogo:false,responsive:true,modeBarButtonsToRemove:['lasso2d','select2d']}});}}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('.tabs button,.tab').forEach(x=>x.classList.remove('on'));b.classList.add('on');document.getElementById('tab-'+b.dataset.t).classList.add('on');}});
let cur='all',q='',kind='';
const fl=document.getElementById('filters');
fl.innerHTML='<button data-k="all" class="on">全部</button>'+Object.entries(TOPICS).map(([k,t])=>`<button data-k="${{k}}" style="border-color:${{t.color}}66">${{t.name}}</button>`).join('')
 +'<select id="kind"><option value="">所有類型</option>'+[...new Set(FEED.map(f=>f.kind))].map(k=>`<option>${{k}}</option>`).join('')+'</select><input id="q" placeholder="搜尋標題…" size="18">';
fl.querySelectorAll('button').forEach(b=>b.onclick=()=>{{fl.querySelectorAll('button').forEach(x=>x.classList.remove('on'));b.classList.add('on');cur=b.dataset.k;draw();}});
document.getElementById('q').oninput=e=>{{q=e.target.value.toLowerCase();draw();}};
document.getElementById('kind').onchange=e=>{{kind=e.target.value;draw();}};
const esc=s=>String(s||'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
function draw(){{const rows=FEED.filter(f=>(cur==='all'||f.topics.includes(cur))&&(!kind||f.kind===kind)&&(!q||f.title.toLowerCase().includes(q))).slice(0,300);
document.getElementById('feed').innerHTML=rows.map(f=>`<li><span class="meta">${{f.t}}<br>${{esc(f.kind)}}</span><div><a href="${{esc(f.url)}}" target="_blank" rel="noopener">${{esc(f.title)}}</a> ${{f.score?'<span class="up">'+f.score.toLocaleString()+'</span>':''}}<br><span class="meta">${{esc(f.src)}}　${{esc(f.sum)}}</span><br>${{f.topics.map(t=>`<span class="chip" style="color:${{TOPICS[t].color}};border-color:${{TOPICS[t].color}}55">${{TOPICS[t].name}}</span>`).join('')}}</div></li>`).join('')||'<li>沒有符合的項目</li>';}}
draw();
setTimeout(()=>location.reload(),30*60*1000);
</script>"""
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>新技術發布監測儀表板</title><link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📡</text></svg>">
<style>{CSS}</style></head><body>{body}</body></html>"""


def main():
    clf = Classifier()
    df = load(clf)
    SITE.mkdir(parents=True, exist_ok=True)
    status = store.read_json("status.json", {})
    runs = []
    rp = store.DATA / "runs.jsonl"
    if rp.exists():
        runs = [json.loads(x) for x in rp.read_text(encoding="utf-8").splitlines() if x.strip()]
    if df.empty:
        (SITE / "index.html").write_text("<meta charset=utf-8><h2>尚無資料，請先執行 collect.py</h2>", encoding="utf-8")
        print("尚無資料")
        return
    now = pd.Timestamp(runs[-1]["t"]) if runs else pd.Timestamp.now(tz="UTC")
    S = compute(df, clf, now)
    F = figs(df, S, clf)
    events, dls = load_events(now)
    (SITE / "index.html").write_text(render(df, S, F, clf, events, dls, status, runs), encoding="utf-8")
    m30 = df[df["t"] >= now - pd.Timedelta(days=30)].copy()
    m30["topics_name"] = m30["topics"].apply(lambda t: "、".join(clf.topics[k].name for k in t))
    m30["time_tpe"] = m30["t"].dt.tz_convert(TPE).dt.strftime("%Y-%m-%d %H:%M")
    m30[["time_tpe", "kind", "source_name", "title", "url", "topics_name", "score", "summary"]] \
        .to_csv(SITE / "items.csv", index=False, encoding="utf-8-sig")
    out = m30.drop(columns=["t", "first_seen"]).assign(t=m30["t"].astype(str))
    (SITE / "items.json").write_text(out.to_json(orient="records", force_ascii=False), encoding="utf-8")
    (SITE / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已產生 {SITE / 'index.html'}（{len(df)} 筆資料）")


if __name__ == "__main__":
    main()
