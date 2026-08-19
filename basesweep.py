"""기준선 시각 스윕 — 5분봉 인덱스 0~11 (09:35 ~ 10:30) 전 구간.
각 기준선에서 '실행가능'(커버>=0.40 AND 커버<1.0) 갭일이 몇 건인지 센다.
표본이 작아 승률/PF는 산출하지 않는다. 건수만.
  대상: 갭 0.2~1.5%, 개장 VIX 변화 |x|<5%
"""
import json, datetime as dt, traceback
import yfinance as yf
import pandas as pd

COVER_MIN = 0.40


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
    IDX = list(range(12))
    label = {i: (dt.datetime(2000, 1, 1, 9, 35) + dt.timedelta(minutes=5 * i)).strftime("%H:%M")
             for i in IDX}

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
        C = [float(x) for x in g["Close"]]
        if pc:
            vx = vm.get(ds)
            if vx is None or abs(vx) < 5.0:
                gap = O[0] - pc
                gp = gap / pc * 100
                if 0.2 <= abs(gp) < 1.5:
                    sgn = 1 if gap > 0 else -1
                    cov = {}
                    for i in IDX:
                        if i < len(C):
                            cl = C[i]
                            cov[i] = ((O[0] - cl) / gap) if sgn > 0 else ((cl - O[0]) / abs(gap))
                        else:
                            cov[i] = None
                    # 종가 기준 최종 갭필 여부 (참고용)
                    lo = min(C)
                    hi = max(C)
                    fill = (lo <= pc) if sgn > 0 else (hi >= pc)
                    recs.append(dict(d=ds, gap=round(abs(gp), 3), sgn=sgn, cov=cov, fill=fill))
        pc = C[-1]

    nd = len(days)
    ann = 252.0 / nd
    out = [f"QQQ 5분봉 {nd}거래일 ({days[0]} ~ {days[-1]}) · 대상 갭일 {len(recs)}건",
           f"실행가능 = 커버 >= {COVER_MIN:.2f} AND 커버 < 1.00 (진입시점 미필)",
           "※ 표본이 작아 건수만 — 승률/PF 산출 불가", ""]
    out.append(f"  {'기준선':7s} {'봉idx':>5s} {'커버40%+':>8s} {'이미필':>6s} "
               f"{'실행가능':>8s} {'연환산':>6s} {'그중 최종갭필':>12s}")
    for i in IDX:
        ok = [r for r in recs if r["cov"][i] is not None and r["cov"][i] >= COVER_MIN]
        already = [r for r in ok if r["cov"][i] >= 1.0]
        exe = [r for r in ok if r["cov"][i] < 1.0]
        fl = sum(1 for r in exe if r["fill"])
        fr = f"{fl}/{len(exe)} ({fl/len(exe)*100:.0f}%)" if exe else "—"
        mk = ""
        if i == 0: mk = "  ← 5분(라벨)"
        if i == 1: mk = "  ← 5분(현재 앱 idx=1)"
        if i == 2: mk = "  ← 15분"
        if i == 11: mk = "  ← 1시간"
        out.append(f"  {label[i]:7s} {i:5d} {len(ok):8d} {len(already):6d} "
                   f"{len(exe):8d} {len(exe)*ann:6.0f} {fr:>12s}{mk}")

    # 커버 임계 민감도
    out.append("")
    out.append("[커버 임계 민감도 — 실행가능 건수]")
    ths = [0.30, 0.35, 0.40, 0.45, 0.50]
    out.append("  기준선   " + "".join(f"{t*100:.0f}%".rjust(8) for t in ths))
    for i in IDX:
        cells = ""
        for t in ths:
            e = [r for r in recs if r["cov"][i] is not None and t <= r["cov"][i] < 1.0]
            cells += str(len(e)).rjust(8)
        out.append(f"  {label[i]:7s}" + cells)

    # 합집합 / 중복
    out.append("")
    out.append("[조합 커버리지 — 서로 다른 날을 잡는가]")
    def se(i):
        return set(r["d"] for r in recs
                   if r["cov"][i] is not None and COVER_MIN <= r["cov"][i] < 1.0)
    for a, b in [(0, 2), (0, 11), (2, 11), (1, 11)]:
        A, B = se(a), se(b)
        out.append(f"  {label[a]} ∪ {label[b]}: 합집합 {len(A|B)}건 "
                   f"(교집합 {len(A&B)} · {label[a]}만 {len(A-B)} · {label[b]}만 {len(B-A)})")
    ALL = set()
    for i in IDX:
        ALL |= se(i)
    out.append(f"  전 기준선 합집합 {len(ALL)}건 / 대상 갭일 {len(recs)}건 "
               f"→ 연환산 {len(ALL)*ann:.0f}건")

    out.append("")
    out.append("[개별 갭일 커버 추이 09:35→10:30]")
    for r in recs:
        s = " ".join(("  --" if r["cov"][i] is None else f"{r['cov'][i]:5.2f}") for i in IDX)
        out.append(f"  {r['d']} {r['gap']:+.2f}%{'↑' if r['sgn']>0 else '↓'} "
                   f"{'필' if r['fill'] else '미'} {s}")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("basesweep_result.json", "w"), ensure_ascii=False, indent=1)
