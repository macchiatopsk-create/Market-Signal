"""
v7 백테스트 — 원본 v3에 변경을 하나씩 얹어 각 변경의 기여를 분리 측정.
market_radar.py 는 건드리지 않는다 (라이브 운영 중, 운영헌법 1조).

  A  원본 v3 그대로                      ← 재구현 검증용 베이스라인
  B  A + 포지션 중복 방지 (한 번에 하나)   ← 중복 카운팅의 실제 영향
  C  B + 시간게이트 (평일 14:00 / 금 12:00)
  D  C + 방향판정을 EMA+GAP → VWAP 레짐 상태기계로 교체

A가 radar_history.json 의 v3 수치와 근사 일치해야 재구현이 맞다는 뜻.
밴드폭 임계값은 원본과 동일하게 표본 상위 1/3 분위수로 동적 산출 (헌법 7조).
"""
import json, math, datetime as dt, sys
import yfinance as yf

TICKERS = ("SPY", "QQQ")
K10 = 5                    # 원본과 동일: 6번째 봉(09:55)부터 진입 허용
JUDGE_FROM = dt.time(10, 30)   # D 변형: 레짐 판정 시작
FALLBACK   = dt.time(12, 0)    # D 변형: 미확정 timeout 판정 시점


def _ema(vals, p):
    k = 2 / (p + 1); e = None
    for x in vals:
        e = x if e is None else x * k + e * (1 - k)
    return e


def _wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k / n, 1.96; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def cutoff_for(d):
    """평일 14:00 ET, 금요일 12:00 ET"""
    return dt.time(12, 0) if d.weekday() == 4 else dt.time(14, 0)


def session_bands(H, L, C, V):
    n = len(C); cpv = cv = cpv2 = 0.0; vwap = []; sd = []
    for i in range(n):
        tp = (H[i] + L[i] + C[i]) / 3
        cpv += tp * V[i]; cv += V[i]; cpv2 += tp * tp * V[i]
        w = cpv / cv if cv else tp
        var = max(cpv2 / cv - w * w, 0.0) if cv else 0.0
        vwap.append(w); sd.append(math.sqrt(var))
    return vwap, sd


def regime_path(T, H, L, C, vwap, sd):
    """D 변형: 밴드 경로로 그날 방향을 확정. 확정된 봉 인덱스와 방향을 돌려준다.
    하단터치→중앙돌파→상단도달=BULL / 하단재이탈=BEAR (상단 시작은 대칭)
    12:00까지 미확정이면 그 시점 종가 위치로 판정."""
    stage = side = None
    for i in range(len(C)):
        if sd[i] <= 1e-9 or T[i] < JUDGE_FROM:
            continue
        up, lo = vwap[i] + sd[i], vwap[i] - sd[i]
        if stage is None:
            if L[i] <= lo:   stage, side = "T", "L"
            elif H[i] >= up: stage, side = "T", "H"
        elif stage == "T":
            if side == "L" and H[i] >= vwap[i]: stage = "C"
            elif side == "H" and L[i] <= vwap[i]: stage = "C"
        elif stage == "C":
            if side == "L":
                if H[i] >= up:   return i, 1
                elif L[i] <= lo: return i, -1
            else:
                if L[i] <= lo:   return i, -1
                elif H[i] >= up: return i, 1
        if T[i] >= FALLBACK:
            return i, (1 if C[i] > vwap[i] else -1)
    return None, 0


def run(tk, variant):
    df = yf.Ticker(tk).history(period="60d", interval="5m")
    if df is None or df.empty:
        return []
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    try: df.index = df.index.tz_convert("America/New_York")
    except Exception: pass
    df = df[(df.index.time >= dt.time(9, 30)) & (df.index.time < dt.time(16, 0))]

    idx = list(df.index)
    pos_of = {ts: i for i, ts in enumerate(idx)}      # 원본의 O(n^2) 검색 제거
    closes_all = [float(x) for x in df["Close"]]

    no_overlap = variant in ("B", "C", "D", "E", "F")
    time_gate  = variant in ("C", "D", "E", "F")
    use_regime = variant in ("D", "E", "F")
    thresh = {"E": 0.5, "F": 0.0}.get(variant, 1.0)   # 진입 임계 (σ)

    days = sorted(set(df.index.date)); trades = []; prev_close = None
    for d in days:
        day = df[df.index.date == d]
        if len(day) < 60:
            if len(day): prev_close = float(day["Close"].iloc[-1])
            continue
        O = [float(x) for x in day["Open"]]; H = [float(x) for x in day["High"]]
        L = [float(x) for x in day["Low"]];  C = [float(x) for x in day["Close"]]
        V = [float(x) for x in day["Volume"]]
        T = [ts.time() for ts in day.index]
        n = len(C)
        vwap, sd = session_bands(H, L, C, V)
        cut = cutoff_for(d)

        # ---- 방향 판정 ----
        if use_regime:
            reg_i, ddir = regime_path(T, H, L, C, vwap, sd)
            start = (reg_i + 1) if reg_i is not None else n
        else:
            gap = (O[0] / prev_close - 1) * 100 if prev_close else 0.0
            i10 = pos_of[day.index[K10]]
            hist = closes_all[max(0, i10 - 60): i10 + 1]
            e9, e21 = _ema(hist, 9), _ema(hist, 21)
            up = 1 if e9 > e21 else -1
            ddir = up if ((up > 0 and gap > 0) or (up < 0 and gap < 0)) else 0
            start = K10 + 1

        if ddir != 0:
            i = start
            while i < n - 1:
                if sd[i] <= 1e-9: i += 1; continue
                if time_gate and T[i] >= cut: break
                dev = (C[i] - vwap[i]) / sd[i]
                bw = 2 * sd[i] / vwap[i] * 100
                if (ddir > 0 and dev > -thresh) or (ddir < 0 and dev < thresh):
                    i += 1; continue

                ep = C[i]; mid_hit = None; res = None; last_j = i
                for j in range(i + 1, n):
                    last_j = j; hold = (j - i) * 5
                    if time_gate and T[j] >= cut:
                        p = ((O[j] / ep - 1) * 100) if ddir > 0 else ((ep / O[j] - 1) * 100)
                        res = ("CUT", p, hold); break
                    if mid_hit is None:
                        if (ddir > 0 and H[j] >= vwap[j]) or (ddir < 0 and L[j] <= vwap[j]):
                            mid_hit = j
                        elif (ddir > 0 and L[j] <= vwap[j] - 2 * sd[j]) or \
                             (ddir < 0 and H[j] >= vwap[j] + 2 * sd[j]):
                            px = (vwap[j] - 2 * sd[j]) if ddir > 0 else (vwap[j] + 2 * sd[j])
                            p = ((px / ep - 1) * 100) if ddir > 0 else ((ep / px - 1) * 100)
                            res = ("STOP_PRE", p, hold); break
                    else:
                        band = vwap[j] + sd[j] if ddir > 0 else vwap[j] - sd[j]
                        if (H[j] >= band) if ddir > 0 else (L[j] <= band):
                            p = ((band / ep - 1) * 100) if ddir > 0 else ((ep / band - 1) * 100)
                            res = ("BAND", p, hold); break
                        if j - mid_hit >= 2:
                            p = ((C[j] / ep - 1) * 100) if ddir > 0 else ((ep / C[j] - 1) * 100)
                            res = ("MID_EXIT", p, hold); break
                if res is None:
                    p = ((C[-1] / ep - 1) * 100) if ddir > 0 else ((ep / C[-1] - 1) * 100)
                    res = ("EOD", p, (n - 1 - i) * 5); last_j = n - 1

                trades.append(dict(d=str(d), dir=ddir, bw=round(bw, 4),
                                   reason=res[0], pnl=round(res[1], 4), hold=res[2],
                                   t=T[i].strftime("%H:%M")))
                i = last_j + 1 if no_overlap else i + 1   # 여기가 A와 B의 유일한 차이
        prev_close = C[-1]
    return trades


def analyze(trades, tk, variant):
    if len(trades) < 20:
        return [f"[{tk}/{variant}] 표본 {len(trades)} 부족"], {}
    rep = []; data = {}
    bws = sorted(t["bw"] for t in trades); cut = bws[2 * len(bws) // 3]
    for nm, ss in (("전체", trades),
                   (f"밴드폭넓음(≥{cut:.2f}%)", [t for t in trades if t["bw"] >= cut])):
        if len(ss) < 20: continue
        n = len(ss); w = sum(1 for t in ss if t["pnl"] > 0)
        tot = sum(t["pnl"] for t in ss); ci = _wilson(w, n)
        g = sum(t["pnl"] for t in ss if t["pnl"] > 0)
        l = -sum(t["pnl"] for t in ss if t["pnl"] <= 0)
        pf = (g / l) if l > 0 else float("inf")
        hold = sum(t["hold"] for t in ss) / n
        rep.append(f"\n[{tk}/{variant}] {nm} n={n} 승률 {w/n*100:.1f}% CI({ci[0]}~{ci[1]}) "
                   f"평균 {tot/n:+.4f}% 합계 {tot:+.1f}% PF {pf:.2f} 평균홀드 {hold:.0f}분 "
                   f"거래일 {len(set(t['d'] for t in ss))}일")
        for r_ in ("BAND", "MID_EXIT", "STOP_PRE", "CUT", "EOD"):
            s2 = [t for t in ss if t["reason"] == r_]
            if s2:
                rep.append(f"    {r_:9s} {len(s2):4d}건({len(s2)/n*100:2.0f}%) "
                           f"평균 {sum(t['pnl'] for t in s2)/len(s2):+.4f}% "
                           f"홀드 {sum(t['hold'] for t in s2)/len(s2):.0f}분")
        if "넓음" in nm:
            data = dict(n=n, win=round(w/n*100, 1), ci=list(ci), avg=round(tot/n, 4),
                        total=round(tot, 1), pf=round(pf, 2), hold=round(hold),
                        days=len(set(t["d"] for t in ss)))
    return rep, data


if __name__ == "__main__":
    report = []; out = {}
    dump = {}
    for variant in ("A", "B", "C", "D", "E", "F"):
        for tk in TICKERS:
            try:
                tr = run(tk, variant)
                rep, data = analyze(tr, tk, variant)
                report += rep
                out.setdefault(variant, {})[tk] = data
                dump.setdefault(variant, {})[tk] = tr
                print(f"  {variant}/{tk} 완료: {len(tr)}건", flush=True)
            except Exception as e:
                msg = f"[{tk}/{variant}] 실패: {type(e).__name__}: {e}"
                report.append(msg); print("  " + msg, flush=True)
    txt = "\n".join(report)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
               "report": txt, "data": out, "trades": dump},
              open("v7_result.json", "w"), ensure_ascii=False, indent=1)
