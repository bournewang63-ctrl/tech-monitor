"""語意相似度層（選用）：用開源嵌入模型補關鍵字漏掉的項目。

只處理「關鍵字完全沒分到類」的項目，不更動關鍵字已判定的結果，所以最壞情況是沒有效果，
不會把原本正確的分類弄亂。模型下載或執行失敗時直接跳過，收集流程照常完成。

環境變數：
  AIMON_SEMANTIC=1        開啟（GitHub Actions 已設定）
  AIMON_SEM_MIN=0.42      相似度門檻（越高越保守）
  AIMON_SEM_Z=1.5         領先幅度門檻：最高分要比其他領域平均高出幾個標準差
  AIMON_SEM_DEBUG=1       印出分數分布，用來調門檻
"""
from __future__ import annotations

import os

MODEL = os.environ.get("AIMON_SEM_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
SIM_MIN = float(os.environ.get("AIMON_SEM_MIN", 0.42))
Z_MIN = float(os.environ.get("AIMON_SEM_Z", 1.5))
MAX_ITEMS = int(os.environ.get("AIMON_SEM_MAX", 400))  # 單次執行上限，避免拖太久
# 這些領域只靠精確名稱判斷，交給語意猜容易誤判（例如論文標題被當成研討會）
EXCLUDE = {"security_events"}


def enabled() -> bool:
    return os.environ.get("AIMON_SEMANTIC") == "1"


def _anchor_text(topic) -> str:
    """每個領域的代表文字：中文名稱 + 前 18 個英文關鍵字。"""
    words = [w for _, w in topic.strong if w.isascii()][:18]
    return f"{topic.name}. " + ", ".join(words)


def enrich(items: list[dict], clf, debug: bool | None = None) -> dict:
    """就地把語意判定寫進 item['sem']；回傳統計摘要。"""
    debug = os.environ.get("AIMON_SEM_DEBUG") == "1" if debug is None else debug
    cand = [it for it in items if not it.get("topics") and it.get("kind") != "vuln"][:MAX_ITEMS]
    if not cand:
        return {"ok": True, "candidates": 0, "assigned": 0}
    try:
        import numpy as np
        from fastembed import TextEmbedding
    except ImportError as e:
        return {"ok": False, "error": f"套件未安裝：{e}"}
    try:
        model = TextEmbedding(MODEL, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH", ".fastembed_cache"))
        keys = [k for k in clf.topics if k not in EXCLUDE]
        anchors = np.array(list(model.embed([_anchor_text(clf.topics[k]) for k in keys])))
        texts = [f"{it['title']}. {it.get('summary', '')[:200]}" for it in cand]
        vecs = np.array(list(model.embed(texts)))
    except Exception as e:  # 模型下載失敗、記憶體不足等：跳過，不影響收集
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}

    norm = lambda m: m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-9, None)
    sims = norm(vecs) @ norm(anchors).T          # (項目數, 領域數)
    best = sims.argmax(axis=1)
    top = sims.max(axis=1)
    z = (top - sims.mean(axis=1)) / np.clip(sims.std(axis=1), 1e-9, None)

    assigned = 0
    for i, it in enumerate(cand):
        if top[i] >= SIM_MIN and z[i] >= Z_MIN:
            it["sem"] = [keys[best[i]]]
            it["sem_score"] = round(float(top[i]), 3)
            assigned += 1
        else:
            it.pop("sem", None)
            it.pop("sem_score", None)
    if debug:
        order = (-top).argsort()
        print(f"  語意層：候選 {len(cand)}，相似度中位數 {float(np.median(top)):.3f}，"
              f"門檻 {SIM_MIN}/{Z_MIN}，補抓 {assigned}")
        for i in order[:8]:
            print(f"    {top[i]:.3f} z={z[i]:.1f} → {clf.topics[keys[best[i]]].name:<10} {cand[i]['title'][:60]}")
        for i in order[-4:]:
            print(f"    {top[i]:.3f} z={z[i]:.1f} ✗ {clf.topics[keys[best[i]]].name:<10} {cand[i]['title'][:60]}")
    return {"ok": True, "candidates": len(cand), "assigned": assigned,
            "median": round(float(np.median(top)), 3), "model": MODEL}
