"""pollsim — 트레일 0.10 vs 0.15, 앱의 실제 실행 조건으로 비교.

모드 3개 (같은 29건 거래):
  A 이상적   1분봉 판정, 트레일 가격(tp)에 체결          — minres2 표와 동일 (상한)
  B 5분낙관  5분봉 H/L 판정, tp 체결                    — 기존 5m 표 (봉내 look-ahead 낙관)
  C 폴링현실 5분마다 종가만 보고 판정, 그 종가에 체결      — 크론 앱이 실제로 하는 것
             (갭필 감지도 종가로만 — 앱은 봉 저가를 못 봄)

출력: 트레일 0.05~0.30 스윕 × 3모드 + 0.10 vs 0.15 짝지은 차이(같은 거래끼리).
옵션 P&L = minres2와 동일 BSM(세타 내장), IV=VXN×1.3, 스프레드 2.2%.
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
TIMECUT = dt.time(11, 30)
FINALCUT = dt.time(14, 0)
BASE_IDX = {"5m": 1, "15m": 2, "1h": 11}
TRAILS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
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


def agg(bars, k):
    out = []
    for i in range(0, len(bars), k):
        ch = bars[i:i + k]
        if ch:
            out.append((ch[-1][0], max(x[1] for x in ch), min(x[2] for x in ch), ch[-1][3]))
    return out


def hold_h(t, t0):
    return (dt.datetime.combine(dt.date(2000, 1, 1), t.time())
            - dt.datetime.combine(dt.date(2000, 1, 1), t0)).total_seconds() / 3600


def run_hl(seq, t0, ep, tgt, sgn, trail):
    """H/L 판정, tp 체결 (모드 A=1분seq, B=5분seq)."""
    filled, ext = False, None
    for (t, h, l, c) in seq:
        if not filled:
            if (l <= tgt) if sgn > 0 else (h >= tgt):
                filled = True
                ext = min(l, tgt) if sgn > 0 else max(h, tgt)
                continue
            if t.time() >= TIMECUT:
                return c, hold_h(t, t0)
            continue
        tp = ext * (1 + trail / 100) if sgn > 0 else ext * (1 - trail / 100)
        if (h >= tp) if sgn > 0 else (l <= tp):
            return tp, hold_h(t, t0)
        ext = min(ext, l) if sgn > 0 else max(ext, h)
        if t.time() >= FINALCUT:
            return c, hold_h(t, t0)
    t, _, _, c = seq[-1]
    return c, hold_h(t, t0)


def run_poll(seq5, t0, ep, tgt, sgn, trail):
    """폴링 현실: 5분 간격 종가만 관측, 판정·체결 모두 그 종가 (모드 C)."""
    filled, ext = False, None
    for (t, _, _, c) in seq5:
        if not filled:
            if (c <= tgt) if sgn > 0 else (c >= tgt):
                filled = True
                ext = c
                continue
            if t.time() >= TIMECUT:
                return c, hold_h(t, t0)
            continue
        tp = ext * (1 + trail / 100) if sgn > 0 else ext * (1 - trail / 100)
        if (c >= tp) if sgn > 0 else (c <= tp):
            return c, hold_h(t, t0)
        ext = min(ext, c) if sgn > 0 else max(ext, c)
        if t.time() >= FINALCUT:
            return c, hold_h(t, t0)
    t, _, _, c = seq5[-1]
    return c, hold_h(t, t0)


def bsm_net(sgn, ep, exit_px, t0, hold, iv):
    flag = "p" if sgn > 0 else "c"
    K = round(ep * (1 + ITM_PCT / 100)) if sgn > 0 else round(ep * (1 - ITM_PCT / 100))
    t0h = t0.hour + t0.minute / 60
    tau0 = max(16.0 - t0h, 0.05) / 24 / 365
    tau1 = max(16.0 - t0h - hold, 0.02) / 24 / 365
    p0 = _bs(flag, ep, K, tau0, RFR, iv)
    p1 = _bs(flag, exit_px, K, tau1, RFR, iv)
    if p0 <= 0.01:
        return None
    return max((p1 - p0) / p0 * 100 - SPREAD, -100.0)


def main():
    if _bs is None:
        return ["py_vollib 미설치"]
    v = norm(yf.Ticker("^VIX").history(period="7mo")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in ch.dropna().items()}
    try:
        x = norm(yf.Ticker("^VXN").history(period="7mo")[["Open"]].dropna())
        ivm = {str(pd.Timestamp(k).date()): float(r) / 100 for k, r in x["Open"].items()}
    except Exception:
        ivm = {}
    vox = {str(pd.Timestamp(k).date()): float(r) for k, r in v["Open"].items()}

    dd = norm(yf.download("QQQ", period="7mo", interval="1d",
                          auto_adjust=False, progress=False))
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    closes = {pd.Timestamp(k).date(): float(r) for k, r in dd["Close"].items()}
    dl = sorted(closes.keys())
    prevc = {dl[i]: closes[dl[i - 1]] for i in range(1, len(dl))}

    df = load_1m()
    days = sorted(set(df.index.date))

    trades = []
    for d in days:
        pc = prevc.get(d)
        if pc is None:
            continue
        vx = vm.get(str(d))
        if vx is not None and abs(vx) >= 5.0:
            continue
        g = df[df.index.date == d]
        b1 = [(t, float(r["High"]), float(r["Low"]), float(r["Close"])) for t, r in g.iterrows()]
        b5 = agg(b1, 5)
        O0 = float(g["Open"].iloc[0])
        gap = O0 - pc
        gp = gap / pc * 100
        if not (0.2 <= abs(gp) < 1.5):
            continue
        sgn = 1 if gap > 0 else -1
        iv = (ivm.get(str(d)) or vox.get(str(d), 16.0) * 1.15 / 100) * K_IV
        for bl, i5 in BASE_IDX.items():
            if i5 >= len(b5) - 2:
                continue
            ep = b5[i5][3]
            cov = ((O0 - ep) / gap) if sgn > 0 else ((ep - O0) / abs(gap))
            if not (COVER_MIN <= cov < 1.0):
                continue
            t_e = b5[i5][0]
            trades.append(dict(d=str(d), sgn=sgn, ep=ep, tgt=pc, t0=t_e.time(), iv=iv,
                               tail1=[x for x in b1 if x[0] > t_e],
                               tail5=[x for x in b5 if x[0] > t_e]))

    out = [f"pollsim · {len(days)}거래일 · 거래 {len(trades)}건 · IV=VXN×{K_IV} · BSM 세타 내장",
           "A=1분판정·tp체결(이상)  B=5분H/L·tp체결(낙관)  C=5분 종가만·종가체결(앱 현실)", ""]

    modes = {
        "A": lambda t, T: run_hl(t["tail1"], t["t0"], t["ep"], t["tgt"], t["sgn"], T),
        "B": lambda t, T: run_hl(t["tail5"], t["t0"], t["ep"], t["tgt"], t["sgn"], T),
        "C": lambda t, T: run_poll(t["tail5"], t["t0"], t["ep"], t["tgt"], t["sgn"], T),
    }
    ops = {}
    for mk, fn in modes.items():
        out.append(f"[{mk}] {'트레일':>6s} {'승률':>7s} {'CI하한':>7s} {'PF':>7s} "
                   f"{'옵션평균':>8s} {'평균보유':>7s}")
        for T in TRAILS:
            col = []
            hd = []
            for t in trades:
                px, hold = fn(t, T)
                pass
                o = bsm_net(t["sgn"], t["ep"], px, t["t0"], hold, t["iv"])
                if o is None:
                    continue
                col.append(o)
                hd.append(hold)
            ops[(mk, T)] = col
            n = len(col)
            w = sum(1 for x in col if x > 0)
            ci = wilson(w, n)
            gp_ = sum(x for x in col if x > 0)
            ln = -sum(x for x in col if x <= 0)
            pfs = "패배0" if ln <= 0 else f"{gp_/ln:.2f}"
            out.append(f"    {T:6.2f}% {w/n*100:6.1f}% {ci[0]:6.1f}% {pfs:>7s} "
                       f"{np.mean(col):+7.1f}% {np.mean(hd):6.2f}h")
        out.append("")

    out.append("=== 0.10 vs 0.15 짝지은 비교 (같은 거래끼리 차이) ===")
    for mk in modes:
        a, b = ops[(mk, 0.10)], ops[(mk, 0.15)]
        n = min(len(a), len(b))
        dif = [a[i] - b[i] for i in range(n)]
        w10 = sum(1 for x in dif if x > 0.01)
        w15 = sum(1 for x in dif if x < -0.01)
        tie = n - w10 - w15
        se = np.std(dif, ddof=1) / math.sqrt(n) if n > 1 else 0
        out.append(f"  [{mk}] 평균차 {np.mean(dif):+.1f}%p (±{1.96*se:.1f}) · "
                   f"0.10우세 {w10} / 동률 {tie} / 0.15우세 {w15}")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("pollsim_result.json", "w"), ensure_ascii=False, indent=1)
