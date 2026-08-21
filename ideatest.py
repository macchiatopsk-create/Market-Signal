"""ideatest — 같은 58 갭일 위에서 3파전: 우리 갭필 vs ORB(스킵데이) vs VWAP트렌드.

[기준] 우리 갭필 — pollsim과 동일 (A=이상적 1분, D=앱 재현), 트레일 0.15%
[1] ORB 스킵데이 수확 (실행가능 버전 — look-ahead 없음):
      09:40에 5분기준선 커버 < 0.40 확인 (갭이 안 되돌아옴 = 모멘텀 유지)
      → 갭 방향으로 진입 (갭업=콜, 갭다운=풋). 09:39 종가 체결
      손절 없음(프리미엄 캡) · 청산 11:30 vs 14:00 두 변형
      같은 날 나중에 우리 갭필 진입이 뜨는 '충돌일'은 따로 집계
[2] VWAP 트렌드 (논문 SSRN 4631351 각색):
      5분 종가가 VWAP 위→콜 / 아래→풋, 교차 때마다 플립, 14:00 청산
      플립마다 스프레드 2.2% 지불 (현실 비용)
옵션 P&L 전부 BSM(세타 내장) · IV=VXN×1.3 · ITM 0.5%
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
ITM_PCT, SPREAD, RFR, K_IV, TRAIL = 0.50, 2.2, 0.045, 1.3, 0.15
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


def bsm_pl(flag, ep, exit_px, t0, hold, iv):
    """진입가 ep 기준 ITM 0.5% 스트라이크, BSM 재평가 순손익% (스프레드 차감, 하한 -100)."""
    K = round(ep * (1 - ITM_PCT / 100)) if flag == "c" else round(ep * (1 + ITM_PCT / 100))
    t0h = t0.hour + t0.minute / 60
    tau0 = max(16.0 - t0h, 0.05) / 24 / 365
    tau1 = max(16.0 - t0h - hold, 0.02) / 24 / 365
    p0 = _bs(flag, ep, K, tau0, RFR, iv)
    p1 = _bs(flag, exit_px, K, tau1, RFR, iv)
    if p0 <= 0.01:
        return None
    return max((p1 - p0) / p0 * 100 - SPREAD, -100.0)


def run_gap_1m(seq, t0, ep, tgt, sgn, trail):
    """우리 갭필 모드 A (1분 이상적)."""
    filled, ext = False, None
    for (t, h, l, c) in seq:
        if not filled:
            if (l <= tgt) if sgn > 0 else (h >= tgt):
                filled, ext = True, (min(l, tgt) if sgn > 0 else max(h, tgt))
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


def run_gap_app(seq5, t0, ep, tgt, sgn, trail):
    """우리 갭필 모드 D (앱 재현)."""
    filled, ext = False, None
    for (t, h, l, c) in seq5:
        if not filled:
            if (l <= tgt) if sgn > 0 else (h >= tgt):
                filled, ext = True, (min(l, tgt) if sgn > 0 else max(h, tgt))
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


def stat_line(name, op):
    n = len(op)
    if n == 0:
        return f"  {name}: n=0"
    w = sum(1 for x in op if x > 0)
    ci = wilson(w, n)
    g = sum(x for x in op if x > 0)
    L = -sum(x for x in op if x <= 0)
    pf = "패배0" if L <= 0 else f"{g/L:.2f}"
    return (f"  {name}: n={n:2d}  승률 {w/n*100:5.1f}% (CI하한 {ci[0]:.1f}%)  "
            f"PF {pf:>5s}  평균 {np.mean(op):+6.1f}%  합계 {sum(op):+7.1f}%")


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

    gap_A, orb_ok, orb_conf, vwap_days = [], [], [], []
    typed = dict(A=0, B=0, C=0)
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

        covs = {}
        for bl, i5 in BASE_IDX.items():
            if i5 < len(b5) - 2:
                cx = b5[i5][3]
                covs[bl] = ((O0 - cx) / gap) if sgn > 0 else ((cx - O0) / abs(gap))
        in_rng = [bl for bl, cv in covs.items() if COVER_MIN <= cv < 1.0]
        if in_rng:
            typed["A"] += 1
            for bl in in_rng:
                i5 = BASE_IDX[bl]
                t_e = b5[i5][0]
                gap_A.append(dict(sgn=sgn, ep=b5[i5][3], tgt=pc, t0=t_e.time(), iv=iv,
                                  tail1=[x for x in b1 if x[0] > t_e],
                                  tail5=[x for x in b5 if x[0] > t_e]))
        elif all(cv < COVER_MIN for cv in covs.values()):
            typed["B"] += 1
        else:
            typed["C"] += 1

        # ORB 실행가능 버전: 09:40 시점 5분기준선 커버 < 0.40 → 갭방향 진입
        if "5m" in covs and covs["5m"] < COVER_MIN:
            i5 = BASE_IDX["5m"]
            t_e = b5[i5][0]
            rec = dict(d=str(d), sgn=sgn, ep=b5[i5][3], t0=t_e.time(), iv=iv,
                       cov5=round(covs["5m"], 2),
                       tail5=[x for x in b5 if x[0] > t_e],
                       later_ours=bool(in_rng))
            (orb_conf if rec["later_ours"] else orb_ok).append(rec)

        vols = {t: float(r["Volume"]) for t, r in g.iterrows()}
        vwap_days.append(dict(d=str(d), b1=b1, b5=b5, iv=iv, vols=vols))

    out = [f"ideatest · {len(days)}갭일 · 유형 A(우리진입 가능) {typed['A']} / "
           f"B(전기준선 커버미달=모멘텀) {typed['B']} / C(기준선서 이미갭필) {typed['C']}", ""]

    # ── 기준: 우리 갭필
    opA = []
    opD = []
    for t in gap_A:
        px, hd = run_gap_1m(t["tail1"], t["t0"], t["ep"], t["tgt"], t["sgn"], TRAIL)
        o = bsm_pl("p" if t["sgn"] > 0 else "c", t["ep"], px, t["t0"], hd, t["iv"])
        if o is not None:
            opA.append(o)
        px, hd = run_gap_app(t["tail5"], t["t0"], t["ep"], t["tgt"], t["sgn"], TRAIL)
        o = bsm_pl("p" if t["sgn"] > 0 else "c", t["ep"], px, t["t0"], hd, t["iv"])
        if o is not None:
            opD.append(o)
    out.append("[기준] 우리 갭필 (트레일 0.15%)")
    out.append(stat_line("이상적(1분)", opA))
    out.append(stat_line("앱 재현    ", opD))
    out.append("")

    # ── [1] ORB 스킵데이
    def orb_run(recs, cut):
        col = []
        for r in recs:
            px, hd = None, None
            for (t, h, l, c) in r["tail5"]:
                if t.time() >= cut:
                    px, hd = c, hold_h(t, r["t0"])
                    break
            if px is None:
                t, _, _, c = r["tail5"][-1]
                px, hd = c, hold_h(t, r["t0"])
            o = bsm_pl("c" if r["sgn"] > 0 else "p", r["ep"], px, r["t0"], hd, r["iv"])
            if o is not None:
                col.append(o)
        return col

    out.append(f"[1] ORB 스킵데이 수확 — 09:40에 5분커버<0.40 확인 후 갭방향 진입 "
               f"(순수 {len(orb_ok)}일 · 충돌 {len(orb_conf)}일)")
    for cut, nm in ((TIMECUT, "11:30 청산"), (FINALCUT, "14:00 청산")):
        out.append(stat_line(f"순수 스킵일 · {nm}", orb_run(orb_ok, cut)))
    if orb_conf:
        out.append(stat_line("충돌일(나중에 우리진입 뜸) · 14:00", orb_run(orb_conf, FINALCUT)))
    out.append("  ※ 충돌일 = ORB 진입 후 갭이 되돌아 우리 갭필 조건이 뜬 날 — ORB엔 역풍")
    out.append("")

    # ── [2] VWAP 트렌드
    day_pl, day_legs = [], []
    for rec in vwap_days:
        b1, b5, iv = rec["b1"], rec["b5"], rec["iv"]
        cpv = cv = 0.0
        vwap_at = {}
        vols = rec["vols"]
        for (t, h, l, c) in b1:
            tp = (h + l + c) / 3
            vv = max(vols.get(t, 0.0), 1.0)
            cpv += tp * vv
            cv += vv
            vwap_at[t] = cpv / cv
        legs, pos = [], None      # pos = (flag, ep, t0)
        for (t, h, l, c) in b5:
            if t.time() >= FINALCUT:
                if pos:
                    legs.append((pos, c, t))
                    pos = None
                break
            w = vwap_at.get(t)
            if w is None:
                continue
            want = "c" if c > w else ("p" if c < w else None)
            if want is None:
                continue
            if pos is None:
                pos = (want, c, t.time())
            elif pos[0] != want:
                legs.append((pos, c, t))
                pos = (want, c, t.time())
        if pos:
            t, _, _, c = b5[-1]
            legs.append((pos, c, t))
        pls = []
        for ((flag, ep, t0), xp, xt) in legs:
            o = bsm_pl(flag, ep, xp, t0, hold_h(xt, t0), iv)
            if o is not None:
                pls.append(o)
        if pls:
            day_pl.append(float(np.mean(pls)))
            day_legs.append(len(pls))
    out.append(f"[2] VWAP 트렌드 (5분 종가 교차 플립 · 14:00 청산 · 플립마다 스프레드 지불)")
    out.append(stat_line("갭일 전체 · 레그평균/일", day_pl))
    if day_legs:
        out.append(f"  평균 플립 {np.mean(day_legs):.1f}회/일 · 최대 {max(day_legs)}회 "
                   f"(플립 1회 = 스프레드 {SPREAD}% 지불)")
    out.append("")
    out.append("※ 58일 단일 레짐 표본. 백필 완료 후 275일 재검증 전제. 원칙 6·7 적용")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("ideatest_result.json", "w"), ensure_ascii=False, indent=1)
