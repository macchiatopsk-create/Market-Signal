"""vbsweep — vectorbt 2단 스윕 파이프라인 실전 가동.

1단 (vectorbt, 기초자산): 모멘텀 다리 격자 — 커버문턱 5 × 트레일 12 = 60조합을
   한 번의 벡터 연산으로. 진입 = 스킵데이+VIX확인 09:45 (롱/숏 = 갭 방향),
   청산 = 트레일(내장) + OR손절 근사(봉 저가 터치 시 그 봉 종가) + 14:00 컷.
2단 (하네스, BSM 옵션): 1단 상위 5조합만 combsim의 정밀 로직(mom_A/mom_D +
   BSM 세타)으로 재검 — PF·상위2제외·평균.
검증: 1단 순위 ↔ 2단 PF 정합 여부 = 파이프라인 신뢰도.
"""
import json, time, glob, datetime as dt, traceback
import numpy as np
import pandas as pd
import yfinance as yf

import combsim as C

COVS = [0.30, 0.35, 0.40, 0.45, 0.50]
TRAILS = [round(0.05 * i, 2) for i in range(1, 13)]          # 0.05~0.60%
T_SIG, T_FINAL = dt.time(9, 45), dt.time(14, 0)


def build_universe():
    v = C.norm(yf.Ticker("^VIX").history(period="8mo")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {pd.Timestamp(k).date(): float(x) for k, x in ch.dropna().items()}
    try:
        x = C.norm(yf.Ticker("^VXN").history(period="8mo")[["Open"]].dropna())
        ivm = {str(pd.Timestamp(k).date()): float(r) / 100 for k, r in x["Open"].items()}
    except Exception:
        ivm = {}
    vox = {str(pd.Timestamp(k).date()): float(r) for k, r in v["Open"].items()}
    dd = C.norm(yf.download("QQQ", period="8mo", interval="1d",
                            auto_adjust=False, progress=False))
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    closes = {pd.Timestamp(k).date(): float(r) for k, r in dd["Close"].items()}
    dl = sorted(closes.keys())
    prevc = {dl[i]: closes[dl[i - 1]] for i in range(1, len(dl))}
    df = C.load_1m()
    days = sorted(set(df.index.date))
    D = {}
    for d in days:
        pc, vx = prevc.get(d), vm.get(d)
        if pc is None or vx is None or abs(vx) >= 5.0:
            continue
        g = df[df.index.date == d]
        if len(g) < 20:
            continue
        O0 = float(g["Open"].iloc[0])
        gap = O0 - pc
        gp = gap / pc * 100
        if not (0.2 <= abs(gp) < 1.5):
            continue
        sgn = 1 if gap > 0 else -1
        ep = float(g["Close"].iloc[14])
        cov = ((O0 - ep) / gap) if sgn > 0 else ((ep - O0) / abs(gap))
        conf = (sgn > 0 and vx < 0) or (sgn < 0 and vx > 0)
        if not conf:
            continue
        iv = (ivm.get(str(d)) or vox.get(str(d), 16.0) * 1.15 / 100) * C.K_IV
        b1 = [(t, float(r["High"]), float(r["Low"]), float(r["Close"]))
              for t, r in g.iterrows()]
        D[d] = dict(sgn=sgn, cov=cov, ep=ep, iv=iv,
                    or_hi=max(x[1] for x in b1[:5]), or_lo=min(x[2] for x in b1[:5]),
                    b1=b1)
    return df, D


def stage1(df, D):
    import vectorbt as vbt
    close = df["Close"].astype(float)
    high, low = df["High"].astype(float), df["Low"].astype(float)
    idx = close.index
    tt = pd.Series(idx.time, index=idx)
    dser = pd.Series(idx.date, index=idx)
    stopmap = pd.Series(np.nan, index=idx)
    for d, s in D.items():
        stopmap[dser == d] = s["or_lo"] if s["sgn"] > 0 else s["or_hi"]
    sgnmap = pd.Series(0, index=idx)
    for d, s in D.items():
        sgnmap[dser == d] = s["sgn"]
    base_exit = (tt >= T_FINAL)
    or_hit = ((sgnmap > 0) & (low <= stopmap)) | ((sgnmap < 0) & (high >= stopmap))
    exits1 = (base_exit | or_hit)
    nC, nT = len(COVS), len(TRAILS)
    ncol = nC * nT
    ent_l = np.zeros((len(idx), ncol), bool)
    ent_s = np.zeros((len(idx), ncol), bool)
    at945 = (tt == T_SIG).values
    for ci, cmax in enumerate(COVS):
        ml = np.zeros(len(idx), bool)
        ms = np.zeros(len(idx), bool)
        for d, s in D.items():
            if s["cov"] < cmax:
                m = (dser == d).values & at945
                (ml if s["sgn"] > 0 else ms)[m] = True
        for ti in range(nT):
            ent_l[:, ci * nT + ti] = ml
            ent_s[:, ci * nT + ti] = ms
    cols = [f"cov<{c:.2f}|tr{t:.2f}" for c in COVS for t in TRAILS]
    sl = np.array([[t / 100 for c in COVS for t in TRAILS]])
    t0 = time.time()
    pf = vbt.Portfolio.from_signals(
        close, pd.DataFrame(ent_l, index=idx, columns=cols),
        pd.DataFrame(np.tile(exits1.values.reshape(-1, 1), (1, ncol)),
                     index=idx, columns=cols),
        short_entries=pd.DataFrame(ent_s, index=idx, columns=cols),
        short_exits=pd.DataFrame(np.tile(exits1.values.reshape(-1, 1), (1, ncol)),
                                 index=idx, columns=cols),
        high=high, low=low, sl_stop=sl, sl_trail=True,
        freq="1min", init_cash=10000)
    el = time.time() - t0
    tr = pf.total_return() * 100
    cnts = pf.trades.count()
    tab = pd.DataFrame(dict(ret=tr.values, n=cnts.values), index=cols)
    return tab.sort_values("ret", ascending=False), el, ncol


def stage2(D, picks):
    rows = []
    for (cmax, trail) in picks:
        res = {"A": [], "D": []}
        for d, s in D.items():
            if s["cov"] >= cmax:
                continue
            seqA = s["b1"][15:]
            seqD = C.agg(s["b1"][15:], 5)
            stop = s["or_lo"] if s["sgn"] > 0 else s["or_hi"]
            flag = "c" if s["sgn"] > 0 else "p"
            t0 = s["b1"][14][0].time()
            for m, run, sq in [("A", C.mom_A, seqA), ("D", C.mom_D, seqD)]:
                px, hd = run(sq, t0, s["ep"], s["sgn"], stop, trail)
                o = C.bsm_net(flag, s["ep"], px, t0, hd, s["iv"])
                if o is not None:
                    res[m].append(o)
        r = dict(cmax=cmax, trail=trail)
        for m in ("A", "D"):
            op = res[m]
            n = len(op)
            g = sum(x for x in op if x > 0)
            l = -sum(x for x in op if x <= 0)
            pf = 99.0 if l <= 0 else g / l
            srt = sorted(op, reverse=True)
            o2 = srt[2:] if n > 4 else srt
            g2 = sum(x for x in o2 if x > 0)
            l2 = -sum(x for x in o2 if x <= 0)
            pf2 = 99.0 if l2 <= 0 else g2 / l2
            r[m] = (n, pf, pf2, float(np.mean(op)) if n else 0.0)
        rows.append(r)
    return rows


def main():
    df, D = build_universe()
    out = [f"vbsweep · 2단 파이프라인 가동 증명 · VIX확인 스킵후보 {len(D)}일 (커버 문턱 이전)"]
    tab, el, ncol = stage1(df, D)
    out.append(f"[1단 vectorbt] {ncol}조합 (커버 {len(COVS)} × 트레일 {len(TRAILS)}) → {el:.1f}초")
    out.append("  상위 8 (기초자산 총수익):")
    for c, r in tab.head(8).iterrows():
        out.append(f"    {c:<18s} {r['ret']:+6.2f}%  n={int(r['n'])}")
    out.append("  하위 3: " + " · ".join(f"{c} {r['ret']:+.2f}%"
                                       for c, r in tab.tail(3).iterrows()))
    picks = []
    for c in tab.head(5).index:
        cv = float(c.split("|")[0].replace("cov<", ""))
        tl = float(c.split("tr")[1])
        picks.append((cv, tl))
    out.append("")
    out.append("[2단 하네스 BSM 정밀] 1단 상위 5조합:")
    out.append("   조합              A모드 n/PF/상위2외/평균      D모드 n/PF/상위2외/평균")
    for r in stage2(D, picks):
        a, dd_ = r["A"], r["D"]
        out.append(f"   cov<{r['cmax']:.2f} tr{r['trail']:.2f}   "
                   f"{a[0]:2d} {a[1]:5.2f} {a[2]:5.2f} {a[3]:+6.1f}%    "
                   f"{dd_[0]:2d} {dd_[1]:5.2f} {dd_[2]:5.2f} {dd_[3]:+6.1f}%")
    out.append("")
    out.append("검증 포인트 — 1단(기초자산) 순위가 2단(옵션 BSM) PF와 대체로 정합하면")
    out.append("파이프라인 유효: 275일 재심 때 1단으로 수백 조합 걸러서 2단 정밀 재검.")
    out.append(f"※ 참고 기준: 기존 orbvix 스펙 = cov<0.40 tr0.30 (A 3.08/1.90, D 2.28/1.49)")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("vbsweep_result.json", "w"), ensure_ascii=False, indent=1)
