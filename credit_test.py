"""
크레딧 스프레드가 성립하는지 측정.
방향 예측이 필요 없는 대신, '숏 스트라이크가 안 터치될 확률'이
구조가 정해주는 손익분기 승률을 넘어야 한다.

측정: 60일 5분봉으로 진입시각 이후 종가까지의 최대 이탈폭을 재고,
      숏 스트라이크 거리(진입가 대비 %)별 미터치 비율을 구한다.
      밴드폭 레짐별로도 쪼갠다 (좁은 날이 크레딧에 유리하다는 가설 검증).
"""
import json, math, datetime as dt
import yfinance as yf

ENTRY = dt.time(10, 30)
DISTS = [0.15, 0.25, 0.35, 0.50, 0.75, 1.00]   # 숏 스트라이크 거리 (%)


def wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k / n, 1.96; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def measure(tk):
    df = yf.Ticker(tk).history(period="60d", interval="5m")
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    try: df.index = df.index.tz_convert("America/New_York")
    except Exception: pass
    df = df[(df.index.time >= dt.time(9, 30)) & (df.index.time < dt.time(16, 0))]

    rows = []
    for d in sorted(set(df.index.date)):
        day = df[df.index.date == d]
        if len(day) < 60: continue
        H = [float(x) for x in day["High"]]; L = [float(x) for x in day["Low"]]
        C = [float(x) for x in day["Close"]]; V = [float(x) for x in day["Volume"]]
        T = [t.time() for t in day.index]
        # 진입 시점 VWAP 밴드폭 (레짐 분류용)
        cpv = cv = cpv2 = 0.0; bw = None; ei = None
        for i in range(len(C)):
            tp = (H[i] + L[i] + C[i]) / 3
            cpv += tp * V[i]; cv += V[i]; cpv2 += tp * tp * V[i]
            w = cpv / cv if cv else tp
            sd = math.sqrt(max(cpv2 / cv - w * w, 0.0)) if cv else 0.0
            if ei is None and T[i] >= ENTRY:
                ei = i; bw = 2 * sd / w * 100 if w else 0.0
        if ei is None or ei >= len(C) - 1: continue
        ep = C[ei]
        up = (max(H[ei + 1:]) / ep - 1) * 100      # 진입 후 최대 상방 이탈
        dn = (1 - min(L[ei + 1:]) / ep) * 100      # 최대 하방 이탈
        rows.append(dict(d=str(d), bw=round(bw, 4), up=round(up, 4), dn=round(dn, 4),
                         both=round(max(up, dn), 4)))
    return rows


def report(rows, tk):
    out = [f"\n########## {tk}  (진입 10:30, n={len(rows)}일) ##########"]
    bws = sorted(r["bw"] for r in rows)
    lo_cut, hi_cut = bws[len(bws)//3], bws[2*len(bws)//3]
    groups = [("전체", rows),
              (f"밴드폭 좁음(<{lo_cut:.2f}%)", [r for r in rows if r["bw"] < lo_cut]),
              (f"밴드폭 넓음(>={hi_cut:.2f}%)", [r for r in rows if r["bw"] >= hi_cut])]
    for nm, ss in groups:
        n = len(ss)
        if n < 10: continue
        out.append(f"\n[{nm}] n={n}일")
        out.append("  거리    아이언콘도르(양쪽)      한쪽만(풋크레딧)     손익분기(폭$5)")
        for dist in DISTS:
            k_both = sum(1 for r in ss if r["both"] < dist)
            k_one  = sum(1 for r in ss if r["dn"] < dist)
            cb, co = wilson(k_both, n), wilson(k_one, n)
            # 거리가 멀수록 받는 크레딧이 작아진다 -> 손익분기 승률 상승 (근사)
            out.append(f"  {dist:.2f}%   {k_both/n*100:5.1f}% CI({cb[0]:4.1f}~{cb[1]:4.1f})   "
                       f"{k_one/n*100:5.1f}% CI({co[0]:4.1f}~{co[1]:4.1f})")
    # 이탈폭 분포
    for nm, ss in groups:
        if len(ss) < 10: continue
        v = sorted(r["both"] for r in ss)
        q = lambda p: v[int(len(v)*p)]
        out.append(f"\n[{nm}] 양방향 최대이탈 분포: "
                   f"중앙값 {q(0.5):.2f}%  75%tile {q(0.75):.2f}%  90%tile {q(0.90):.2f}%  최대 {v[-1]:.2f}%")
    return out


if __name__ == "__main__":
    rep = []; data = {}
    for tk in ("SPY", "QQQ"):
        try:
            rows = measure(tk)
            rep += report(rows, tk)
            data[tk] = rows
            print(f"  {tk} 완료 {len(rows)}일", flush=True)
        except Exception as e:
            m = f"[{tk}] 실패 {type(e).__name__}: {e}"; rep.append(m); print(m)
    txt = "\n".join(rep); print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
               "report": txt, "days": data},
              open("credit_result.json", "w"), ensure_ascii=False, indent=1)
