"""combsim — 통합 계좌 백테스트: 한 북으로 갭필+모멘텀 (기준선 3개).

기준선 t* ∈ {09:35, 09:45, 10:30} 에서 커버 한 번 판정:
  커버 ≥ 0.40            → 갭필: t* 종가에 되돌림 방향 진입,
                           타깃=전일종가, 필 후 트레일 0.15%, 11:30 미필 청산, 14:00 컷
  커버 < 0.40 + VIX확인   → 모멘텀: t* 종가에 갭 방향 진입,
                           트레일 0.30%, OR(첫5분) 반대극점 손절, 14:00 컷
  커버 < 0.40 + VIX역행   → 관망
우주: 갭 0.2~1.5% · 개장VIX |x|<5%. 상호배타라 한 북에 하루 최대 1거래.
모드 A(1분 이상적) / D(앱 5분 wake·종가 체결). 옵션 = BSM 세타 내장.
결합 성과 + 50% 사이징 복리 곡선(잔고·MDD)까지.
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
TIMECUT = dt.time(11, 30)
FINALCUT = dt.time(14, 0)
FILL_TRAIL, MOM_TRAIL = 0.15, 0.30
E1M = {"09:35": 4, "09:45": 14, "10:30": 59}     # 기준선 → 1분봉 종가 인덱스
ITM_PCT, SPREAD, RFR, K_IV = 0.50, 2.2, 0.045, 1.3
SIZE_F = 0.50
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


# ── 갭필 실행 (pollsim 검증 로직) ──
def fill_A(seq, t0, tgt, sgn, trail):
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


def fill_D(seq5, t0, tgt, sgn, trail):
    filled, ext = False, None
    for (t, h, l, c) in seq5:
        if not filled:
            if (l <= tgt) if sgn > 0 else (h >= tgt):
                filled = True
                ext = min(l, tgt) if sgn > 0 else max(h, tgt)
                tp = ext * (1 + trail / 100) if sgn > 0 else ext * (1 - trail / 100)
                if (c >= tp) if sgn > 0 else (c <= tp):
                    return c, hold_h(t, t0)
                continue
            if t.time() >= TIMECUT:
                return c, hold_h(t, t0)
            continue
        ext = min(ext, l) if sgn > 0 else max(ext, h)
        tp = ext * (1 + trail / 100) if sgn > 0 else ext * (1 - trail / 100)
        if (c >= tp) if sgn > 0 else (c <= tp):
            return c, hold_h(t, t0)
        if t.time() >= FINALCUT:
            return c, hold_h(t, t0)
    t, _, _, c = seq5[-1]
    return c, hold_h(t, t0)


# ── 모멘텀 실행 (orbvix 검증 로직) ──
def mom_A(seq, t0, ep, dirn, stop, trail):
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


def mom_D(seq5, t0, ep, dirn, stop, trail):
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


def bsm_net(flag, ep, exit_px, t0, hold, iv):
    K = round(ep * (1 - ITM_PCT / 100)) if flag == "c" else round(ep * (1 + ITM_PCT / 100))
    t0h = t0.hour + t0.minute / 60
    tau0 = max(16.0 - t0h, 0.05) / 24 / 365
    tau1 = max(16.0 - t0h - hold, 0.02) / 24 / 365
    p0 = _bs(flag, ep, K, tau0, RFR, iv)
    p1 = _bs(flag, exit_px, K, tau1, RFR, iv)
    if p0 <= 0.01:
        return None
    return max((p1 - p0) / p0 * 100 - SPREAD, -100.0)


def stat(seq):
    op = [x[1] for x in seq]
    n = len(op)
    if n == 0:
        return "n= 0"
    w = sum(1 for x in op if x > 0)
    g = sum(x for x in op if x > 0)
    l = -sum(x for x in op if x <= 0)
    pf = "승만" if l <= 0 else f"{g/l:5.2f}"
    srt = sorted(op, reverse=True)
    o2 = srt[2:] if n > 4 else srt
    g2 = sum(x for x in o2 if x > 0)
    l2 = -sum(x for x in o2 if x <= 0)
    pf2 = "승만" if l2 <= 0 else f"{g2/l2:5.2f}"
    cap, peak, mdd = 1.0, 1.0, 0.0
    for _, p in sorted(seq):
        cap *= (1 + SIZE_F * p / 100)
        peak = max(peak, cap)
        mdd = max(mdd, (peak - cap) / peak)
    return (f"n={n:2d} 승률 {w/n*100:5.1f}%(CI {wilson_lo(w,n):4.1f}) PF {pf} "
            f"상위2외 {pf2} 평균 {np.mean(op):+6.1f}% │ 50%복리 ${2000*cap:6,.0f} MDD {mdd*100:4.1f}%")


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
    out = [f"combsim · 데이터 {len(days)}일 · 한 북 = 커버≥{COVER_MIN} 갭필(트레일 {FILL_TRAIL})"
           f" / <{COVER_MIN}+VIX확인 모멘텀(트레일 {MOM_TRAIL}) / 역행 관망", ""]
    for bl, ei in E1M.items():
        res = {m: dict(F=[], M=[]) for m in ("A", "D")}
        n_veto = 0
        for d in days:
            pc = prevc.get(d)
            vx = vm.get(str(d))
            if pc is None or vx is None or abs(vx) >= 5.0:
                continue
            g = df[df.index.date == d]
            b1 = [(t, float(r["High"]), float(r["Low"]), float(r["Close"]))
                  for t, r in g.iterrows()]
            if len(b1) <= ei + 2:
                continue
            O0 = float(g["Open"].iloc[0])
            gap = O0 - pc
            gp = gap / pc * 100
            if not (0.2 <= abs(gp) < 1.5):
                continue
            sgn = 1 if gap > 0 else -1
            ep = b1[ei][3]
            t0 = b1[ei][0].time()
            cov = ((O0 - ep) / gap) if sgn > 0 else ((ep - O0) / abs(gap))
            iv = (ivm.get(str(d)) or vox.get(str(d), 16.0) * 1.15 / 100) * K_IV
            seqA, seqD = b1[ei + 1:], agg(b1[ei + 1:], 5)
            if cov >= COVER_MIN:                       # 갭필 (되돌림)
                flag = "p" if sgn > 0 else "c"
                for m, run, sq in [("A", fill_A, seqA), ("D", fill_D, seqD)]:
                    px, hd = run(sq, t0, pc, sgn, FILL_TRAIL)
                    o = bsm_net(flag, ep, px, t0, hd, iv)
                    if o is not None:
                        res[m]["F"].append((str(d), o))
            else:
                conf = (sgn > 0 and vx < 0) or (sgn < 0 and vx > 0)
                if not conf:
                    n_veto += 1
                    continue
                or_hi = max(x[1] for x in b1[:5])
                or_lo = min(x[2] for x in b1[:5])
                stop = or_lo if sgn > 0 else or_hi
                flag = "c" if sgn > 0 else "p"
                for m, run, sq in [("A", mom_A, seqA), ("D", mom_D, seqD)]:
                    px, hd = run(sq, t0, ep, sgn, stop, MOM_TRAIL)
                    o = bsm_net(flag, ep, px, t0, hd, iv)
                    if o is not None:
                        res[m]["M"].append((str(d), o))
        out.append(f"[기준선 {bl}]  관망(VIX역행) {n_veto}일")
        for m in ("A", "D"):
            F, M = res[m]["F"], res[m]["M"]
            out.append(f" {m}모드 갭필만   {stat(F)}")
            out.append(f" {m}모드 모멘텀만  {stat(M)}")
            out.append(f" {m}모드 ★결합    {stat(F + M)}")
        out.append("")
    out.append("※ 74일 한 레짐 · 결합의 가치 = 거래일 확대 + 곡선 평탄화 여부. 275일 재심 대상")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("combsim_result.json", "w"), ensure_ascii=False, indent=1)
