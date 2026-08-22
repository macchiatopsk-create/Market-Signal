"""orbvix — 스킵데이 모멘텀 + VIX 방향 확인 분해.

가설(형님): 갭을 못 메우는 날 갭 방향으로 갈 때, VIX가 갭 방향을 확인해주는
날만 골라내면 가짜 돌파가 걸러진다.
  확인 = 갭업 & VIX개장변화<0  또는  갭다운 & VIX개장변화>0
  역행 = 그 반대

진입 두 방식 × VIX 두 그룹 분해:
  B: 커버<0.40 → 09:45 종가 일괄진입 (갭 방향)
  C: 커버<0.40 → OR 극점 실돌파 진입 (11:30 데드라인)
청산: 트레일 스윕 + OR 반대극점 손절 + 14:00 컷. A/D 두 모드.
옵션 P&L = BSM 세타 내장, IV=VXN×1.3, 스프레드 2.2%.
"""
import json, math, glob, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

try:
    from py_vollib.black_scholes import black_scholes as _bs
except Exception:
    _bs = None

COVER_MIN = 0.40
ENTRY_DEADLINE = dt.time(11, 30)
FINALCUT = dt.time(14, 0)
TRAILS = [0.10, 0.20, 0.30, 0.40]
ITM_PCT, SPREAD, RFR, K_IV = 0.50, 2.2, 0.045, 1.3
DATA = "data/1m"


def wilson_lo(k, n):
    if n == 0:
        return 0.0
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0, c - h) * 100, 1)


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


def agg(bars, k):
    out = []
    for i in range(0, len(bars), k):
        ch = bars[i:i + k]
        if ch:
            out.append((ch[-1][0], max(x[1] for x in ch), min(x[2] for x in ch), ch[-1][3]))
    return out


def run_mom(seq, t0, ep, dirn, stop, trail):
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


def run_mom_app(seq5, t0, ep, dirn, stop, trail):
    ext = ep
    for (t, h, l, c) in seq5:
        ext = max(ext, h) if dirn > 0 else min(ext, l)
        tp = ext * (1 - trail / 100) if dirn > 0 else ext * (1 + trail / 100)
        if (c <= tp) if dirn > 0 else (c >= tp):
            return c, hold_h(t, t0)
        if (c <= stop) if dirn > 0 else (c >= stop):
            return c, hold_h(t, t0)
        if t.time() >= FINALCUT:
            return c, hold_h(t, t0)
    t, _, _, c = seq5[-1]
    return c, hold_h(t, t0)


def stat(rows):
    op = [r[0] for r in rows]
    n = len(op)
    if n == 0:
        return "n= 0"
    w = sum(1 for x in op if x > 0)
    g = sum(x for x in op if x > 0)
    l = -sum(x for x in op if x <= 0)
    pf = "승만" if l <= 0 else f"{g/l:.2f}"
    srt = sorted(op, reverse=True)
    op2 = srt[2:] if n > 4 else srt
    g2 = sum(x for x in op2 if x > 0)
    l2 = -sum(x for x in op2 if x <= 0)
    pf2 = "승만" if l2 <= 0 else f"{g2/l2:.2f}"
    return (f"n={n:2d}  승률 {w/n*100:5.1f}% (CI {wilson_lo(w, n):4.1f}%)  "
            f"PF {pf:>5s}  상위2제외 {pf2:>5s}  옵션평균 {np.mean(op):+6.1f}%")


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
    B, Cc = [], []
    for d in days:
        pc = prevc.get(d)
        if pc is None:
            continue
        vx = vm.get(str(d))
        if vx is None or abs(vx) >= 5.0:
            continue
        g = df[df.index.date == d]
        b1 = [(t, float(r["High"]), float(r["Low"]), float(r["Close"])) for t, r in g.iterrows()]
        if len(b1) < 20:
            continue
        O0 = float(g["Open"].iloc[0])
        gap = O0 - pc
        gp = gap / pc * 100
        if not (0.2 <= abs(gp) < 1.5):
            continue
        sgn = 1 if gap > 0 else -1
        ep15 = b1[14][3]
        cov15 = ((O0 - ep15) / gap) if sgn > 0 else ((ep15 - O0) / abs(gap))
        if cov15 >= COVER_MIN:
            continue
        conf = (sgn > 0 and vx < 0) or (sgn < 0 and vx > 0)  # VIX가 갭 방향 확인
        iv = (ivm.get(str(d)) or vox.get(str(d), 16.0) * 1.15 / 100) * K_IV
        or_hi = max(x[1] for x in b1[:5])
        or_lo = min(x[2] for x in b1[:5])
        stop = or_lo if sgn > 0 else or_hi
        base = dict(dirn=sgn, stop=stop, iv=iv, conf=conf)
        B.append(dict(base, ep=ep15, t0=b1[14][0].time(),
                      seq=b1[15:], seq5=agg(b1[15:], 5)))
        brk = or_hi if sgn > 0 else or_lo
        ep = t0 = idx = None
        if (ep15 >= brk) if sgn > 0 else (ep15 <= brk):
            ep, t0, idx = ep15, b1[14][0].time(), 15
        else:
            for j in range(15, len(b1)):
                t, h, l, c = b1[j]
                if t.time() >= ENTRY_DEADLINE:
                    break
                if (h >= brk) if sgn > 0 else (l <= brk):
                    ep, t0, idx = brk, t.time(), j + 1
                    break
        if ep is not None:
            Cc.append(dict(base, ep=ep, t0=t0, seq=b1[idx:], seq5=agg(b1[idx:], 5)))

    def block(tag, S, runner, key):
        out = [f"[{tag}]"]
        for grp, lab in [(True, "VIX확인"), (False, "VIX역행")]:
            sub = [s for s in S if s["conf"] == grp]
            out.append(f" {lab} ({len(sub)}건)")
            for T in TRAILS:
                rows = []
                for s in sub:
                    px, hold = runner(s[key], s["t0"], s["ep"], s["dirn"], s["stop"], T)
                    o = bsm_net("c" if s["dirn"] > 0 else "p",
                                s["ep"], px, s["t0"], hold, s["iv"])
                    if o is not None:
                        rows.append((o, hold))
                out.append(f"   트레일 {T:4.2f}%  {stat(rows)}")
        return out

    nb_c = sum(1 for s in B if s["conf"])
    out = [f"orbvix · 스킵데이 {len(B)}일 = VIX확인 {nb_c} / 역행 {len(B)-nb_c}",
           "확인 = 갭업&VIX개장↓ 또는 갭다운&VIX개장↑ (개장변화 부호 기준)", ""]
    out += block("B진입 09:45 일괄 · A모드", B, run_mom, "seq") + [""]
    out += block("B진입 09:45 일괄 · D모드(앱)", B, run_mom_app, "seq5") + [""]
    out += block("C진입 OR돌파 · A모드", Cc, run_mom, "seq") + [""]
    out += block("C진입 OR돌파 · D모드(앱)", Cc, run_mom_app, "seq5") + [""]
    out.append("기준: 상위2제외 PF>1.2 · 이웃 있는 봉우리만 실체 · n<15는 참고만")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("orbvix_result.json", "w"), ensure_ascii=False, indent=1)
