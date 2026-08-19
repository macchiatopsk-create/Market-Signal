"""1분 해상도 트레일 스윕 — yfinance 1분봉(최근 30일, 7일씩 청크)으로 확보.
같은 진입에 대해 청산 판정을 1분 / 5분 / 15분 / 1시간으로 각각 돌린다.
목적: 5분 해상도에서 0.05~0.25% 구간이 구분 안 되던 문제가 1분에서 풀리는지.

규칙 (형님 확정본):
  진입   기준선 봉 종가(5분봉 idx 1/2/11). 갭 0.2~1.5%, 개장VIX|x|<5%, 커버 0.40~1.00
  손절   없음
  갭필   전일종가 도달 → 트레일 무장. 그 봉에서는 청산 판정 안 함
  트레일 직전 봉까지의 ext로 판정 후 ext 갱신 (봉내 look-ahead 방지)
  타임컷 11:30 미갭필 청산 / 14:00 최종컷
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

COVER_MIN = 0.40
TIMECUT = dt.time(11, 30)
FINALCUT = dt.time(14, 0)
BASE_IDX = {"5m": 1, "15m": 2, "1h": 11}          # 5분봉 0-base
RES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}     # 청산 판정 = 1분봉 몇 개 묶음
TRAILS = [round(x * 0.05, 2) for x in range(1, 21)]   # 0.05 ~ 1.00

DELTA, SPREAD, ITM_PCT, TV_RATIO, THETA_PER_HR = 0.69, 2.2, 0.50, 0.28, 0.214


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


def fetch_1m():
    """최근 30일을 7일씩 끊어서 1분봉 수집."""
    end = dt.date.today() + dt.timedelta(days=1)
    frames = []
    for back in range(28, -1, -7):
        s = end - dt.timedelta(days=back + 7)
        e = end - dt.timedelta(days=back)
        try:
            d = yf.download("QQQ", start=s.isoformat(), end=e.isoformat(), interval="1m",
                            prepost=False, auto_adjust=False, progress=False)
        except Exception as ex:
            print(f"  청크 {s}~{e} 실패: {ex}")
            continue
        if d is None or len(d) == 0:
            print(f"  청크 {s}~{e} 데이터 없음")
            continue
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        frames.append(d.dropna())
        print(f"  청크 {s}~{e}: {len(d)}봉")
    if not frames:
        raise RuntimeError("1분봉 확보 실패")
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index = df.index.tz_convert("America/New_York")
    return df[(df.index.time >= dt.time(9, 30)) & (df.index.time < dt.time(16, 0))]


def agg(bars, k):
    out = []
    for i in range(0, len(bars), k):
        ch = bars[i:i + k]
        if ch:
            out.append((ch[-1][0], max(x[1] for x in ch), min(x[2] for x in ch), ch[-1][3]))
    return out


def run(seq, t0, ep, tgt, sgn, trail):
    filled, ext = False, None
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
        tp = ext * (1 + trail / 100) if sgn > 0 else ext * (1 - trail / 100)
        if (h >= tp) if sgn > 0 else (l <= tp):
            return "TRAIL", ((ep - tp) / ep * 100) if sgn > 0 else ((tp - ep) / ep * 100), hold
        ext = min(ext, l) if sgn > 0 else max(ext, h)
        if t.time() >= FINALCUT:
            return "CUT", ((ep - c) / ep * 100) if sgn > 0 else ((c - ep) / ep * 100), hold
    t, _, _, c = seq[-1]
    hold = (dt.datetime.combine(dt.date(2000, 1, 1), t.time())
            - dt.datetime.combine(dt.date(2000, 1, 1), t0)).total_seconds() / 3600
    return "EOD", ((ep - c) / ep * 100) if sgn > 0 else ((c - ep) / ep * 100), hold


def opt_net(spot, ux, hold, vix):
    intr = spot * ITM_PCT / 100
    prem = intr / (1 - TV_RATIO) * (vix / 16.0) ** 0.5
    net = max(spot * ux / 100 * DELTA - THETA_PER_HR * hold * (prem / 4.75)
              - prem * SPREAD / 100, -prem)
    return net / prem * 100


def main():
    v = norm(yf.Ticker("^VIX").history(period="3mo")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in ch.dropna().items()}
    vl = {str(pd.Timestamp(k).date()): float(x) for k, x in v["Open"].items()}

    print("1분봉 수집:")
    df = fetch_1m()
    days = sorted(set(df.index.date))
    print(f"  총 {len(days)}거래일 {len(df)}봉")

    trades, pc = [], None
    for d in days:
        g = df[df.index.date == d]
        if len(g) < 300:
            if len(g):
                pc = float(g["Close"].iloc[-1])
            continue
        b1 = [(t, float(r["High"]), float(r["Low"]), float(r["Close"])) for t, r in g.iterrows()]
        b5 = agg(b1, 5)
        O0 = float(g["Open"].iloc[0])
        if pc:
            vx = vm.get(str(d))
            if vx is None or abs(vx) < 5.0:
                gap = O0 - pc
                gp = gap / pc * 100
                if 0.2 <= abs(gp) < 1.5:
                    sgn = 1 if gap > 0 else -1
                    for bl, i5 in BASE_IDX.items():
                        if i5 >= len(b5) - 2:
                            continue
                        ep = b5[i5][3]
                        cov = ((O0 - ep) / gap) if sgn > 0 else ((ep - O0) / abs(gap))
                        if not (COVER_MIN <= cov < 1.0):
                            continue
                        t_entry = b5[i5][0]
                        tail = [x for x in b1 if x[0] > t_entry]
                        trades.append(dict(d=str(d), bl=bl, sgn=sgn, ep=ep, tgt=pc,
                                           cov=round(cov, 2), gap=round(abs(gp), 3),
                                           t0=t_entry.time(), tail=tail,
                                           vix=vl.get(str(d), 16.0)))
        pc = b1[-1][3]

    out = [f"1분 해상도 트레일 스윕 · QQQ 1분봉 {len(days)}거래일 "
           f"({days[0]}~{days[-1]}) · 거래 {len(trades)}건",
           "진입 완전 고정 · 청산 판정 봉 크기만 변경 · 봉내 look-ahead 수정본",
           "옵션% = 델타 0.69 · 세타 · 스프레드 2.2% 반영", ""]
    if not trades:
        out.append("진입 조건 충족 거래 0건")
        return out

    out.append("거래 목록: " + ", ".join(f"{t['d']}/{t['bl']}(cov{t['cov']:.2f})" for t in trades))
    out.append("")

    for rn, k in RES.items():
        out.append(f"[청산 판정 {rn}]")
        out.append(f"  {'트레일':>6s} {'승률':>7s} {'CI하한':>7s} {'PF':>7s} "
                   f"{'기초평균':>8s} {'옵션평균':>8s} {'트레일':>6s} {'평균보유':>7s}")
        for T in TRAILS:
            ux, op, hd, ntr = [], [], [], 0
            for t in trades:
                seq = agg(t["tail"], k)
                if not seq:
                    continue
                r_, p_, hold = run(seq, t["t0"], t["ep"], t["tgt"], t["sgn"], T)
                ux.append(p_)
                op.append(opt_net(t["ep"], p_, hold, t["vix"]))
                hd.append(hold)
                if r_ == "TRAIL":
                    ntr += 1
            n = len(ux)
            if n == 0:
                continue
            w = sum(1 for x in op if x > 0)
            ci = wilson(w, n)
            gpos = sum(x for x in op if x > 0)
            lneg = -sum(x for x in op if x <= 0)
            pfs = "패배0" if lneg <= 0 else f"{gpos/lneg:.2f}"
            out.append(f"  {T:6.2f}% {w/n*100:6.1f}% {ci[0]:6.1f}% {pfs:>7s} "
                       f"{np.mean(ux):+7.3f}% {np.mean(op):+7.1f}% {ntr:3d}/{n} "
                       f"{np.mean(hd):6.2f}h")
        out.append("")
    out.append("※ n이 작다. 원칙 7 · 원칙 6 적용해서 읽을 것")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("minres_result.json", "w"), ensure_ascii=False, indent=1)
