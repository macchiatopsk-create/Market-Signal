"""트레일링 폭 0.05% 단위 스윕 — 앱 실제 해상도(5분) 기준.
진입 조건 고정, 트레일 폭만 0.05 ~ 1.50% 를 0.05 간격으로.
비교용으로 15분 / 1시간 판정도 같이 낸다.

규칙 (형님 확정본):
  진입   기준선 봉 종가. 갭 0.2~1.5%, 개장VIX|x|<5%, 커버 >= 0.40, 진입시점 미필
  손절   없음
  갭필   전일종가 도달 → 트레일 무장. 그 봉에서는 청산 판정 안 함
  트레일 갭필 이후 봉부터. ext = 갭필 이후 최저(최고), 트레일선 = ext*(1±T%)
  타임컷 11:30까지 미갭필 → 청산 / 14:00 최종컷

옵션 환산: 실측 파라미터로 프리미엄/세타/스프레드 반영한 순손익도 병기
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

COVER_MIN = 0.40
TIMECUT = dt.time(11, 30)
FINALCUT = dt.time(14, 0)
BASE_IDX = {"5m": 1, "15m": 2, "1h": 11}
RES = {"5m": 1, "15m": 3, "1h": 12}
TRAILS = [round(x * 0.05, 2) for x in range(1, 31)]      # 0.05 ~ 1.50

# 실측 옵션 파라미터 (2026-08-18 로빈후드)
DELTA = 0.69
SPREAD = 2.2          # 왕복 스프레드 %
ITM_PCT = 0.50
TV_RATIO = 0.28
THETA_PER_HR = 0.214  # 프리미엄 $4.75 기준 시간당 $


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
    out = []
    for i in range(0, len(bars), k):
        ch = bars[i:i + k]
        if ch:
            out.append((ch[-1][0], max(x[1] for x in ch), min(x[2] for x in ch), ch[-1][3]))
    return out


def run(seq, t0, ep, tgt, sgn, trail):
    """seq = 진입 다음 봉부터의 (t,h,l,c). 반환 (결과, 기초%, 보유시간h)"""
    filled = False
    ext = None
    for (t, h, l, c) in seq:
        hold = (dt.datetime.combine(dt.date(2000, 1, 1), t.time())
                - dt.datetime.combine(dt.date(2000, 1, 1), t0)).total_seconds() / 3600
        if not filled:
            if (l <= tgt) if sgn > 0 else (h >= tgt):
                filled = True
                ext = min(l, tgt) if sgn > 0 else max(h, tgt)
                continue
            if t.time() >= TIMECUT:
                return "TIMECUT", ((ep - c) / ep * 100) if sgn > 0 else ((c - ep) / ep * 100), hold
            continue
        ext = min(ext, l) if sgn > 0 else max(ext, h)
        tp = ext * (1 + trail / 100) if sgn > 0 else ext * (1 - trail / 100)
        if (h >= tp) if sgn > 0 else (l <= tp):
            return "TRAIL", ((ep - tp) / ep * 100) if sgn > 0 else ((tp - ep) / ep * 100), hold
        if t.time() >= FINALCUT:
            return "CUT", ((ep - c) / ep * 100) if sgn > 0 else ((c - ep) / ep * 100), hold
    t, _, _, c = seq[-1]
    hold = (dt.datetime.combine(dt.date(2000, 1, 1), t.time())
            - dt.datetime.combine(dt.date(2000, 1, 1), t0)).total_seconds() / 3600
    return "EOD", ((ep - c) / ep * 100) if sgn > 0 else ((c - ep) / ep * 100), hold


def opt_net(spot, ux, hold, vix):
    """기초 ux% 움직임 → 옵션 순손익 % (프리미엄 대비). 최대손실 -100%"""
    intr = spot * ITM_PCT / 100
    prem = intr / (1 - TV_RATIO) * (vix / 16.0) ** 0.5
    gross = spot * ux / 100 * DELTA
    theta = THETA_PER_HR * hold * (prem / 4.75)
    slip = prem * SPREAD / 100
    net = max(gross - theta - slip, -prem)
    return net / prem * 100


def main():
    v = norm(yf.Ticker("^VIX").history(period="6mo")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in ch.dropna().items()}
    vl = {str(pd.Timestamp(k).date()): float(x) for k, x in v["Open"].items()}

    df = yf.download("QQQ", period="60d", interval="5m", prepost=False,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df.index = df.index.tz_convert("America/New_York")
    df = df[(df.index.time >= dt.time(9, 30)) & (df.index.time < dt.time(16, 0))]

    days = sorted(set(df.index.date))
    trades = []          # 진입 확정된 거래들
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
                        trades.append(dict(d=str(d), bl=bl, sgn=sgn, ep=ep, tgt=pc,
                                           cov=round(cov, 2), gap=round(abs(gp), 3),
                                           t0=bars5[i0][0].time(),
                                           tail=bars5[i0 + 1:],
                                           vix=vl.get(str(d), 16.0)))
        pc = bars5[-1][3]

    def stat(ux, op):
        n = len(ux)
        if n == 0:
            return None
        w = sum(1 for x in op if x > 0)
        ci = wilson(w, n)
        gp = sum(x for x in op if x > 0)
        ls = -sum(x for x in op if x <= 0)
        pf = None if ls <= 0 else gp / ls
        return dict(n=n, wr=w / n * 100, ci=ci, pf=pf,
                    ux=float(np.mean(ux)), op=float(np.mean(op)), tot=float(np.sum(op)))

    out = [f"트레일 폭 스윕 0.05% 단위 · QQQ 5분봉 {len(days)}거래일 ({days[0]}~{days[-1]})",
           f"진입 고정(커버 {COVER_MIN}~1.00) · 손절없음 · 11:30 타임컷 · 14:00 컷 · 거래 {len(trades)}건",
           "옵션% = 델타 0.69 · 세타 · 스프레드 2.2% 반영, 최대손실 -100%", ""]

    best = {}
    for rn, k in RES.items():
        out.append(f"[청산 판정 해상도 {rn}]")
        out.append(f"  {'트레일':>6s} {'승률':>7s} {'CI하한':>7s} {'PF':>7s} "
                   f"{'기초평균':>8s} {'옵션평균':>8s} {'옵션합계':>9s} {'트레일청산':>7s}")
        rows = []
        for T in TRAILS:
            ux, op, ntr = [], [], 0
            for t in trades:
                seq = agg(t["tail"], k)
                if not seq:
                    continue
                r_, p_, hold = run(seq, t["t0"], t["ep"], t["tgt"], t["sgn"], T)
                ux.append(p_)
                op.append(opt_net(t["ep"], p_, hold, t["vix"]))
                if r_ == "TRAIL":
                    ntr += 1
            s = stat(ux, op)
            if s is None:
                continue
            rows.append((T, s, ntr))
            pfs = "패배0" if s["pf"] is None else f"{s['pf']:.2f}"
            out.append(f"  {T:6.2f}% {s['wr']:6.1f}% {s['ci'][0]:6.1f}% {pfs:>7s} "
                       f"{s['ux']:+7.3f}% {s['op']:+7.1f}% {s['tot']:+8.1f}% {ntr:6d}/{s['n']}")
        cand = [r for r in rows if r[1]["pf"] is not None]
        if cand:
            b = max(cand, key=lambda r: r[1]["tot"])
            best[rn] = b
            out.append(f"  → 옵션 합계 최대: 트레일 {b[0]:.2f}%  "
                       f"합계 {b[1]['tot']:+.1f}%  PF {b[1]['pf']:.2f}  CI하한 {b[1]['ci'][0]:.1f}%")
        out.append("")

    out.append("[요약]")
    for rn in RES:
        if rn in best:
            T, s, ntr = best[rn]
            out.append(f"  {rn:4s} 최적 트레일 {T:.2f}%  n={s['n']}  승률 {s['wr']:.1f}% "
                       f"(CI하한 {s['ci'][0]:.1f}%)  PF {s['pf']:.2f}  옵션평균 {s['op']:+.1f}%")
    out.append("")
    out.append("※ n이 작다. 원칙 7(n<15 방향만) · 원칙 6(패배0=계산불능) 적용해서 읽을 것")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("trailsweep_result.json", "w"), ensure_ascii=False, indent=1)
