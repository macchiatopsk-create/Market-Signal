"""기준선별(5분/15분/1시간) 실행가능 갭일 발생률 — 5분봉 60일 재표본.
1시간봉 2년 결과와 직접 비교 가능하도록 동일 필터 사용.
  대상: 갭 0.2~1.5%, 개장 VIX 변화 |x|<5%
  실행가능: 첫봉 커버 >= 0.40  AND  커버 < 1.0 (진입 시점에 아직 미필)
"""
import json, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

COVER_MIN = 0.40
BASELINES = {"5m": "09:35", "15m": "09:45", "1h": "10:30"}


def norm(d):
    try:
        d.index = d.index.tz_localize(None)
    except Exception:
        pass
    d.index = pd.to_datetime(d.index).normalize()
    return d[~d.index.duplicated(keep="last")]


def main():
    v = norm(yf.Ticker("^VIX").history(period="6mo")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in ch.dropna().items()}

    df = yf.download("QQQ", period="60d", interval="5m", prepost=False,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df.index = df.index.tz_convert("America/New_York")
    df = df[(df.index.time >= dt.time(9, 30)) & (df.index.time < dt.time(16, 0))]

    days = sorted(set(df.index.date))
    out = [f"QQQ 5분봉 {len(days)}거래일 ({days[0]} ~ {days[-1]})",
           f"대상 갭 0.2~1.5% · 개장VIX|x|<5% · 커버 >= {COVER_MIN:.2f}", ""]

    recs = []
    pc = None
    for d in days:
        g = df[df.index.date == d]
        if len(g) < 60:
            if len(g):
                pc = float(g["Close"].iloc[-1])
            continue
        ds = str(d)
        O = [float(x) for x in g["Open"]]
        L = [float(x) for x in g["Low"]]
        H = [float(x) for x in g["High"]]
        C = [float(x) for x in g["Close"]]
        if pc:
            vx = vm.get(ds)
            if vx is None or abs(vx) < 5.0:
                gap = O[0] - pc
                gp = gap / pc * 100
                if 0.2 <= abs(gp) < 1.5:
                    sgn = 1 if gap > 0 else -1
                    r = dict(d=ds, gap=round(abs(gp), 3), sgn=sgn)
                    # 기준선별 첫봉 종가 = 5분봉 인덱스 0 / 2 / 11
                    for bl, idx in (("5m", 0), ("15m", 2), ("1h", 11)):
                        if idx >= len(C):
                            r[bl] = None
                            continue
                        cl = C[idx]
                        cov = ((O[0] - cl) / gap) if sgn > 0 else ((cl - O[0]) / abs(gap))
                        r[bl] = round(cov, 3)
                    recs.append(r)
        pc = C[-1]

    nd = len(days)
    ann = 252.0 / nd
    out.append(f"전체 대상 갭일 {len(recs)}건  →  연환산 {len(recs)*ann:.0f}건")
    out.append("")
    hdr = f"  {'기준선':8s} {'시각':6s} {'커버40%+':>8s} {'그중 이미필':>10s} {'실행가능':>8s} {'연환산':>7s}"
    out.append(hdr)
    for bl in ("5m", "15m", "1h"):
        ok = [r for r in recs if r[bl] is not None and r[bl] >= COVER_MIN]
        already = [r for r in ok if r[bl] >= 1.0]
        exe = [r for r in ok if r[bl] < 1.0]
        out.append(f"  {bl:8s} {BASELINES[bl]:6s} {len(ok):8d} {len(already):10d} "
                   f"{len(exe):8d} {len(exe)*ann:7.0f}")

    out.append("")
    out.append("[교차: 1시간 기준선이 놓친 날을 5분/15분이 잡는가]")
    miss1h = [r for r in recs if r["1h"] is not None and r["1h"] >= 1.0]
    out.append(f"  1h 커버>=1.0 (이미 갭필로 진입 불가) {len(miss1h)}건")
    for bl in ("5m", "15m"):
        got = [r for r in miss1h if r[bl] is not None and COVER_MIN <= r[bl] < 1.0]
        out.append(f"    → {bl} 기준선에서 실행가능 {len(got)}건 "
                   f"({len(got)/len(miss1h)*100:.0f}%)" if miss1h else "    → 표본없음")

    out.append("")
    out.append("[반대: 5분이 잡고 1시간이 못 잡는 것 외에, 1시간만 잡는 날]")
    for bl in ("5m", "15m"):
        only1h = [r for r in recs
                  if r["1h"] is not None and COVER_MIN <= r["1h"] < 1.0
                  and (r[bl] is None or r[bl] < COVER_MIN)]
        out.append(f"  1h 실행가능인데 {bl} 커버 40% 미달: {len(only1h)}건")

    out.append("")
    out.append("[개별 갭일 커버 — 5m / 15m / 1h]")
    for r in recs:
        f5 = f'{r["5m"]:.2f}' if r["5m"] is not None else "--"
        f15 = f'{r["15m"]:.2f}' if r["15m"] is not None else "--"
        f1 = f'{r["1h"]:.2f}' if r["1h"] is not None else "--"
        mark = "".join(bl[0] if (r[bl] is not None and COVER_MIN <= r[bl] < 1.0) else "."
                       for bl in ("5m", "15m", "1h"))
        out.append(f"  {r['d']}  갭{r['gap']:+.2f}%{'↑' if r['sgn']>0 else '↓'}  "
                   f"{f5:>6s} {f15:>6s} {f1:>6s}   {mark}")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("baserate_result.json", "w"), ensure_ascii=False, indent=1)
