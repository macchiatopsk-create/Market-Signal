"""orbvwap — 형님 요청 2건을 같은 58일 데이터·같은 옵션 P&L로 비교.

[1] ORB를 우리 시스템에 붙이기
    우리 갭필이 스킵하는 날(커버<0.40 = 갭이 안 되돌아오는 날)에
    갭 방향 모멘텀 진입. 시가레인지(첫 5분) 반대 극점 = 손절, 14:00 컷.
    변형 A: 논문 원형 — 09:35에 첫봉 방향으로 전 갭일 진입 (대조군)
    변형 B: 스킵데이 한정 — 09:45 커버<0.40 확인 후 갭 방향 진입 (우리식 결합)

[2] VWAP 트렌드 (Zarattini VWAP 논문) vs 우리 갭필
    VWAP 위=롱 아래=숏, 교차 시 뒤집기, 14:00 청산. 기초자산 레벨로 측정.
    옵션 표현 가능성은 하루 플립 수 × 스프레드 2.2%로 별도 평가.

공통: 청산 판정은 이상적 1분(mode A) — 우리 갭필도 같은 조건으로 재출력해 비교.
옵션 P&L = minres2와 동일 BSM(세타 내장), IV=VXN×1.3.
주의: 데이터가 '갭일만'이라 VWAP 비교도 갭일 우주에 한정됨.
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
ITM_PCT, SPREAD, RFR, K_IV = 0.50, 2.2, 0.045, 1.3
DATA = "data/1m"
GAP_TRAIL = 0.15


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


def bsm_net(side, ep, exit_px, t0, hold, iv):
    """side: 'c'/'p'. 순손익% (스프레드 반영, 하한 -100)."""
    K = round(ep * (1 - ITM_PCT / 100)) if side == "c" else round(ep * (1 + ITM_PCT / 100))
    t0h = t0.hour + t0.minute / 60
    tau0 = max(16.0 - t0h, 0.05) / 24 / 365
    tau1 = max(16.0 - t0h - hold, 0.02) / 24 / 365
    p0 = _bs(side, ep, K, tau0, RFR, iv)
    p1 = _bs(side, exit_px, K, tau1, RFR, iv)
    if p0 <= 0.01:
        return None
    return max((p1 - p0) / p0 * 100 - SPREAD, -100.0)


def hold_h(t, t0):
    return (dt.datetime.combine(dt.date(2000, 1, 1), t.time())
            - dt.datetime.combine(dt.date(2000, 1, 1), t0)).total_seconds() / 3600


def stat_line(tag, rows):
    """rows: [(opt%, hold)] → 요약 한 줄."""
    if not rows:
        return f"  {tag:26s} 거래 0건"
    op = [r[0] for r in rows]
    n = len(op)
    w = sum(1 for x in op if x > 0)
    ci = wilson(w, n)
    g = sum(x for x in op if x > 0)
    l = -sum(x for x in op if x <= 0)
    pf = "패배0" if l <= 0 else f"{g/l:.2f}"
    return (f"  {tag:26s} n={n:3d}  승률 {w/n*100:5.1f}% (CI하한 {ci[0]:4.1f}%)  "
            f"PF {pf:>5s}  옵션평균 {np.mean(op):+6.1f}%  보유 {np.mean([r[1] for r in rows]):.2f}h")


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
    out = [f"orbvwap · 갭일 {len(days)}일 ({days[0]}~{days[-1]}) · BSM 세타 내장 · "
           f"IV=VXN×{K_IV} · 청산판정 이상적 1분(mode A)", ""]

    ours, orb_a, orb_b, vwap_days = [], [], [], []
    n_skip = n_trade = 0

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

        # ---------- 우리 갭필 (1h 기준선 10:29, 트레일 0.15, mode A) ----------
        ep15 = b1[14][3] if len(b1) > 16 else None      # 09:44 종가 (커버 판정용 15m 기준선)
        ep1h = b1[59][3] if len(b1) > 61 else None      # 10:29 종가
        cov1h = None
        if ep1h is not None:
            cov1h = ((O0 - ep1h) / gap) if sgn > 0 else ((ep1h - O0) / abs(gap))
            if COVER_MIN <= cov1h < 1.0:
                n_trade += 1
                t0 = b1[59][0].time()
                filled, ext, done = False, None, False
                for (t, h, l, c) in b1[60:]:
                    if not filled:
                        if (l <= pc) if sgn > 0 else (h >= pc):
                            filled = True
                            ext = min(l, pc) if sgn > 0 else max(h, pc)
                            continue
                        if t.time() >= TIMECUT:
                            o = bsm_net("p" if sgn > 0 else "c", ep1h, c, t0, hold_h(t, t0), iv)
                            ours.append((o, hold_h(t, t0))); done = True; break
                        continue
                    tp = ext * (1 + GAP_TRAIL / 100) if sgn > 0 else ext * (1 - GAP_TRAIL / 100)
                    if (h >= tp) if sgn > 0 else (l <= tp):
                        o = bsm_net("p" if sgn > 0 else "c", ep1h, tp, t0, hold_h(t, t0), iv)
                        ours.append((o, hold_h(t, t0))); done = True; break
                    ext = min(ext, l) if sgn > 0 else max(ext, h)
                    if t.time() >= FINALCUT:
                        o = bsm_net("p" if sgn > 0 else "c", ep1h, c, t0, hold_h(t, t0), iv)
                        ours.append((o, hold_h(t, t0))); done = True; break
                if not done and b1[60:]:
                    t, _, _, c = b1[-1]
                    o = bsm_net("p" if sgn > 0 else "c", ep1h, c, t0, hold_h(t, t0), iv)
                    ours.append((o, hold_h(t, t0)))

        # ---------- ORB 변형 A: 논문 원형 (전 갭일, 09:35, 첫봉 방향) ----------
        c5 = b1[4][3]                                    # 09:34 종가
        fdir = 1 if c5 > O0 else (-1 if c5 < O0 else 0)  # 첫 5분봉 방향
        if fdir != 0 and len(b1) > 6:
            side = "c" if fdir > 0 else "p"
            ep = c5
            t0 = b1[4][0].time()
            stop = or_lo if fdir > 0 else or_hi
            done = False
            for (t, h, l, c) in b1[5:]:
                if (l <= stop) if fdir > 0 else (h >= stop):
                    o = bsm_net(side, ep, stop, t0, hold_h(t, t0), iv)
                    orb_a.append((o, hold_h(t, t0))); done = True; break
                if t.time() >= FINALCUT:
                    o = bsm_net(side, ep, c, t0, hold_h(t, t0), iv)
                    orb_a.append((o, hold_h(t, t0))); done = True; break
            if not done:
                t, _, _, c = b1[-1]
                o = bsm_net(side, ep, c, t0, hold_h(t, t0), iv)
                orb_a.append((o, hold_h(t, t0)))

        # ---------- ORB 변형 B: 스킵데이 한정 (09:45 커버<0.40 → 갭 방향) ----------
        if ep15 is not None:
            cov15 = ((O0 - ep15) / gap) if sgn > 0 else ((ep15 - O0) / abs(gap))
            if cov15 < COVER_MIN:
                n_skip += 1
                side = "c" if sgn > 0 else "p"           # 갭 방향 모멘텀 (갭업=콜)
                ep = ep15
                t0 = b1[14][0].time()
                stop = or_lo if sgn > 0 else or_hi
                done = False
                for (t, h, l, c) in b1[15:]:
                    if (l <= stop) if sgn > 0 else (h >= stop):
                        o = bsm_net(side, ep, stop, t0, hold_h(t, t0), iv)
                        orb_b.append((o, hold_h(t, t0))); done = True; break
                    if t.time() >= FINALCUT:
                        o = bsm_net(side, ep, c, t0, hold_h(t, t0), iv)
                        orb_b.append((o, hold_h(t, t0))); done = True; break
                if not done:
                    t, _, _, c = b1[-1]
                    o = bsm_net(side, ep, c, t0, hold_h(t, t0), iv)
                    orb_b.append((o, hold_h(t, t0)))

        # ---------- VWAP 트렌드 (기초자산, 교차 뒤집기, 14:00 청산) ----------
        cv = cpv = 0.0
        pos, entry_px, flips, pnl = 0, None, 0, 0.0
        for i, (t, h, l, c) in enumerate(b1):
            tp_ = (h + l + c) / 3
            vol = float(g["Volume"].iloc[i])
            cpv += tp_ * vol
            cv += vol
            w = cpv / cv if cv else tp_
            want = 1 if c > w else (-1 if c < w else pos)
            if t.time() >= FINALCUT:
                if pos != 0:
                    pnl += pos * (c - entry_px) / entry_px * 100
                break
            if want != pos:
                if pos != 0:
                    pnl += pos * (c - entry_px) / entry_px * 100
                    flips += 1
                pos, entry_px = want, c
        vwap_days.append((pnl, flips))

    out.append(f"[1] ORB 결합 테스트  (스킵데이 {n_skip}일 / 갭필 거래일 {n_trade}일)")
    out.append(stat_line("우리 갭필 (1h·0.15, 기준)", ours))
    out.append(stat_line("ORB-A 논문원형 (전 갭일)", orb_a))
    out.append(stat_line("ORB-B 스킵데이 한정", orb_b))
    out.append("")

    p = [x[0] for x in vwap_days]
    f = [x[1] for x in vwap_days]
    n = len(p)
    w = sum(1 for x in p if x > 0)
    g_ = sum(x for x in p if x > 0)
    l_ = -sum(x for x in p if x <= 0)
    out.append(f"[2] VWAP 트렌드 (기초자산 레벨, 갭일 {n}일 한정)")
    out.append(f"  일평균 {np.mean(p):+.3f}%  승일 {w}/{n} ({w/n*100:.1f}%)  "
               f"PF {('패배0' if l_<=0 else f'{g_/l_:.2f}')}  합계 {sum(p):+.2f}%")
    out.append(f"  하루 평균 플립 {np.mean(f):.1f}회 → 옵션 표현 시 스프레드만 "
               f"{np.mean(f)*SPREAD:.1f}%/일 — 옵션 부적합, 하려면 주식/선물")
    ou = [x[0] for x in ours]
    if ou:
        out.append(f"  (참고: 우리 갭필 옵션평균 {np.mean(ou):+.1f}%/거래, "
                   f"기초 레벨로는 진입일에만 발생)")
    out.append("")
    out.append("※ 갭일 우주 58일 · 한 레짐 · mode A(이상적) 기준. 백필 완료 후 재검증 대상")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("orbvwap_result.json", "w"), ensure_ascii=False, indent=1)
