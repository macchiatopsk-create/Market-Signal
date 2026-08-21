"""ideacheck — ideatest 감사. 좋아 보이는 숫자는 먼저 때려본다.

[1] ORB 스킵데이 해부: 갭업(콜)/갭다운(풋) 분리 · 월별 · 상위 집중도
[2] 플라시보: 같은 39일 같은 시각에 ①무조건 콜 ②무조건 풋 ③갭 반대방향
    → ORB(갭방향)가 플라시보를 못 이기면 신호가 아니라 장세
[3] VWAP 트렌드: 레그평균 아닌 '일 합계' 기준 재집계 (플립 비용이 쌓이는 실제 계좌 관점)
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


def agg(b, k):
    o = []
    for i in range(0, len(b), k):
        c = b[i:i + k]
        if c:
            o.append((c[-1][0], max(x[1] for x in c), min(x[2] for x in c), c[-1][3]))
    return o


def hh(t, t0):
    return (dt.datetime.combine(dt.date(2000, 1, 1), t.time())
            - dt.datetime.combine(dt.date(2000, 1, 1), t0)).total_seconds() / 3600


def bsm_pl(flag, ep, xp, t0, hold, iv):
    K = round(ep * (1 - ITM_PCT / 100)) if flag == "c" else round(ep * (1 + ITM_PCT / 100))
    h0 = t0.hour + t0.minute / 60
    p0 = _bs(flag, ep, K, max(16 - h0, .05) / 24 / 365, RFR, iv)
    p1 = _bs(flag, xp, K, max(16 - h0 - hold, .02) / 24 / 365, RFR, iv)
    return max((p1 - p0) / p0 * 100 - SPREAD, -100.0) if p0 > 0.01 else None


def sline(nm, op):
    n = len(op)
    if n == 0:
        return f"  {nm}: n=0"
    w = sum(1 for x in op if x > 0)
    g = sum(x for x in op if x > 0)
    L = -sum(x for x in op if x <= 0)
    pf = "패배0" if L <= 0 else f"{g/L:.2f}"
    return (f"  {nm}: n={n:2d}  승률 {w/n*100:5.1f}%  PF {pf:>5s}  "
            f"평균 {np.mean(op):+6.1f}%  중앙값 {np.median(op):+6.1f}%  합계 {sum(op):+7.1f}%")


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

    orb, vdays = [], []
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
        for i5 in (1, 2, 11):
            if i5 < len(b5) - 2:
                cx = b5[i5][3]
                covs[i5] = ((O0 - cx) / gap) if sgn > 0 else ((cx - O0) / abs(gap))
        in_rng = any(COVER_MIN <= cv < 1.0 for cv in covs.values())
        if covs.get(1, 1.0) < COVER_MIN and not in_rng:
            t_e = b5[1][0]
            orb.append(dict(d=str(d), mon=str(d)[:7], sgn=sgn, ep=b5[1][3],
                            t0=t_e.time(), iv=iv, gp=gp,
                            tail5=[x for x in b5 if x[0] > t_e]))
        vols = {t: float(r["Volume"]) for t, r in g.iterrows()}
        vdays.append(dict(b1=b1, b5=b5, iv=iv, vols=vols))

    def orb_pl(rec, flag, cut):
        px = hd = None
        for (t, h, l, c) in rec["tail5"]:
            if t.time() >= cut:
                px, hd = c, hh(t, rec["t0"])
                break
        if px is None:
            t, _, _, c = rec["tail5"][-1]
            px, hd = c, hh(t, rec["t0"])
        return bsm_pl(flag, rec["ep"], px, rec["t0"], hd, rec["iv"])

    out = [f"ideacheck · 순수 스킵일 {len(orb)}일", ""]

    # [1] 방향/월별/집중도
    out.append("[1] ORB 해부 (11:30 청산 기준)")
    sig = [(r, orb_pl(r, "c" if r["sgn"] > 0 else "p", TIMECUT)) for r in orb]
    sig = [(r, o) for r, o in sig if o is not None]
    up = [o for r, o in sig if r["sgn"] > 0]
    dn = [o for r, o in sig if r["sgn"] < 0]
    out.append(sline(f"갭업→콜 ", up))
    out.append(sline(f"갭다운→풋", dn))
    mons = sorted(set(r["mon"] for r, _ in sig))
    for m in mons:
        out.append(sline(f"{m}    ", [o for r, o in sig if r["mon"] == m]))
    ops = sorted([o for _, o in sig], reverse=True)
    tot = sum(ops)
    top5 = sum(ops[:5])
    out.append(f"  상위5건 합 {top5:+.1f}% = 전체의 {top5/tot*100 if tot else 0:.0f}% · "
               f"상위5 제외 평균 {np.mean(ops[5:]) if len(ops)>5 else 0:+.1f}%")
    out.append("")

    # [2] 플라시보
    out.append("[2] 플라시보 (같은 날 · 같은 시각 · 같은 청산)")
    for nm, fl in (("무조건 콜", lambda r: "c"), ("무조건 풋", lambda r: "p"),
                   ("갭 반대방향", lambda r: "p" if r["sgn"] > 0 else "c")):
        col = [orb_pl(r, fl(r), TIMECUT) for r in orb]
        col = [x for x in col if x is not None]
        out.append(sline(nm, col))
    out.append("  판정: 갭방향(신호)이 무조건콜·무조건풋을 확실히 이겨야 신호로 인정")
    out.append("")

    # [3] VWAP 일합계
    out.append("[3] VWAP 트렌드 — 일 합계 기준 (플립 비용 누적된 계좌 관점)")
    dsum, dlegs = [], []
    for rec in vdays:
        b1, b5, iv, vols = rec["b1"], rec["b5"], rec["iv"], rec["vols"]
        cpv = cv = 0.0
        wat = {}
        for (t, h, l, c) in b1:
            tp = (h + l + c) / 3
            vv = max(vols.get(t, 0.0), 1.0)
            cpv += tp * vv
            cv += vv
            wat[t] = cpv / cv
        legs, pos = [], None
        for (t, h, l, c) in b5:
            if t.time() >= FINALCUT:
                if pos:
                    legs.append((pos, c, t))
                    pos = None
                break
            w = wat.get(t)
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
        pls = [bsm_pl(f, ep, xp, t0, hh(xt, t0), iv) for ((f, ep, t0), xp, xt) in legs]
        pls = [x for x in pls if x is not None]
        if pls:
            dsum.append(sum(pls))
            dlegs.append(len(pls))
    out.append(sline("일 합계", dsum))
    out.append(f"  평균 레그 {np.mean(dlegs):.1f}개/일 · 스프레드만 하루 평균 "
               f"{np.mean(dlegs)*SPREAD:.1f}% 지불")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("ideacheck_result.json", "w"), ensure_ascii=False, indent=1)
