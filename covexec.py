"""실행가능 구간(커버 40~100%) 강건성 검증.
covthresh.py 와 동일한 거래 생성 로직 · 1시간봉 2년 · 무손절 · 트레일 0.15%
추가 검증: 상위 1~2건 제외 PF / 반반검증 / 월별 분해 / 100%+ 대조군
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

TRAIL = 0.15


def wilson(k, n):
    if n == 0:
        return (0.0, 0.0)
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def pf(sel):
    g = sum(r["pnl"] for r in sel if r["pnl"] > 0)
    l = -sum(r["pnl"] for r in sel if r["pnl"] <= 0)
    if l <= 0:
        return None
    return g / l


def norm(d):
    try:
        d.index = d.index.tz_localize(None)
    except Exception:
        pass
    d.index = pd.to_datetime(d.index).normalize()
    return d[~d.index.duplicated(keep="last")]


def build():
    v = norm(yf.Ticker("^VIX").history(period="2y")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in ch.dropna().items()}

    df = yf.download("QQQ", period="2y", interval="1h", prepost=False,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df.index = df.index.tz_convert("America/New_York")
    df = df[(df.index.time >= dt.time(9, 30)) & (df.index.time < dt.time(16, 0))]

    days = sorted(set(df.index.date))
    rows = []
    pc = None
    for d in days:
        g = df[df.index.date == d]
        if len(g) < 5:
            if len(g):
                pc = float(g["Close"].iloc[-1])
            continue
        ds = str(d)
        O = [float(x) for x in g["Open"]]
        H = [float(x) for x in g["High"]]
        L = [float(x) for x in g["Low"]]
        C = [float(x) for x in g["Close"]]
        if pc:
            vx = vm.get(ds)
            if vx is None or abs(vx) < 5.0:
                gap = O[0] - pc
                gp = gap / pc * 100
                if 0.2 <= abs(gp) < 1.5:
                    sgn = 1 if gap > 0 else -1
                    cover = ((O[0] - C[0]) / gap) if sgn > 0 else ((C[0] - O[0]) / abs(gap))
                    ep = C[0]
                    tgt = pc
                    filled = False
                    ext = ep
                    res = None
                    pnl = None
                    mae = 0.0
                    for k in range(1, len(C)):
                        adv = (H[k] - ep) / ep * 100 if sgn > 0 else (ep - L[k]) / ep * 100
                        mae = max(mae, adv)
                        if not filled:
                            if (L[k] <= tgt) if sgn > 0 else (H[k] >= tgt):
                                filled = True
                                ext = min(L[k], tgt) if sgn > 0 else max(H[k], tgt)
                            continue
                        ext = min(ext, L[k]) if sgn > 0 else max(ext, H[k])
                        tp = ext * (1 + TRAIL / 100) if sgn > 0 else ext * (1 - TRAIL / 100)
                        if (H[k] >= tp) if sgn > 0 else (L[k] <= tp):
                            res = "TRAIL"
                            pnl = ((ep - tp) / ep * 100) if sgn > 0 else ((tp - ep) / ep * 100)
                            break
                    if res is None:
                        px = C[-1]
                        res = "EOD"
                        pnl = ((ep - px) / ep * 100) if sgn > 0 else ((px - ep) / ep * 100)
                    rows.append(dict(d=ds, cover=cover, pnl=pnl, filled=filled,
                                     mae=mae, gap=abs(gp), sgn=sgn))
        pc = C[-1]
    return rows


def block(out, sel, lab):
    n = len(sel)
    out.append("")
    out.append(f"### {lab}  n={n}")
    if n < 8:
        out.append("  표본부족")
        return
    w = sum(1 for r in sel if r["pnl"] > 0)
    ci = wilson(w, n)
    base = pf(sel)
    out.append(f"  승률 {w/n*100:.1f}%  CI {ci[0]:.1f}~{ci[1]:.1f}  "
               f"PF {'계산불능(패배0)' if base is None else format(base, '.2f')}  "
               f"평균 {np.mean([r['pnl'] for r in sel]):+.3f}%")

    # 상위 제외
    srt = sorted(sel, key=lambda r: r["pnl"], reverse=True)
    for cut in (1, 2):
        sub = srt[cut:]
        p = pf(sub)
        out.append(f"  상위 {cut}건 제외 PF "
                   f"{'계산불능' if p is None else format(p, '.2f')}  "
                   f"평균 {np.mean([r['pnl'] for r in sub]):+.3f}%")

    # 반반
    chrono = sorted(sel, key=lambda r: r["d"])
    half = len(chrono) // 2
    for lb, sub in (("전반", chrono[:half]), ("후반", chrono[half:])):
        p = pf(sub)
        ww = sum(1 for r in sub if r["pnl"] > 0)
        out.append(f"  {lb} n={len(sub):3d}  승률 {ww/len(sub)*100:5.1f}%  PF "
                   f"{'계산불능' if p is None else format(p, '.2f')}  "
                   f"기간 {sub[0]['d']}~{sub[-1]['d']}")

    # 최악 3건
    worst = sorted(sel, key=lambda r: r["pnl"])[:3]
    ws = " / ".join(f"{r['d']} {r['pnl']:+.3f}%" for r in worst)
    out.append(f"  최악3건 {ws}")

    # 월별
    mm = {}
    for r in sel:
        mm.setdefault(r["d"][:7], []).append(r)
    line = []
    for m in sorted(mm):
        s = mm[m]
        ww = sum(1 for r in s if r["pnl"] > 0)
        line.append(f"{m[2:]} {len(s)}건 {ww}승 {sum(r['pnl'] for r in s):+.2f}%")
    out.append("  월별: " + " | ".join(line))


def main():
    rows = build()
    out = [f"커버 40~100% 실행가능 구간 강건성 · 갭 0.2~1.5% · 무손절 · 트레일 {TRAIL}%",
           f"전체 갭일 n={len(rows)}  ({rows[0]['d']} ~ {rows[-1]['d']})"]

    exec_sel = [r for r in rows if 0.40 <= r["cover"] < 1.0]
    already = [r for r in rows if r["cover"] >= 1.0]
    allsel = [r for r in rows if r["cover"] >= 0.40]

    block(out, allsel, "40%+ 전체 (기존 근거)")
    block(out, exec_sel, "40~100% 실행가능 ★판정대상")
    block(out, already, "100%+ 진입시점 이미 갭필 (대조군)")

    # 방향별
    block(out, [r for r in exec_sel if r["sgn"] > 0], "40~100% 갭업(PUT)")
    block(out, [r for r in exec_sel if r["sgn"] < 0], "40~100% 갭다운(CALL)")

    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("covexec_result.json", "w"), ensure_ascii=False, indent=1)
