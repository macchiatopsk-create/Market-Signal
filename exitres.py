"""청산 판정 해상도 대조 실험.
진입 조건은 전부 고정하고 '청산을 몇 분봉으로 판정하느냐'만 바꾼다.
동일한 거래 집합을 5분 / 15분 / 1시간 해상도로 각각 판정해 차이를 본다.

규칙 (형님 확정본):
  진입   기준선 봉 종가. 갭 0.2~1.5%, 개장VIX|x|<5%, 커버 >= 0.40, 진입시점 미필(커버<1.0)
  손절   없음
  갭필   저가(갭업)/고가(갭다운)가 전일종가 도달 → 트레일 무장. 그 봉에서는 청산 판정 안 함
  트레일 갭필 이후 봉부터. ext = 갭필 이후 최저(최고), 트레일선 = ext*(1±0.15%)
  타임컷 11:30까지 미갭필 → 청산 / 14:00 최종컷
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

TRAIL = 0.15
COVER_MIN = 0.40
TIMECUT = dt.time(11, 30)
FINALCUT = dt.time(14, 0)
BASE_IDX = {"5m": 1, "15m": 2, "1h": 11}      # 앱과 동일 (5분봉 0-base)
RES = {"5m": 1, "15m": 3, "1h": 12}           # 청산 판정 해상도 = 5분봉 몇 개 묶음


def wilson(k, n):
    if n == 0:
        return (0.0, 0.0)
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def norm(d):
    try:
        d.index = d.index.tz_localize(None)
    except Exception:
        pass
    d.index = pd.to_datetime(d.index).normalize()
    return d[~d.index.duplicated(keep="last")]


def agg(bars, k):
    """5분봉 리스트를 k개씩 묶어 (t, h, l, c) 리스트로."""
    out = []
    for i in range(0, len(bars), k):
        ch = bars[i:i + k]
        if not ch:
            continue
        out.append((ch[-1][0], max(x[1] for x in ch), min(x[2] for x in ch), ch[-1][3]))
    return out


def run(bars5, i0, ep, tgt, sgn, k):
    """i0 = 진입한 5분봉 인덱스. 그 다음 봉부터 k묶음 해상도로 판정."""
    seq = agg(bars5[i0 + 1:], k)
    filled = False
    ext = None
    for (t, h, l, c) in seq:
        if not filled:
            if (l <= tgt) if sgn > 0 else (h >= tgt):
                filled = True
                ext = min(l, tgt) if sgn > 0 else max(h, tgt)
                continue                      # 갭필 봉에서는 청산 판정 안 함
            if t.time() >= TIMECUT:
                return "TIMECUT", ((ep - c) / ep * 100) if sgn > 0 else ((c - ep) / ep * 100), t
            continue
        ext = min(ext, l) if sgn > 0 else max(ext, h)
        tp = ext * (1 + TRAIL / 100) if sgn > 0 else ext * (1 - TRAIL / 100)
        if (h >= tp) if sgn > 0 else (l <= tp):
            return "TRAIL", ((ep - tp) / ep * 100) if sgn > 0 else ((tp - ep) / ep * 100), t
        if t.time() >= FINALCUT:
            return "CUT", ((ep - c) / ep * 100) if sgn > 0 else ((c - ep) / ep * 100), t
    t, _, _, c = seq[-1]
    return "EOD", ((ep - c) / ep * 100) if sgn > 0 else ((c - ep) / ep * 100), t


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
    res = {bl: {r: [] for r in RES} for bl in BASE_IDX}
    detail = []
    pc = None
    for d in days:
        g = df[df.index.date == d]
        if len(g) < 60:
            if len(g):
                pc = float(g["Close"].iloc[-1])
            continue
        bars5 = [(t, float(r["High"]), float(r["Low"]), float(r["Close"]))
                 for t, r in g.iterrows()]
        O0 = float(g["Open"].iloc[0])
        if pc:
            vx = vm.get(str(d))
            if vx is None or abs(vx) < 5.0:
                gap = O0 - pc
                gp = gap / pc * 100
                if 0.2 <= abs(gp) < 1.5:
                    sgn = 1 if gap > 0 else -1
                    for bl, i0 in BASE_IDX.items():
                        if i0 >= len(bars5) - 2:
                            continue
                        ep = bars5[i0][3]
                        cov = ((O0 - ep) / gap) if sgn > 0 else ((ep - O0) / abs(gap))
                        if not (COVER_MIN <= cov < 1.0):
                            continue
                        row = dict(d=str(d), bl=bl, gap=round(abs(gp), 3),
                                   sgn=sgn, cov=round(cov, 2), ep=round(ep, 2))
                        for rn, k in RES.items():
                            r_, p_, t_ = run(bars5, i0, ep, pc, sgn, k)
                            res[bl][rn].append(p_)
                            row[rn] = (r_, round(p_, 3), t_.strftime("%H:%M"))
                        detail.append(row)
        pc = bars5[-1][3]

    def stat(a):
        n = len(a)
        if n == 0:
            return "  표본0"
        w = sum(1 for x in a if x > 0)
        ci = wilson(w, n)
        gp = sum(x for x in a if x > 0)
        ls = -sum(x for x in a if x <= 0)
        p = "패배0" if ls <= 0 else f"{gp/ls:.2f}"
        return (f"n={n:3d}  승률 {w/n*100:5.1f}% ({ci[0]:4.1f}~{ci[1]:4.1f})  "
                f"PF {p:>6s}  평균 {np.mean(a):+.3f}%  합계 {sum(a):+.2f}%")

    out = [f"청산 판정 해상도 대조 · QQQ 5분봉 {len(days)}거래일 ({days[0]}~{days[-1]})",
           f"트레일 {TRAIL}% · 손절없음 · 11:30 타임컷 · 14:00 최종컷 · 커버 {COVER_MIN}~1.00",
           "※ 진입은 완전히 동일. 청산 판정 봉 크기만 다름", ""]
    for bl in BASE_IDX:
        out.append(f"[진입 기준선 {bl} (5분봉 idx {BASE_IDX[bl]})]")
        for rn in RES:
            out.append(f"  청산판정 {rn:4s}  {stat(res[bl][rn])}")
        out.append("")

    out.append("[전 기준선 합산 — 해상도별]")
    for rn in RES:
        allp = [p for bl in BASE_IDX for p in res[bl][rn]]
        out.append(f"  청산판정 {rn:4s}  {stat(allp)}")

    out.append("")
    out.append("[개별 거래 — 해상도별 청산]")
    out.append(f"  {'날짜':10s} {'기준':4s} {'커버':5s} {'진입':8s} "
               f"{'5분판정':>22s} {'15분판정':>22s} {'1시간판정':>22s}")
    for r in detail:
        cells = ""
        for rn in RES:
            a, b, c = r[rn]
            cells += f"  {a:7s}{b:+7.3f}%@{c}"
        out.append(f"  {r['d']:10s} {r['bl']:4s} {r['cov']:5.2f} {r['ep']:8.2f}{cells}")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("exitres_result.json", "w"), ensure_ascii=False, indent=1)
