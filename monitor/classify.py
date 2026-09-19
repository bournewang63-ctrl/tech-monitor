"""關鍵字分類：主題、機構、命中關鍵字。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CJK = re.compile(r"[㐀-鿿]")


def _pattern(kw: str) -> tuple[re.Pattern, str]:
    """回傳 (regex, 顯示用關鍵字)。"""
    case = kw.startswith("=")
    word = kw[1:] if case else kw
    if CJK.search(word):
        return re.compile(re.escape(word)), word
    esc = re.escape(word)
    lead = r"(?<![A-Za-z0-9])" if word[0].isalnum() else ""
    tail = r"(?:s|es)?(?![A-Za-z0-9])" if word[-1].isalnum() else ""
    return re.compile(lead + esc + tail, 0 if case else re.IGNORECASE), word


@dataclass
class Topic:
    key: str
    name: str
    short: str
    color: str
    strong: list
    weak: list
    requires: list


class Classifier:
    def __init__(self, topics_path=ROOT / "config/topics.yaml", orgs_path=ROOT / "config/orgs.yaml"):
        cfg = yaml.safe_load(Path(topics_path).read_text(encoding="utf-8"))["topics"]
        self.topics: dict[str, Topic] = {}
        for k, t in cfg.items():
            self.topics[k] = Topic(k, t["name"], t.get("short", t["name"]), t.get("color", "#94a3b8"),
                                   [_pattern(x) for x in t.get("keywords", [])],
                                   [_pattern(x) for x in t.get("weak", [])], t.get("requires", []))
        orgs = yaml.safe_load(Path(orgs_path).read_text(encoding="utf-8")) or {}
        self.orgs = {name: [_pattern(a)[0] for a in aliases] for name, aliases in orgs.items()}

    def classify(self, text: str) -> tuple[list[str], list[str]]:
        """回傳 (主題 key 清單, 命中關鍵字清單)。"""
        hits, kws = set(), []
        pending = []
        for k, t in self.topics.items():
            found = [disp for rx, disp in t.strong if rx.search(text)]
            if found:
                hits.add(k)
                kws += found
            elif t.weak:
                wf = [disp for rx, disp in t.weak if rx.search(text)]
                if wf:
                    pending.append((k, t, wf))
        for k, t, wf in pending:
            if any(r in hits for r in t.requires):
                hits.add(k)
                kws += wf
        # 有更具體的 AI 主題時，不重複標「AI 技術」
        specific_ai = {"llm", "agent", "multimodal", "robotics", "ai_chip"}
        if "ai_general" in hits and hits & specific_ai:
            hits.discard("ai_general")
        order = list(self.topics)
        return sorted(hits, key=order.index), list(dict.fromkeys(kws))

    def orgs_in(self, text: str) -> list[str]:
        return [name for name, pats in self.orgs.items() if any(p.search(text) for p in pats)]
