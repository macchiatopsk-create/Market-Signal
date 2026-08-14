"""
v10 — 3층(VWAP 타이밍) 검증

전제: 상위 2층은 501일로 확정됨 (QQQ · VIX>=50% · pos>0.5 롱 · PF 1.69).
질문: 그 '통과일'에서 진입/청산을 어떻게 하는 게 나은가?

비교 (같은 날짜 집합, 같은 방향=롱):
  A 09:30 시장가 진입 -> 종가 청산            (2층 검증과 동일 = 벤치마크)
  B VWAP -1σ 터치 대기 -> 종가 청산            (터치 없으면 미진입)
  C VWAP -1σ 터치 -> TP1 중간선 50% + 러너 상단, 손절 당일저점   (형님 설계)
  D 09:30 진입 -> TP1 중간선 50% + 러너 상단, 손절 당일저점      (분할청산만 적용)
  E VWAP -0.5σ 터치 -> C와 동일 청산           (터치 문턱 완화)

주의: 15분봉 60일 표본. 여기서 보는 건 '엣지 유무'가 아니라
      '진입 방식 간 상대 비교'다. 표본이 작으므로 방향만 읽는다.
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

SIG = 1.0


def _n(x):
    try: x.index = x.index.tz_localize(None)
    except (TypeError, AttributeError): pass
    x.index = pd.to_datetime(x.index).normalize()
    return x[~x.index.duplicated(keep="last")]


def _grab(tk, tries=3):
    import time
    for i in range(tries):
        try:
            s = yf.Ticker(tk).history(period="3y")["Close"].dropna()
            if len(s) > 100: return _n(s)
        except Exception: pass
        time.sleep(3)
    return None


def vix_map():
    from v9_test import vix_pct_map, DIAG
    m = vix_pct_map()
    if not m:
        import time
        time.sleep(10)                     # 레이트리밋 회피 후 1회 재시도
        m = vix_pct_map()
    print("\n".join(DIAG))
    return m


def day_frames(tk):
    df = yf.download(tk, period="60d", interval="15m", prepost=True,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df.index = df.index.tz_convert("America/New_York")
    for d in sorted(set(df.index.date)):
        g = df[df.index.date == d]
        pm = g[(g.index.time >= dt.time(4, 0)) & (g.index.time < dt.time(9, 30))]
        rt = g[(g.index.time >= dt.time(9, 30)) & (g.index.time < dt.time(16, 0))]
        if len(pm) < 6 or len(rt) < 20: continue
        yield str(d), pm, rt


def vwap_bands(rt):
    H = rt["High"].values; L = rt["Low"].values; C = rt["Close"].values; V = rt["Volume"].values
    cpv = cv = cpv2 = 0.0; W = []; S = []
    for i in range(len(C)):
        tp = (H[i] + L[i] + C[i]) / 3
        cpv += tp * V[i]; cv += V[i]; cpv2 += tp * tp * V[i]
        w = cpv / cv if cv else tp
        W.append(w); S.append(math.sqrt(max(cpv2 / cv - w * w, 0.0)) if cv else 0.0)
    return W, S


def sim(rt, mode, sig=SIG):
    """통과일 하루를 모드별로 시뮬레이션. 반환 pnl% 또는 None(미진입)."""
    H = rt["High"].values; L = rt["Low"].values; C = rt["Close"].values; O = rt["Open"].values
    T = [t.time() for t in rt.index]
    W, S = vwap_bands(rt)
    n = len(C)

    if mode == "A":
        return (C[-1] / O[0] - 1) * 100

    # 진입 탐색
    ep = ei = None
    if mode == "D":
        ep, ei = O[0], 0
    else:
        th = 0.5 if mode == "E" else 1.0
        for i in range(1, n):                     # 09:45 이후
            if T[i] >= dt.time(14, 0): break
            if S[i] <= 1e-9: continue
            lo_band = W[i] - th * S[i]
            if L[i] <= lo_band:
                ep, ei = lo_band, i; break
        if ep is None:
            return None                            # 터치 없음 -> 미진입

    if mode == "B":
        return (C[-1] / ep - 1) * 100

    # C/D/E: TP1 중간선 50% + 러너 상단, 손절 당일저점
    day_lo = float(min(L[:ei + 1])) if ei > 0 else float(L[0])
    stop = day_lo * (1 - 0.0005)          # 진입 시점 당일저점 고정 (설계 의도)
    half = False; tp1 = None
    for i in range(ei + 1, n):
        w, s = W[i], S[i]
        if not half and H[i] >= w:
            tp1, half = w, True
        run_tgt = w + sig * s
        if L[i] <= stop:
            px = stop
            if half: return 0.5 * ((tp1/ep - 1) * 100) + 0.5 * ((px/ep - 1) * 100)
            return (px / ep - 1) * 100
        if half and H[i] >= run_tgt:
            return 0.5 * ((tp1/ep - 1) * 100) + 0.5 * ((run_tgt/ep - 1) * 100)
        if T[i] >= dt.time(15, 45):
            px = O[i]
            if half: return 0.5 * ((tp1/ep - 1) * 100) + 0.5 * ((px/ep - 1) * 100)
            return (px / ep - 1) * 100
    px = C[-1]
    if half: return 0.5 * ((tp1/ep - 1) * 100) + 0.5 * ((px/ep - 1) * 100)
    return (px / ep - 1) * 100


def main():
    out = []
    vmap = vix_map()
    out.append(f"VIX 맵 {len(vmap)}일")
    tk = "QQQ"
    passed = []
    for ds, pm, rt in day_frames(tk):
        v = vmap.get(ds)
        if v is None or v < 50: continue
        pmh, pml = float(pm["High"].max()), float(pm["Low"].min())
        if pmh <= pml: continue
        op = float(rt["Open"].iloc[0])
        pos = (op - pml) / (pmh - pml)
        if pos <= 0.5: continue
        passed.append((ds, rt))
    out.append(f"[{tk}] 60일 중 상위2층 통과일(롱): {len(passed)}일")
    if len(passed) < 8:
        out.append("표본 부족 — 비교 불가"); return out

    res = {m: [] for m in "ABCDE"}
    detail = []
    for ds, rt in passed:
        row = [ds]
        for m in "ABCDE":
            p = sim(rt, m)
            res[m].append(p)
            row.append("미진입" if p is None else f"{p:+.3f}")
        detail.append(row)

    names = {"A": "A 09:30진입·종가청산 (벤치)", "B": "B -1σ대기·종가청산",
             "C": "C -1σ대기·TP1+러너 (형님설계)", "D": "D 09:30진입·TP1+러너",
             "E": "E -0.5σ대기·TP1+러너"}
    out.append(f"\n  {'모드':30s} {'진입':>4s} {'승률':>6s} {'평균':>8s} {'합계':>8s} {'최악':>8s}")
    for m in "ABCDE":
        v = [x for x in res[m] if x is not None]
        skip = len(res[m]) - len(v)
        if not v:
            out.append(f"  {names[m]:30s} 전부 미진입"); continue
        w = sum(1 for x in v if x > 0)
        out.append(f"  {names[m]:30s} {len(v):3d}일{f'(스킵{skip})' if skip else '    '} "
                   f"{w/len(v)*100:5.1f}% {sum(v)/len(v):+7.3f}% {sum(v):+7.2f}% {min(v):+7.3f}%")

    out.append("\n  일별 상세 (A/B/C/D/E):")
    for row in detail:
        out.append("    " + row[0] + "  " + "  ".join(f"{x:>8s}" for x in row[1:]))
    return out


if __name__ == "__main__":
    try: r = main()
    except Exception: r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r); print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("v10_result.json", "w"), ensure_ascii=False, indent=1)
