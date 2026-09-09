#!/usr/bin/env python3
"""l3pm · 프리마켓 복원 검증 + L2 문턱 사다리"""
import datetime as dt, json
import numpy as np, pandas as pd, yfinance as yf
from combsim import load_1m
import l3full as F

LIVE = {"2026-08-14": 0.685, "2026-08-18": 0.025, "2026-08-19": 0.814,
        "2026-08-20": 0.268, "2026-08-21": 0.749, "2026-08-24": 0.426,
        "2026-08-25": 0.65, "2026-08-26": 0.31, "2026-09-09": 0.393}


def main():
    pmm = F.premarket_pos()
    out = ["[1] 복원 검증 — 라이브 실측 vs 백테스트 복원", "   날짜        라이브   복원    차이"]
    errs = []
    for k, v in sorted(LIVE.items()):
        d = dt.date.fromisoformat(k)
        r = pmm.get(d)
        if r is None:
            out.append(f"   {k}   {v:.3f}    —     (복원 실패)")
            continue
        errs.append(abs(r - v))
        out.append(f"   {k}   {v:.3f}  {r:.3f}   {r-v:+.3f}")
    if errs:
        out.append(f"   평균 절대오차 {np.mean(errs):.3f} · 최대 {max(errs):.3f} · "
                   f"{'일치' if np.mean(errs) < 0.05 else '괴리 있음'}")
    out.append("")

    df = load_1m()
    days = sorted(set(df.index.date))
    try:
        a = F.tzless(yf.Ticker("^VIX9D").history(period="5y")[["Close"]].dropna())
        b = F.tzless(yf.Ticker("^VIX3M").history(period="5y")[["Close"]].dropna())
        r = (a["Close"] / b.reindex(a.index)["Close"]).dropna()
        pc = r.rolling(252).apply(lambda x: (x[-1] >= x).mean() * 100, raw=True)
        l1 = {k.date(): float(v) for k, v in pc.dropna().items()}
    except Exception:
        l1 = {}
    try:
        vx = F.tzless(yf.Ticker("^VXN").history(period="5y")[["Open"]].dropna())
        ivm = {k.date(): float(v) / 100 for k, v in vx["Open"].items()}
    except Exception:
        ivm = {}
    v = F.tzless(yf.Ticker("^VIX").history(period="5y")[["Open"]].dropna())
    vopen = {k.date(): float(x) for k, x in v["Open"].items()}

    base = F.run(df, days, l1, ivm, vopen, pmm, False)      # L2 미적용 전체
    bymap = {t["d"]: t for t in base}
    out.append("[2] L2(프리마켓 위치) 문턱 사다리 — 같은 진입군을 PM으로 갈라보기")
    out.append("   문턱          거래   승률    평균     PF   상위2외")
    for lo, hi, lab in ((-9, 0.3, "PM<0.3 (약세)"), (0.3, 0.5, "0.3~0.5"),
                        (0.5, 0.7, "0.5~0.7"), (0.7, 9, "PM>0.7 (강세)"),
                        (0.5, 9, "PM>0.5 [현행]"), (0.7, 9, "PM>0.7"),
                        (0.8, 9, "PM>0.8"), (-9, 9, "전체(게이트 없음)")):
        sel = [t for t in base if (t["pm"] is not None and lo < t["pm"] <= hi)] if lab != "전체(게이트 없음)" else base
        if not sel:
            out.append(f"   {lab:16s}  0건")
            continue
        pl = [t["pl"] for t in sel]
        w = [x for x in pl if x > 0]
        l = [-x for x in pl if x <= 0]
        pf = sum(w) / sum(l) if l else 99.9
        bb = sorted(pl)[:-2] if len(pl) > 2 else pl
        w2 = [x for x in bb if x > 0]
        l2 = [-x for x in bb if x <= 0]
        pf2 = sum(w2) / sum(l2) if l2 else 99.9
        out.append(f"   {lab:16s} {len(pl):3d}건  {len(w)/len(pl)*100:4.1f}%  "
                   f"{np.mean(pl):+6.1f}%  {pf:4.2f}   {pf2:4.2f}")
    cov = sum(1 for t in base if t["pm"] is not None)
    out.append(f"   (진입일 {len(base)}건 중 프리마켓 복원된 건 {cov}건)")
    return out


if __name__ == "__main__":
    rep = main()
    print("\n".join(rep))
    json.dump({"at": dt.datetime.utcnow().isoformat(), "report": "\n".join(rep)},
              open("l3pm_result.json", "w"), ensure_ascii=False, indent=1)
