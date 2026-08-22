"""minres2 — 트레일 스윕 v2: Dukascopy 1분봉(백필) + BSM 옵션 P&L(세타 정식 반영).

v1 대비 바뀐 것 (둘뿐):
  데이터   yfinance 30일 → data/1m/*.csv.gz (Dukascopy 백필, 갭 거래일 전체)
  옵션P&L  선형 델타 근사 → py_vollib 블랙숄즈 재평가 (진입/청산 시점 프리미엄 차)
           만기 16:00 ET, τ가 장중에 줄며 세타가 자동 반영됨
바뀌지 않은 것:
  진입/청산 엔진(run, agg) — 봉내 look-ahead 수정본 그대로
  규칙: 갭 0.2~1.5% · 개장VIX|x|<5% · 커버 0.40~1.00 · 손절없음 · 11:30컷 · 14:00최종컷
IV: VXN 시가 × k. 0DTE IV는 30일 지수 IV보다 높은 게 보통이라 k∈{1.0,1.3,1.6} 민감도.
스프레드: 실측 왕복 2.2% (프리미엄 대비) 유지. 순손익 하한 -100% (프리미엄 캡).
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
RES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
TRAILS = [round(x * 0.05, 2) for x in range(1, 21)]
ITM_PCT, SPREAD, RFR = 0.50, 2.2, 0.045
K_SENS = [1.0, 1.3, 1.6]
K_MAIN = 1.3
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
    frames = []
    for f in sorted(glob.glob(f"{DATA}/QQQ_*.csv.gz")):
        d = pd.read_csv(f, compression="gzip")
        frames.append(d)
    if not frames:
        raise RuntimeError("data/1m 비어 있음")
    df = pd.concat(frames, ignore_index=True)
    df["t"] = pd.to_datetime(df["ts"])
    df = df.drop_duplicates(subset=["ts"]).sort_values("t").set_index("t")
    # 완전한 날만 (봉 320+)
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
                px = c
                return "TIMECUT", px, hold
            continue
        tp = ext * (1 + trail / 100) if sgn > 0 else ext * (1 - trail / 100)
        if (h >= tp) if sgn > 0 else (l <= tp):
            return "TRAIL", tp, hold
        ext = min(ext, l) if sgn > 0 else max(ext, h)
        if t.time() >= FINALCUT:
            return "CUT", c, hold
    t, _, _, c = seq[-1]
    hold = (dt.datetime.combine(dt.date(2000, 1, 1), t.time())
            - dt.datetime.combine(dt.date(2000, 1, 1), t0)).total_seconds() / 3600
    return "EOD", c, hold


def bsm_net(sgn, ep, exit_px, t0, hold, iv):
    """BSM 재평가 순손익%: (청산프리미엄-진입프리미엄)/진입프리미엄 - 스프레드, 하한 -100."""
    flag = "p" if sgn > 0 else "c"                 # 갭업→풋, 갭다운→콜
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
        return ["py_vollib 미설치 — deps에 추가 필요"]

    v = norm(yf.Ticker("^VIX").history(period="26mo")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in ch.dropna().items()}
    try:
        x = norm(yf.Ticker("^VXN").history(period="26mo")[["Open"]].dropna())
        ivm = {str(pd.Timestamp(k).date()): float(r) / 100 for k, r in x["Open"].items()}
    except Exception:
        ivm = {}
    vix_open = {str(pd.Timestamp(k).date()): float(r) for k, r in v["Open"].items()}

    dd = norm(yf.download("QQQ", period="26mo", interval="1d",
                          auto_adjust=False, progress=False))
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    closes = {pd.Timestamp(k).date(): float(r) for k, r in dd["Close"].items()}
    dl = sorted(closes.keys())
    prevc = {dl[i]: closes[dl[i - 1]] for i in range(1, len(dl))}

    df = load_1m()
    days = sorted(set(df.index.date))
    out = [f"minres2 · Dukascopy 1분봉 {len(days)}거래일 ({days[0]}~{days[-1]})",
           "v1과 동일 엔진 · 옵션P&L = BSM 재평가(만기16:00, 세타 내장) · "
           f"IV = VXN시가×{K_MAIN} · 스프레드 {SPREAD}%", ""]

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
        iv = ivm.get(str(d)) or (vix_open.get(str(d), 16.0) * 1.15 / 100)
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
                               t0=t_entry.time(), tail=tail, iv=iv))

    out.append(f"거래 {len(trades)}건 (기준선 중복 포함): "
               + ", ".join(f"{t['d']}/{t['bl']}" for t in trades))
    out.append("")
    if not trades:
        return out

    def sweep(k_iv, res_keys):
        blk = []
        for rn in res_keys:
            k = RES[rn]
            blk.append(f"[청산 판정 {rn} · IV×{k_iv}]")
            blk.append(f"  {'트레일':>6s} {'승률':>7s} {'CI하한':>7s} {'PF':>7s} "
                       f"{'기초평균':>8s} {'옵션평균':>8s} {'트레일수':>7s} {'평균보유':>7s}")
            for T in TRAILS:
                ux, op, hd, ntr = [], [], [], 0
                for t in trades:
                    seq = agg(t["tail"], k)
                    if not seq:
                        continue
                    r_, px, hold = run(seq, t["t0"], t["ep"], t["tgt"], t["sgn"], T)
                    upct = ((t["ep"] - px) / t["ep"] * 100) if t["sgn"] > 0 \
                        else ((px - t["ep"]) / t["ep"] * 100)
                    o = bsm_net(t["sgn"], t["ep"], px, t["t0"], hold, t["iv"] * k_iv)
                    if o is None:
                        continue
                    ux.append(upct)
                    op.append(o)
                    hd.append(hold)
                    if r_ == "TRAIL":
                        ntr += 1
                n = len(op)
                if n == 0:
                    continue
                w = sum(1 for x in op if x > 0)
                ci = wilson(w, n)
                gpos = sum(x for x in op if x > 0)
                lneg = -sum(x for x in op if x <= 0)
                pfs = "패배0" if lneg <= 0 else f"{gpos/lneg:.2f}"
                blk.append(f"  {T:6.2f}% {w/n*100:6.1f}% {ci[0]:6.1f}% {pfs:>7s} "
                           f"{np.mean(ux):+7.3f}% {np.mean(op):+7.1f}% {ntr:4d}/{n} "
                           f"{np.mean(hd):6.2f}h")
            blk.append("")
        return blk

    out += sweep(K_MAIN, ["1m", "5m", "15m", "1h"])
    out.append("=== IV 민감도 (1m 판정, 옵션평균 기준 상위 5개 트레일) ===")
    for kv in K_SENS:
        rows = []
        for T in TRAILS:
            op = []
            for t in trades:
                seq = agg(t["tail"], 1)
                if not seq:
                    continue
                r_, px, hold = run(seq, t["t0"], t["ep"], t["tgt"], t["sgn"], T)
                o = bsm_net(t["sgn"], t["ep"], px, t["t0"], hold, t["iv"] * kv)
                if o is not None:
                    op.append(o)
            if op:
                rows.append((T, np.mean(op)))
        top = sorted(rows, key=lambda x: -x[1])[:5]
        out.append(f"  IV×{kv}: " + "  ".join(f"{t:.2f}%→{m:+.1f}%" for t, m in top))
    out.append("")
    out.append("※ 표본이 아직 백필 중간 단계임. 원칙 6·7 적용. 백필 완료 후 재실행 예정")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("minres2_result.json", "w"), ensure_ascii=False, indent=1)
