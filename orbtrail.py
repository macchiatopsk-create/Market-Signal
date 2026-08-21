"""orbtrail — ORB 재대결: 우리와 같은 청산 무기(트레일링)를 주고 폭 스윕.

형님 지적 반영: 이전 테스트는 ORB에 익절이 없었다 (OR극점 손절 or 14:00 종가뿐).
이번엔 진입 직후부터 유리한 극점 추적 → T% 되돌림에 청산. 봉내 순서 규율 동일
(직전 봉까지의 극점으로 먼저 판정 → 그 뒤 갱신). OR 반대극점은 재난 손절로 유지.

대상: ORB-B (스킵데이 = 09:45 커버<0.40, 갭 방향 진입) — 트랙 후보였던 쪽.
참고: ORB-A (논문원형, 전 갭일 09:35 첫봉 방향)도 같은 스윕.
비교 기준: 우리 갭필 전기준선 0.15 트레일 (같은 74일 mode A: PF 1.55, +5.6%).
옵션 P&L = BSM 세타 내장, IV=VXN×1.3, 스프레드 2.2%.
"""
import os, json, math, glob, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

try:
    from py_vollib.black_scholes import black_scholes as _bs
except Exception:
    _bs = None

COVER_MIN = 0.40
FINALCUT = dt.time(14, 0)
TRAILS = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.60]
ITM_PCT, SPREAD, RFR, K_IV = 0.50, 2.2, 0.045, 1.3
DATA = "data/1m"


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


def load_1m():
    frames = [pd.read_csv(f, compression="gzip")
              for f in sorted(glob.glob(f"{DATA}/QQQ_*.csv.gz"))]
    df = pd.concat(frames, ignore_index=True)
    df["t"] = pd.to_datetime(df["ts"])
    df = df.drop_duplicates(subset=["ts"]).sort_values("t").set_index("t")
    cnt = df.groupby(df.index.date).size()
    okd = set(cnt[cnt >= 320].index)
    return df[[d in okd for d in df.index.date]]


def hold_h(t, t0):
    return (dt.datetime.combine(dt.date(2000, 1, 1), t.time())
            - dt.datetime.combine(dt.date(2000, 1, 1), t0)).total_seconds() / 3600


def bsm_net(side, ep, exit_px, t0, hold, iv):
    K = round(ep * (1 - ITM_PCT / 100)) if side == "c" else round(ep * (1 + ITM_PCT / 100))
    t0h = t0.hour + t0.minute / 60
    tau0 = max(16.0 - t0h, 0.05) / 24 / 365
    tau1 = max(16.0 - t0h - hold, 0.02) / 24 / 365
    p0 = _bs(side, ep, K, tau0, RFR, iv)
    p1 = _bs(side, exit_px, K, tau1, RFR, iv)
    if p0 <= 0.01:
        return None
    return max((p1 - p0) / p0 * 100 - SPREAD, -100.0)


def run_mom(seq, t0, ep, dirn, stop, trail):
    """모멘텀 트레일: dirn=+1 롱(콜)/-1 숏(풋). ext=유리 극점(진입가로 초기화).
    봉내 순서 규율: 직전 봉까지의 ext로 트레일 판정 → 손절 판정 → ext 갱신."""
    ext = ep
    for (t, h, l, c) in seq:
        tp = ext * (1 - trail / 100) if dirn > 0 else ext * (1 + trail / 100)
        if (l <= tp) if dirn > 0 else (h >= tp):
            return tp, hold_h(t, t0)
        if (l <= stop) if dirn > 0 else (h >= stop):
            return stop, hold_h(t, t0)
        ext = max(ext, h) if dirn > 0 else min(ext, l)
        if t.time() >= FINALCUT:
            return c, hold_h(t, t0)
    t, _, _, c = seq[-1]
    return c, hold_h(t, t0)


def stat(rows):
    op = [r[0] for r in rows]
    n = len(op)
    if n == 0:
        return "n=0"
    w = sum(1 for x in op if x > 0)
    ci = wilson(w, n)
    g = sum(x for x in op if x > 0)
    l = -sum(x for x in op if x <= 0)
    pf = "패배0" if l <= 0 else f"{g/l:.2f}"
    return (f"승률 {w/n*100:5.1f}% (CI {ci[0]:4.1f}%)  PF {pf:>5s}  "
            f"옵션평균 {np.mean(op):+6.1f}%  보유 {np.mean([r[1] for r in rows]):.2f}h")


def main():
    if _bs is None:
        return ["py_vollib 미설치"]
    v = norm(yf.Ticker("^VIX").history(period="8mo")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in ch.dropna().items()}
    try:
        x = norm(yf.Ticker("^VXN").history(period="8mo")[["Open"]].dropna())
        ivm = {str(pd.Timestamp(k).date()): float(r) / 100 for k, r in x["Open"].items()}
    except Exception:
        ivm = {}
    vox = {str(pd.Timestamp(k).date()): float(r) for k, r in v["Open"].items()}
    dd = norm(yf.download("QQQ", period="8mo", interval="1d",
                          auto_adjust=False, progress=False))
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    closes = {pd.Timestamp(k).date(): float(r) for k, r in dd["Close"].items()}
    dl = sorted(closes.keys())
    prevc = {dl[i]: closes[dl[i - 1]] for i in range(1, len(dl))}

    df = load_1m()
    days = sorted(set(df.index.date))
    A, B = [], []
    for d in days:
        pc = prevc.get(d)
        if pc is None:
            continue
        vx = vm.get(str(d))
        if vx is not None and abs(vx) >= 5.0:
            continue
        g = df[df.index.date == d]
        b1 = [(t, float(r["High"]), float(r["Low"]), float(r["Close"])) for t, r in g.iterrows()]
        O0 = float(g["Open"].iloc[0])
        gap = O0 - pc
        gp = gap / pc * 100
        if not (0.2 <= abs(gp) < 1.5):
            continue
        sgn = 1 if gap > 0 else -1
        iv = (ivm.get(str(d)) or vox.get(str(d), 16.0) * 1.15 / 100) * K_IV
        or_hi = max(x[1] for x in b1[:5])
        or_lo = min(x[2] for x in b1[:5])
        # A: 논문원형 진입 (09:35, 첫봉 방향)
        c5 = b1[4][3]
        fdir = 1 if c5 > O0 else (-1 if c5 < O0 else 0)
        if fdir != 0 and len(b1) > 6:
            A.append(dict(dirn=fdir, ep=c5, t0=b1[4][0].time(),
                          stop=(or_lo if fdir > 0 else or_hi),
                          seq=b1[5:], iv=iv))
        # B: 스킵데이 진입 (09:45 커버<0.40, 갭 방향)
        if len(b1) > 16:
            ep15 = b1[14][3]
            cov15 = ((O0 - ep15) / gap) if sgn > 0 else ((ep15 - O0) / abs(gap))
            if cov15 < COVER_MIN:
                B.append(dict(dirn=sgn, ep=ep15, t0=b1[14][0].time(),
                              stop=(or_lo if sgn > 0 else or_hi),
                              seq=b1[15:], iv=iv))

    out = [f"orbtrail · 갭일 {len(days)}일 · ORB에 트레일 익절 장착 후 재대결",
           f"비교 기준: 우리 갭필 전기준선·0.15 (같은 데이터 mode A): PF 1.55 · +5.6% · n=35",
           ""]
    for tag, S in (("ORB-B 스킵데이 (트랙 후보)", B), ("ORB-A 논문원형 (대조군)", A)):
        out.append(f"[{tag}]  n={len(S)}")
        for T in TRAILS:
            rows = []
            for s in S:
                px, hold = run_mom(s["seq"], s["t0"], s["ep"], s["dirn"], s["stop"], T)
                side = "c" if s["dirn"] > 0 else "p"
                o = bsm_net(side, s["ep"], px, s["t0"], hold, s["iv"])
                if o is not None:
                    rows.append((o, hold))
            out.append(f"  트레일 {T:4.2f}%  {stat(rows)}")
        out.append("")
    out.append("※ 74일 한 레짐 · mode A(이상적 1분) · 275일 재검증 대상")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("orbtrail_result.json", "w"), ensure_ascii=False, indent=1)
