"""
v8 — 3층 구조 전략

  1층 크기 (전날 종가 확정)  : VIX9D/VIX3M 백분위. 낮으면 그날 아예 안 침
  2층 방향 (09:30 확정)      : 프리마켓 위치/모멘텀 -> 그날 편향
  3층 타이밍 (장중)          : VWAP 밴드

진입
  롱 : 편향=롱 이고 VWAP -1σ 터치
  숏 : 편향=숏 이고 +1σ 도달 후, 당일 고점을 FAIL_N 회 이상 못 뚫으면
       (= 오늘 고점이 확정됐다는 인식) 진입

청산 (분할)
  TP1 : 중간선(VWAP) 도달 -> 물량 50% 청산
  러너: 나머지 50%는 반대편 밴드까지
  손절: 롱=당일 저점 이탈 / 숏=당일 고점 돌파  (인식이 깨지는 지점)
  시간: 15:45 강제청산

주: 손절 기준과 '5회 실패' 카운트 방식은 구현 해석. 변형으로 같이 측정한다.
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

CUT_ENTRY = dt.time(14, 0)
EXIT_TIME = dt.time(15, 45)
NEAR = 0.001          # 당일 고점 '근처' 판정 (0.1%)


def wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k / n, 1.96; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def vix_pct_map():
    def _n(x):
        try: x.index = x.index.tz_localize(None)
        except (TypeError, AttributeError): pass
        x.index = pd.to_datetime(x.index).normalize()
        return x[~x.index.duplicated(keep="last")]
    a = _n(yf.Ticker("^VIX9D").history(period="2y")["Close"].dropna())
    b = _n(yf.Ticker("^VIX3M").history(period="2y")["Close"].dropna())
    ts = (a / b.reindex(a.index).ffill()).dropna()
    def _p(w):
        if len(w) < 2: return float("nan")
        return float((w[:-1] < w[-1]).sum()) / (len(w) - 1) * 100
    pct = ts.rolling(252).apply(_p, raw=True).shift(1)   # 전날 값 사용
    return {str(d.date()): float(v) for d, v in pct.dropna().items()}


def run(tk, vix_min=0.0, pm_mode="position", fail_n=5, sigma=1.0, vmap=None):
    df = yf.download(tk, period="60d", interval="5m", prepost=True,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    try: df.index = df.index.tz_convert("America/New_York")
    except Exception: pass

    trades = []
    for d in sorted(set(df.index.date)):
        ds = str(d)
        if vix_min > 0:
            v = (vmap or {}).get(ds)
            if v is None or v < vix_min: continue

        g = df[df.index.date == d]
        pm = g[(g.index.time >= dt.time(4, 0)) & (g.index.time < dt.time(9, 30))]
        rt = g[(g.index.time >= dt.time(9, 30)) & (g.index.time < dt.time(16, 0))]
        if len(pm) < 6 or len(rt) < 60: continue

        # ── 2층: 방향 ──
        pmh, pml = float(pm["High"].max()), float(pm["Low"].min())
        op = float(rt["Open"].iloc[0])
        if pm_mode == "position":
            if pmh <= pml: continue
            pos = (op - pml) / (pmh - pml)
            bias = 1 if pos > 0.5 else -1
        else:  # momentum: 프리마켓 첫 종가 -> 09:30 시가 방향
            first = float(pm["Close"].iloc[0])
            bias = 1 if op > first else -1
            pos = (op / first - 1) * 100

        H = [float(x) for x in rt["High"]]; L = [float(x) for x in rt["Low"]]
        C = [float(x) for x in rt["Close"]]; O = [float(x) for x in rt["Open"]]
        V = [float(x) for x in rt["Volume"]]; T = [t.time() for t in rt.index]
        n = len(C)

        cpv = cv = cpv2 = 0.0; vwap = []; sd = []
        for i in range(n):
            tp = (H[i] + L[i] + C[i]) / 3
            cpv += tp * V[i]; cv += V[i]; cpv2 += tp * tp * V[i]
            w = cpv / cv if cv else tp
            vwap.append(w); sd.append(math.sqrt(max(cpv2 / cv - w * w, 0.0)) if cv else 0.0)

        day_hi = day_lo = None; fails = 0; touched_up = False
        pos_open = None; half_done = False

        for i in range(6, n):                     # 10:00 이후
            w, s = vwap[i], sd[i]
            if s <= 1e-9: continue
            up, lo = w + sigma * s, w - sigma * s
            prev_hi = day_hi
            day_hi = H[i] if day_hi is None else max(day_hi, H[i])
            day_lo = L[i] if day_lo is None else min(day_lo, L[i])

            # 당일 고점 근처까지 갔다가 갱신 실패 카운트
            if prev_hi is not None and H[i] >= prev_hi * (1 - NEAR) and H[i] < prev_hi:
                fails += 1
            if H[i] >= up: touched_up = True

            # ── 보유 중 ──
            if pos_open:
                sgn = pos_open["dir"]; ep = pos_open["ep"]
                if not half_done:
                    hit_mid = (H[i] >= w) if sgn > 0 else (L[i] <= w)
                    if hit_mid:
                        pos_open["tp1"] = w; half_done = True
                runner_tgt = up if sgn > 0 else lo
                hit_run = (H[i] >= runner_tgt) if sgn > 0 else (L[i] <= runner_tgt)
                stop_px = pos_open["stop"]
                hit_stop = (L[i] <= stop_px) if sgn > 0 else (H[i] >= stop_px)
                reason = px2 = None
                if hit_stop:      reason, px2 = ("STOP" if not half_done else "STOP_AFTER_TP1"), stop_px
                elif hit_run:     reason, px2 = "RUNNER", runner_tgt
                elif T[i] >= EXIT_TIME: reason, px2 = "TIME", O[i]
                if reason:
                    if half_done:
                        p1 = ((pos_open["tp1"] / ep - 1) * 100) * sgn
                        p2 = ((px2 / ep - 1) * 100) * sgn
                        pnl = 0.5 * p1 + 0.5 * p2
                    else:
                        pnl = ((px2 / ep - 1) * 100) * sgn
                    trades.append(dict(d=ds, dir=sgn, reason=reason, half=half_done,
                                       pnl=round(pnl, 4), bars=i - pos_open["i"],
                                       pos=round(pos, 3)))
                    pos_open = None; half_done = False
                    break                          # 하루 1회
                continue

            if T[i] >= CUT_ENTRY: break

            # ── 진입 ──
            if bias > 0 and L[i] <= lo:
                pos_open = dict(dir=1, ep=lo, i=i, stop=day_lo * (1 - 0.0005), tp1=None)
            elif bias < 0 and touched_up and fails >= fail_n and H[i] >= up:
                pos_open = dict(dir=-1, ep=up, i=i, stop=day_hi * (1 + 0.0005), tp1=None)

        if pos_open:
            sgn = pos_open["dir"]; ep = pos_open["ep"]
            pnl = ((C[-1] / ep - 1) * 100) * sgn
            if half_done:
                pnl = 0.5 * (((pos_open["tp1"] / ep - 1) * 100) * sgn) + 0.5 * pnl
            trades.append(dict(d=ds, dir=sgn, reason="EOD", half=half_done,
                               pnl=round(pnl, 4), bars=n - 1 - pos_open["i"], pos=round(pos, 3)))
    return trades


def rep(tr, lab, out):
    n = len(tr)
    if n < 8:
        out.append(f"  {lab:34s} n={n:3d} 표본부족"); return
    w = sum(1 for t in tr if t["pnl"] > 0); ci = wilson(w, n)
    g = sum(t["pnl"] for t in tr if t["pnl"] > 0); l = -sum(t["pnl"] for t in tr if t["pnl"] <= 0)
    s2 = sorted(tr, key=lambda x: -x["pnl"])[2:]
    g2 = sum(t["pnl"] for t in s2 if t["pnl"] > 0); l2 = -sum(t["pnl"] for t in s2 if t["pnl"] <= 0)
    ds = sorted(set(t["d"] for t in tr)); half = ds[len(ds)//2]
    def _pf(x):
        a = sum(t["pnl"] for t in x if t["pnl"] > 0); b = -sum(t["pnl"] for t in x if t["pnl"] <= 0)
        return (a/b) if b > 0 else 99.0
    rc = {}
    for t in tr: rc[t["reason"]] = rc.get(t["reason"], 0) + 1
    nl = sum(1 for t in tr if t["dir"] > 0); ns = n - nl
    out.append(f"  {lab:34s} n={n:3d}/{len(ds):2d}일(롱{nl}/숏{ns}) 승률 {w/n*100:5.1f}% "
               f"CI({ci[0]:4.1f}~{ci[1]:4.1f}) PF {g/l if l else 99:5.2f} "
               f"|상위2제외 {g2/l2 if l2 else 99:5.2f} 합계 {sum(t['pnl'] for t in tr):+6.2f}% "
               f"| 반반 {_pf([t for t in tr if t['d']<half]):.2f}/{_pf([t for t in tr if t['d']>=half]):.2f} "
               f"| {'/'.join(f'{k}{v}' for k,v in sorted(rc.items()))}")


def main():
    out = []
    vmap = vix_pct_map()
    out.append(f"VIX 백분위 맵 {len(vmap)}일")
    for tk in ("SPY", "QQQ"):
        out.append(f"\n{'='*112}\n[{tk}] 3층: VIX(크기) + 프리마켓(방향) + VWAP(타이밍) · TP1 중간선 절반 + 러너\n{'='*112}")
        for vmin, vlab in ((0, "VIX필터 없음"), (50, "VIX≥50%"), (80, "VIX≥80%")):
            for mode in ("position", "momentum"):
                rep(run(tk, vix_min=vmin, pm_mode=mode, vmap=vmap),
                    f"{vlab} · 프리마켓 {mode}", out)
        out.append("  --- 고점 실패 횟수 변형 (VIX≥50, position) ---")
        for fn in (0, 3, 5, 8):
            rep(run(tk, vix_min=50, pm_mode="position", fail_n=fn, vmap=vmap),
                f"고점 돌파실패 {fn}회 이상", out)
    return out


if __name__ == "__main__":
    try: r = main()
    except Exception: r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r); print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("v8_result.json", "w"), ensure_ascii=False, indent=1)
