"""
갭필 경로 검증 — 실제로 옵션에 실을 수 있는지.

앞선 결과: 첫봉 커버≥50% -> 갭필 93~99% (QQQ/SPY, 458/440일, 반반 통과)
이제 재는 것:
  1) MAE   진입(첫봉 종가) 후 갭필 전까지 최대 역행폭  <- 0DTE 생존 가능한가
  2) 시각  몇 번째 봉에서 메워지는가                  <- 14:00 컷에 걸리는가
  3) 손익  TP=전날종가 / SL=첫봉 극점 / 14:00 시간청산 으로 PF 계산
  4) VIX   L1 게이트(백분위>=50)와 결합 시 변화

방향: 갭업 커버 -> 하락 베팅(풋) / 갭다운 커버 -> 상승 베팅(콜)
1시간봉 2년. 해상도는 1시간이지만 표본이 8배라 판정 가능.
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

COVER_LO, COVER_HI = 0.5, 1.0     # 실전 구간 (1.0 이상은 이미 메워진 것에 가까움)
# 컷오프는 인터벌별로 14:30 에 맞춰 계산


def wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k / n, 1.96; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def _n(x):
    try: x.index = x.index.tz_localize(None)
    except (TypeError, AttributeError): pass
    x.index = pd.to_datetime(x.index).normalize()
    return x[~x.index.duplicated(keep="last")]


def _grab(tk, tries=4):
    import time
    for i in range(tries):
        try:
            s = yf.Ticker(tk).history(period="3y")["Close"].dropna()
            if len(s) > 100: return _n(s)
        except Exception: pass
        time.sleep(5 * (2 ** i))
    return None


def vix_map():
    a = _grab("^VIX9D")
    if a is None: a = _grab("^VIX")
    b = _grab("^VIX3M")
    if a is None or b is None: return {}
    ts = (a / b.reindex(a.index).ffill()).dropna()
    def _p(w):
        if len(w) < 2: return float("nan")
        return float((w[:-1] < w[-1]).sum()) / (len(w) - 1) * 100
    pct = ts.rolling(252).apply(_p, raw=True).shift(1)
    return {str(pd.Timestamp(d).date()): float(v) for d, v in pct.dropna().items()}


def build(tk, interval="1h", period="2y"):
    df = yf.download(tk, period=period, interval=interval, prepost=False,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df.index = df.index.tz_convert("America/New_York")
    df = df[(df.index.time >= dt.time(9, 30)) & (df.index.time < dt.time(16, 0))]

    rows = []; prev_close = None
    for d in sorted(set(df.index.date)):
        g = df[df.index.date == d]
        need = {"5m": 40, "15m": 15, "1h": 5}[interval]
        if len(g) < need:
            if len(g): prev_close = float(g["Close"].iloc[-1])
            continue
        cut_bar = {"5m": 60, "15m": 20, "1h": 4}[interval]      # 14:30 상당
        O = [float(x) for x in g["Open"]]; H = [float(x) for x in g["High"]]
        L = [float(x) for x in g["Low"]];  C = [float(x) for x in g["Close"]]
        T = [t.strftime("%H:%M") for t in g.index]
        if prev_close:
            gap = O[0] - prev_close
            gp = gap / prev_close * 100
            if abs(gp) >= 0.05:
                sgn = 1 if gap > 0 else -1
                cover = ((O[0] - C[0]) / gap) if sgn > 0 else ((C[0] - O[0]) / abs(gap))
                ep = C[0]                                  # 첫봉 종가 진입
                tgt = prev_close                           # 갭필 타깃
                stop = H[0] if sgn > 0 else L[0]           # 첫봉 극점 손절
                fill_bar = None; mae = 0.0; stop_bar = None
                for i in range(1, len(C)):
                    adverse = (H[i] - ep) / ep * 100 if sgn > 0 else (ep - L[i]) / ep * 100
                    mae = max(mae, adverse)
                    if stop_bar is None:
                        if (H[i] >= stop) if sgn > 0 else (L[i] <= stop): stop_bar = i
                    if fill_bar is None:
                        if (L[i] <= tgt) if sgn > 0 else (H[i] >= tgt): fill_bar = i
                    if fill_bar is not None: break
                # 손익: TP=갭필 / SL=첫봉극점 / CUT_BAR 시간청산 (먼저 온 것)
                if fill_bar is not None and (stop_bar is None or fill_bar <= stop_bar) and fill_bar <= cut_bar:
                    pnl = abs(tgt - ep) / ep * 100 * (1 if True else 1); res = "FILL"
                elif stop_bar is not None and stop_bar <= cut_bar:
                    pnl = -abs(stop - ep) / ep * 100; res = "STOP"
                else:
                    idx = min(cut_bar, len(C) - 1)
                    px = C[idx]
                    pnl = ((ep - px) / ep * 100) if sgn > 0 else ((px - ep) / ep * 100)
                    res = "CUT"
                room = abs(tgt - ep) / ep * 100          # 진입가->갭필까지 남은 거리(%)
                rows.append(dict(d=str(d), dir=sgn, gp=round(gp, 3), cover=round(cover, 3),
                                 room=round(room, 3),
                                 mae=round(mae, 3), fill_bar=fill_bar, res=res,
                                 pnl=round(pnl, 4),
                                 fill_t=(T[fill_bar] if fill_bar is not None else None)))
        prev_close = C[-1]
    return rows


def rep(rows, lab, out, ind="    "):
    n = len(rows)
    if n < 10:
        out.append(f"{ind}{lab:30s} n={n:4d} 표본부족"); return
    w = sum(1 for r in rows if r["pnl"] > 0); ci = wilson(w, n)
    g = sum(r["pnl"] for r in rows if r["pnl"] > 0); l = -sum(r["pnl"] for r in rows if r["pnl"] <= 0)
    s2 = sorted(rows, key=lambda x: -x["pnl"])[2:]
    g2 = sum(r["pnl"] for r in s2 if r["pnl"] > 0); l2 = -sum(r["pnl"] for r in s2 if r["pnl"] <= 0)
    ds = sorted(r["d"] for r in rows); half = ds[len(ds)//2]
    def _pf(x):
        a = sum(r["pnl"] for r in x if r["pnl"] > 0); b = -sum(r["pnl"] for r in x if r["pnl"] <= 0)
        return (a/b) if b > 0 else 99.0
    fb = [r["fill_bar"] for r in rows if r["fill_bar"] is not None]
    rc = {}
    for r in rows: rc[r["res"]] = rc.get(r["res"], 0) + 1
    out.append(f"{ind}{lab:30s} n={n:4d} 승률 {w/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
               f"PF {g/l if l else 99:5.2f} |상위2제외 {g2/l2 if l2 else 99:5.2f} "
               f"평균 {sum(r['pnl'] for r in rows)/n:+.3f}% | 반반 {_pf([r for r in rows if r['d']<half]):.2f}/"
               f"{_pf([r for r in rows if r['d']>=half]):.2f} | MAE중앙 {sorted(r['mae'] for r in rows)[n//2]:.2f}% "
               f"최악 {max(r['mae'] for r in rows):.2f}% | 필봉중앙 {sorted(fb)[len(fb)//2] if fb else '-'} "
               f"| 잔여갭중앙 {sorted(r['room'] for r in rows)[n//2]:.3f}% "
               f"| {'/'.join(f'{k}{v}' for k,v in sorted(rc.items()))}")


def main():
    out = []
    vmap = vix_map()
    out.append(f"VIX 맵 {len(vmap)}일 · 진입=첫봉 종가 · TP=전날종가 · SL=첫봉극점 · 시간청산 14:30")
    for tk in ("QQQ", "SPY"):
      for iv, per in (("5m", "60d"), ("15m", "60d")):
        rows = build(tk, iv, per)
        out.append(f"\n{'='*118}\n[{tk}] 첫봉={iv} · {len(rows)}일\n{'='*118}")
        for sgn, nm in ((1, "갭업 → 풋(하락베팅)"), (-1, "갭다운 → 콜(상승베팅)")):
            ss = [r for r in rows if r["dir"] == sgn]
            out.append(f"  ── {nm} (n={len(ss)}) ──")
            rep(ss, "전체 갭 (베이스라인)", out)
            core = [r for r in ss if COVER_LO <= r["cover"] < COVER_HI]
            rep(core, f"커버 {COVER_LO}~{COVER_HI} (핵심)", out)
            rep([r for r in ss if r["cover"] >= COVER_LO], f"커버 ≥{COVER_LO} (전체)", out)
            big = [r for r in ss if r["cover"] >= COVER_LO and abs(r["gp"]) >= 0.4]
            rep(big, "커버≥0.5 & 갭≥0.4%", out)
            if vmap:
                rep([r for r in ss if r["cover"] >= COVER_LO and vmap.get(r["d"], -1) >= 50],
                    "커버≥0.5 & VIX≥50%", out)
                rep([r for r in ss if r["cover"] >= COVER_LO and vmap.get(r["d"], -1) < 50],
                    "커버≥0.5 & VIX<50%", out)
            fbn = [r["fill_bar"] for r in ss if r["cover"] >= COVER_LO and r["fill_bar"] is not None]
            if fbn:
                fbn.sort()
                mins = {"5m": 5, "15m": 15, "1h": 60}[iv]
                out.append(f"      갭필 소요: 중앙 {fbn[len(fbn)//2]*mins}분 · "
                           f"75%tile {fbn[int(len(fbn)*0.75)]*mins}분 · 최대 {fbn[-1]*mins}분")
    return out


if __name__ == "__main__":
    try: r = main()
    except Exception: r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r); print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("gappath_result.json", "w"), ensure_ascii=False, indent=1)
