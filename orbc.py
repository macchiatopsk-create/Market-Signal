"""orbc — 진짜 ORB 결합형: 스킵데이 조건 + OR 극점 실제 돌파 진입.

진입 (갭업 기준, 갭다운은 대칭):
  1) 갭일 (0.2~1.5%, 개장VIX|x|<5%) 이고 09:45 커버 < 0.40   ← 우리 스킵 조건
  2) 09:45 이후 가격이 OR 고가(첫 5분 고가)를 실제로 깨는 순간 진입
     - 09:45에 이미 OR 고가 위면 09:45 종가에 즉시 진입 (돌파 기확인)
     - 아니면 1분봉 고가가 OR 고가 도달 시 그 레벨에 진입
  3) 11:30까지 돌파 없으면 무거래 (필터가 거른 것)
청산: 진입 직후부터 트레일 (폭 스윕) + OR 반대극점 재난손절 + 14:00 컷
모드: A(1분 이상적) / D(앱 5분 wake·종가) · 상위2건 제외 PF 포함
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
ENTRY_DEADLINE = dt.time(11, 30)
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


def agg(bars, k):
    out = []
    for i in range(0, len(bars), k):
        ch = bars[i:i + k]
        if ch:
            out.append((ch[-1][0], max(x[1] for x in ch), min(x[2] for x in ch), ch[-1][3]))
    return out


def run_mom(seq, t0, ep, dirn, stop, trail):
    """A모드: 직전 봉까지 ext로 트레일 판정 → 손절 → ext 갱신."""
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
    """D모드: 5분 wake, 극점=봉 H/L(유효), 판정·체결=종가."""
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
        return "n=0"
    w = sum(1 for x in op if x > 0)
    ci = wilson(w, n)
    g = sum(x for x in op if x > 0)
    l = -sum(x for x in op if x <= 0)
    pf = "패배0" if l <= 0 else f"{g/l:.2f}"
    srt = sorted(op, reverse=True)
    op2 = srt[2:] if n > 4 else srt
    g2 = sum(x for x in op2 if x > 0)
    l2 = -sum(x for x in op2 if x <= 0)
    pf2 = "패배0" if l2 <= 0 else f"{g2/l2:.2f}"
    return (f"n={n:2d}  승률 {w/n*100:5.1f}% (CI {ci[0]:4.1f}%)  PF {pf:>5s}  "
            f"상위2제외 {pf2:>5s}  옵션평균 {np.mean(op):+6.1f}%  "
            f"보유 {np.mean([r[1] for r in rows]):.2f}h")


def main():
    if _bs is None:
        return ["py_vollib 미설치"]
    v = norm(yf.Ticker("^VIX").history(period="26mo")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in ch.dropna().items()}
    try:
        x = norm(yf.Ticker("^VXN").history(period="26mo")[["Open"]].dropna())
        ivm = {str(pd.Timestamp(k).date()): float(r) / 100 for k, r in x["Open"].items()}
    except Exception:
        ivm = {}
    vox = {str(pd.Timestamp(k).date()): float(r) for k, r in v["Open"].items()}
    dd = norm(yf.download("QQQ", period="26mo", interval="1d",
                          auto_adjust=False, progress=False))
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    closes = {pd.Timestamp(k).date(): float(r) for k, r in dd["Close"].items()}
    dl = sorted(closes.keys())
    prevc = {dl[i]: closes[dl[i - 1]] for i in range(1, len(dl))}

    df = load_1m()
    days = sorted(set(df.index.date))
    C, n_skip, n_imm, n_wait, n_none = [], 0, 0, 0, 0
    for d in days:
        pc = prevc.get(d)
        if pc is None:
            continue
        vx = vm.get(str(d))
        if vx is not None and abs(vx) >= 5.0:
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
        n_skip += 1
        iv = (ivm.get(str(d)) or vox.get(str(d), 16.0) * 1.15 / 100) * K_IV
        or_hi = max(x[1] for x in b1[:5])
        or_lo = min(x[2] for x in b1[:5])
        brk = or_hi if sgn > 0 else or_lo
        stop = or_lo if sgn > 0 else or_hi
        # 돌파 탐색: 09:45 이후 ~ 11:30
        ep = t0 = idx = None
        if (ep15 >= brk) if sgn > 0 else (ep15 <= brk):
            ep, t0, idx = ep15, b1[14][0].time(), 15
            n_imm += 1
        else:
            for j in range(15, len(b1)):
                t, h, l, c = b1[j]
                if t.time() >= ENTRY_DEADLINE:
                    break
                if (h >= brk) if sgn > 0 else (l <= brk):
                    ep, t0, idx = brk, t.time(), j + 1
                    n_wait += 1
                    break
        if ep is None:
            n_none += 1
            continue
        C.append(dict(dirn=sgn, ep=ep, t0=t0, stop=stop, iv=iv,
                      seq=b1[idx:], seq5=agg(b1[idx:], 5)))

    out = [f"orbc · 갭일 {len(days)}일 · 스킵데이 {n_skip}일 중 "
           f"돌파진입 {len(C)}건 (즉시 {n_imm} / 대기돌파 {n_wait} / 무돌파 {n_none})",
           "진입 = 커버<0.40 확인 + OR 극점 실돌파 · 11:30 데드라인 · 14:00 컷", ""]
    out.append("[A모드 — 1분 이상적]")
    for T in TRAILS:
        rows = []
        for s in C:
            px, hold = run_mom(s["seq"], s["t0"], s["ep"], s["dirn"], s["stop"], T)
            o = bsm_net("c" if s["dirn"] > 0 else "p", s["ep"], px, s["t0"], hold, s["iv"])
            if o is not None:
                rows.append((o, hold))
        out.append(f"  트레일 {T:4.2f}%  {stat(rows)}")
    out.append("")
    out.append("[D모드 — 앱 5분 wake·종가 체결]")
    for T in TRAILS:
        rows = []
        for s in C:
            px, hold = run_mom_app(s["seq5"], s["t0"], s["ep"], s["dirn"], s["stop"], T)
            o = bsm_net("c" if s["dirn"] > 0 else "p", s["ep"], px, s["t0"], hold, s["iv"])
            if o is not None:
                rows.append((o, hold))
        out.append(f"  트레일 {T:4.2f}%  {stat(rows)}")
    out.append("")
    out.append("비교: ORB-B(돌파 미확인 09:45 일괄진입) 최고 PF 1.76/상위2제외 1.16 · "
               "우리 갭필 PF 1.55(+5.6%)")
    out.append("※ 한 레짐 · 275일 재검증 대상")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("orbc_result.json", "w"), ensure_ascii=False, indent=1)
