"""
갭필 파라미터 스윕.

변경점 (형님 지시)
  - 커버 기준 50% -> 30%
  - 손절을 첫봉 극점보다 넓게: 극점 + 갭크기 배수 / 진입가 대비 고정 % / 손절없음

진입: 첫봉 종가 (갭업이면 숏=풋, 갭다운이면 롱=콜)
TP  : 전날 종가 (갭필)
시간청산: 14:30
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

COVER_MIN = 0.30
CFGS = [("EXT0.00", "extreme", 0.00), ("EXT0.25", "extreme", 0.25),
        ("EXT0.50", "extreme", 0.50), ("EXT1.00", "extreme", 1.00),
        ("FIX0.30%", "fixed", 0.30), ("FIX0.50%", "fixed", 0.50),
        ("FIX0.75%", "fixed", 0.75), ("NOSTOP", "none", 0.0)]


def wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k / n, 1.96; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def build(tk, interval, period):
    df = yf.download(tk, period=period, interval=interval, prepost=False,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df.index = df.index.tz_convert("America/New_York")
    df = df[(df.index.time >= dt.time(9, 30)) & (df.index.time < dt.time(16, 0))]
    need = {"5m": 40, "15m": 15, "1h": 5}[interval]
    cut_bar = {"5m": 60, "15m": 20, "1h": 4}[interval]
    mins = {"5m": 5, "15m": 15, "1h": 60}[interval]

    days = []; prev_close = None
    for d in sorted(set(df.index.date)):
        g = df[df.index.date == d]
        if len(g) < need:
            if len(g): prev_close = float(g["Close"].iloc[-1])
            continue
        O = [float(x) for x in g["Open"]]; H = [float(x) for x in g["High"]]
        L = [float(x) for x in g["Low"]];  C = [float(x) for x in g["Close"]]
        if prev_close:
            gap = O[0] - prev_close
            gp = gap / prev_close * 100
            if abs(gp) >= 0.05:
                sgn = 1 if gap > 0 else -1
                cover = ((O[0] - C[0]) / gap) if sgn > 0 else ((C[0] - O[0]) / abs(gap))
                days.append(dict(d=str(d), sgn=sgn, gp=gp, cover=cover, pc=prev_close,
                                 ep=C[0], ext=(H[0] if sgn > 0 else L[0]),
                                 H=H, L=L, C=C, cut=cut_bar, mins=mins))
        prev_close = C[-1]
    return days


def sim(day, mode, k):
    sgn, ep, tgt = day["sgn"], day["ep"], day["pc"]
    gapabs = abs(day["gp"]) / 100 * day["pc"]
    if mode == "extreme":
        stop = day["ext"] + sgn * k * gapabs        # 갭업(sgn=1)이면 위로
    elif mode == "fixed":
        stop = ep * (1 + sgn * k / 100)
    else:
        stop = None
    H, L, C, cut = day["H"], day["L"], day["C"], day["cut"]
    fill_bar = stop_bar = None; mae = 0.0
    for i in range(1, len(C)):
        adverse = (H[i] - ep) / ep * 100 if sgn > 0 else (ep - L[i]) / ep * 100
        mae = max(mae, adverse)
        if stop is not None and stop_bar is None:
            if (H[i] >= stop) if sgn > 0 else (L[i] <= stop): stop_bar = i
        if fill_bar is None:
            if (L[i] <= tgt) if sgn > 0 else (H[i] >= tgt): fill_bar = i
        if fill_bar is not None or (stop_bar is not None and stop_bar <= cut): break
    if fill_bar is not None and (stop_bar is None or fill_bar <= stop_bar) and fill_bar <= cut:
        return dict(pnl=abs(tgt - ep) / ep * 100, res="FILL", bar=fill_bar, mae=mae)
    if stop_bar is not None and stop_bar <= cut:
        return dict(pnl=-abs(stop - ep) / ep * 100, res="STOP", bar=stop_bar, mae=mae)
    idx = min(cut, len(C) - 1); px = C[idx]
    pnl = ((ep - px) / ep * 100) if sgn > 0 else ((px - ep) / ep * 100)
    return dict(pnl=pnl, res="CUT", bar=idx, mae=mae)


def rep(res, lab, out, mins, ind="    "):
    n = len(res)
    if n < 10:
        out.append(f"{ind}{lab:12s} n={n:3d} 표본부족"); return
    w = sum(1 for r in res if r["pnl"] > 0); ci = wilson(w, n)
    g = sum(r["pnl"] for r in res if r["pnl"] > 0); l = -sum(r["pnl"] for r in res if r["pnl"] <= 0)
    s2 = sorted(res, key=lambda x: -x["pnl"])[2:]
    g2 = sum(r["pnl"] for r in s2 if r["pnl"] > 0); l2 = -sum(r["pnl"] for r in s2 if r["pnl"] <= 0)
    ds = sorted(r["d"] for r in res); half = ds[len(ds)//2]
    def _pf(x):
        a = sum(r["pnl"] for r in x if r["pnl"] > 0); b = -sum(r["pnl"] for r in x if r["pnl"] <= 0)
        return (a/b) if b > 0 else 99.0
    rc = {}
    for r in res: rc[r["res"]] = rc.get(r["res"], 0) + 1
    fb = sorted(r["bar"] for r in res if r["res"] == "FILL")
    out.append(f"{ind}{lab:12s} n={n:3d} 승률 {w/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
               f"PF {g/l if l else 99:6.2f} |상위2제외 {g2/l2 if l2 else 99:6.2f} "
               f"평균 {sum(r['pnl'] for r in res)/n:+.3f}% 합계 {sum(r['pnl'] for r in res):+6.1f}% "
               f"| 반반 {_pf([r for r in res if r['d']<half]):5.2f}/{_pf([r for r in res if r['d']>=half]):5.2f} "
               f"| 필소요중앙 {fb[len(fb)//2]*mins if fb else '-'}분 "
               f"| {'/'.join(f'{k}{v}' for k,v in sorted(rc.items()))}")


def main():
    out = [f"커버 기준 {COVER_MIN:.0%} · 진입=첫봉 종가 · TP=전날종가 · 시간청산 14:30",
           "손절: EXT k=첫봉극점+갭×k / FIX k=진입가±k% / NOSTOP=시간청산만"]
    for tk in ("QQQ", "SPY"):
        for iv, per in (("5m", "60d"), ("15m", "60d"), ("1h", "2y")):
            try:
                days = build(tk, iv, per)
            except Exception as e:
                out.append(f"[{tk}/{iv}] 실패 {e}"); continue
            mins = {"5m": 5, "15m": 15, "1h": 60}[iv]
            out.append(f"\n{'='*126}\n[{tk}] 첫봉={iv} · 갭 {len(days)}일\n{'='*126}")
            for sgn, nm in ((1, "갭업→풋"), (-1, "갭다운→콜")):
                sel = [x for x in days if x["sgn"] == sgn and x["cover"] >= COVER_MIN]
                out.append(f"  ── {nm} · 커버≥{COVER_MIN:.0%} (n={len(sel)}) ──")
                if len(sel) < 10:
                    out.append(f"    표본부족 ({len(sel)})"); continue
                for lab, mode, k in CFGS:
                    res = []
                    for day in sel:
                        r = sim(day, mode, k); r["d"] = day["d"]; res.append(r)
                    rep(res, lab, out, mins)
    return out


if __name__ == "__main__":
    try: r = main()
    except Exception: r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r); print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("gapsweep_result.json", "w"), ensure_ascii=False, indent=1)
