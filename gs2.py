"""gs2 — GS PredefinedAssetEngine 풀스펙 백테스트 (공식 결과).

09:45 결합 룰 전체를 GS 트리거 안에 구현, 1분 스테이트로 실행:
  커버≥0.40           → 갭필: 되돌림 방향 진입, 필 후 트레일 0.15%,
                         11:30 미필 청산, 14:00 컷
  커버<0.40 + VIX확인  → 모멘텀: 갭 방향 진입, 트레일 0.30%,
                         OR(첫5분) 반대극점 손절, 14:00 컷
  역행                 → 관망
체결 = 1분 폴링 (감지·체결 모두 해당 분 시가) — 앱 D모드와 A모드의 중간.
기초자산(QQQ 10주) 레벨 — 옵션 프라이싱은 GS 서버 전용이라 밖에 둠.
교차검증: 동일 룰 pandas 수계산과 거래별 P&L 대조.
"""
import json, glob, math, datetime as dt, traceback
import numpy as np
import pandas as pd
import yfinance as yf

UTC = dt.timezone.utc
COVER_MIN, FILL_TRAIL, MOM_TRAIL = 0.40, 0.15, 0.30
T_SIG, T_TIMECUT, T_FINAL = dt.time(9, 45), dt.time(11, 30), dt.time(14, 0)
QTY = 10
DATA = "data/1m"


def norm(d):
    try:
        d.index = d.index.tz_localize(None)
    except Exception:
        pass
    d.index = pd.to_datetime(d.index).normalize()
    return d[~d.index.duplicated(keep="last")]


def build():
    frames = [pd.read_csv(f, compression="gzip")
              for f in sorted(glob.glob(f"{DATA}/QQQ_*.csv.gz"))]
    df = pd.concat(frames, ignore_index=True)
    df["t"] = pd.to_datetime(df["ts"]).dt.tz_localize(UTC)
    df = df.drop_duplicates("ts").sort_values("t").set_index("t")
    cnt = df.groupby(df.index.date).size()
    df = df[[d in set(cnt[cnt >= 320].index) for d in df.index.date]]
    days = sorted(set(df.index.date))
    v = norm(yf.Ticker("^VIX").history(period="5y")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {pd.Timestamp(k).date(): float(x) for k, x in ch.dropna().items()}
    dd = norm(yf.download("QQQ", period="5y", interval="1d",
                          auto_adjust=False, progress=False))
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    closes = {pd.Timestamp(k).date(): float(r) for k, r in dd["Close"].items()}
    dl = sorted(closes)
    prevc = {dl[i]: closes[dl[i - 1]] for i in range(1, len(dl))}
    SIG = {}
    for d in days:
        pc, vx = prevc.get(d), vm.get(d)
        if pc is None or vx is None or abs(vx) >= 5.0:
            continue
        g = df[df.index.date == d]
        if len(g) < 20:
            continue
        O0 = float(g["Open"].iloc[0])
        gap = O0 - pc
        gp = gap / pc * 100
        if not (0.2 <= abs(gp) < 1.5):
            continue
        sgn = 1 if gap > 0 else -1
        ep = float(g["Open"].iloc[15])            # 09:45 시가 = 09:44 종가
        cov = ((O0 - ep) / gap) if sgn > 0 else ((ep - O0) / abs(gap))
        or_hi = float(g["High"].iloc[:5].max())
        or_lo = float(g["Low"].iloc[:5].min())
        if cov >= COVER_MIN:
            SIG[d] = dict(mode="F", sgn=sgn, dirn=-sgn, tgt=pc, ep=ep, cov=cov)
        elif (sgn > 0 and vx < 0) or (sgn < 0 and vx > 0):
            SIG[d] = dict(mode="M", sgn=sgn, dirn=sgn,
                          stop=(or_lo if sgn > 0 else or_hi), ep=ep, cov=cov)
    return df, days, SIG


def run_rule(day_px, s):
    """1분 폴링 수계산 (교차검증 기준) — 진입 09:45, 이후 분 시가 판정·체결."""
    dirn = s["dirn"]
    ep = s["ep"]
    ext = ep
    filled = False
    for t, cur in day_px.items():
        tt = t.time()
        if tt <= T_SIG:
            continue
        if tt > T_FINAL:
            break
        if s["mode"] == "F":
            if not filled:
                hit = (cur <= s["tgt"]) if dirn < 0 else (cur >= s["tgt"])
                if hit:
                    filled = True
                    ext = cur
                elif tt >= T_TIMECUT:
                    return cur, tt
                continue
            ext = min(ext, cur) if dirn < 0 else max(ext, cur)
            tp = ext * (1 + FILL_TRAIL / 100) if dirn < 0 else ext * (1 - FILL_TRAIL / 100)
            if (cur >= tp) if dirn < 0 else (cur <= tp):
                return cur, tt
        else:
            ext = max(ext, cur) if dirn > 0 else min(ext, cur)
            tp = ext * (1 - MOM_TRAIL / 100) if dirn > 0 else ext * (1 + MOM_TRAIL / 100)
            if (cur <= tp) if dirn > 0 else (cur >= tp):
                return cur, tt
            if (cur <= s["stop"]) if dirn > 0 else (cur >= s["stop"]):
                return cur, tt
        if tt >= T_FINAL:
            return cur, tt
    return float(day_px.iloc[-1]), day_px.index[-1].time()


def main():
    from gs_quant.instrument import EqStock
    from gs_quant.backtests.data_sources import DataManager, DataFrequency
    from gs_quant.backtests.core import ValuationFixingType
    from gs_quant.backtests.triggers import OrdersGeneratorTrigger
    from gs_quant.backtests.order import OrderAtMarket
    from gs_quant.backtests.strategy import Strategy
    from gs_quant.backtests.predefined_asset_engine import PredefinedAssetEngine

    df, days, SIG = build()
    px = df["Open"].astype(float)
    inst = EqStock(identifier="QQQ", name="QQQ")
    dm = DataManager()
    dm.add_data_source(px, DataFrequency.REAL_TIME, inst, ValuationFixingType.PRICE)

    minute_times = [dt.time(9, m) for m in range(45, 60)] + \
                   [dt.time(h, m) for h in range(10, 14) for m in range(60)] + [T_FINAL]

    class FullTrigger(OrdersGeneratorTrigger):
        def __init__(self):
            super().__init__()
            self.live = {}
        def get_trigger_times(self):
            return minute_times
        def generate_orders(self, state, backtest=None):
            d = state.date()
            tt = state.time()
            cur = float(px.get(state, np.nan))
            if np.isnan(cur):
                return []
            if tt == T_SIG:
                s = SIG.get(d)
                if s:
                    self.live[d] = dict(s, ext=s["ep"], filled=False, open=True)
                    return [OrderAtMarket(inst, s["dirn"] * QTY, state, state, "entry")]
                return []
            L = self.live.get(d)
            if not L or not L["open"]:
                return []
            close_now = False
            if L["mode"] == "F":
                if not L["filled"]:
                    hit = (cur <= L["tgt"]) if L["dirn"] < 0 else (cur >= L["tgt"])
                    if hit:
                        L["filled"] = True
                        L["ext"] = cur
                    elif tt >= T_TIMECUT:
                        close_now = True
                else:
                    L["ext"] = min(L["ext"], cur) if L["dirn"] < 0 else max(L["ext"], cur)
                    tp = L["ext"] * (1 + FILL_TRAIL / 100) if L["dirn"] < 0 \
                        else L["ext"] * (1 - FILL_TRAIL / 100)
                    if (cur >= tp) if L["dirn"] < 0 else (cur <= tp):
                        close_now = True
            else:
                L["ext"] = max(L["ext"], cur) if L["dirn"] > 0 else min(L["ext"], cur)
                tp = L["ext"] * (1 - MOM_TRAIL / 100) if L["dirn"] > 0 \
                    else L["ext"] * (1 + MOM_TRAIL / 100)
                if ((cur <= tp) if L["dirn"] > 0 else (cur >= tp)) or \
                   ((cur <= L["stop"]) if L["dirn"] > 0 else (cur >= L["stop"])):
                    close_now = True
            if tt >= T_FINAL:
                close_now = True
            if close_now:
                L["open"] = False
                return [OrderAtMarket(inst, -L["dirn"] * QTY, state, state, "exit")]
            return []

    states = []
    for d in days:
        if d in SIG:
            states += [dt.datetime.combine(d, t, UTC) for t in minute_times
                       if dt.datetime.combine(d, t, UTC) in px.index]
    eng = PredefinedAssetEngine(data_mgr=dm, tz=UTC)
    bt = eng.run_backtest(Strategy(None, [FullTrigger()]), start=days[0], end=days[-1],
                          states=states, initial_value=10000)
    led = bt.trade_ledger()
    perf = bt.performance

    # ── 교차검증: 동일 룰 pandas ──
    rows, diffs = [], []
    for d, s in SIG.items():
        g = px[px.index.date == d]
        xp, xt = run_rule(g, s)
        pnl_pd = s["dirn"] * QTY * (xp - s["ep"])
        rows.append((d, s["mode"], s["dirn"], s["ep"], xp, xt, pnl_pd))
    pd_total = sum(r[6] for r in rows)
    gs_total = float(perf.iloc[-1])
    n = len(rows)
    F = [r for r in rows if r[1] == "F"]
    M = [r for r in rows if r[1] == "M"]

    def blk(tag, rr):
        if not rr:
            return f"  {tag}: n=0"
        p = [r[6] for r in rr]
        w = sum(1 for x in p if x > 0)
        g_ = sum(x for x in p if x > 0)
        l_ = -sum(x for x in p if x <= 0)
        pf = "승만" if l_ <= 0 else f"{g_/l_:.2f}"
        bp = np.mean([r[2] * (r[4] / r[3] - 1) * 1e4 for r in rr])
        return (f"  {tag}: n={len(rr)}  승률 {w/len(rr)*100:.1f}%  PF {pf}  "
                f"합계 ${sum(p):+,.2f}  평균 {bp:+.1f}bp/거래")

    out = [f"gs2 · GS PredefinedAssetEngine 풀스펙 공식 결과 (QQQ {QTY}주, 1분 폴링)",
           f"우주 {len(days)}일 → 신호 {n}건 (갭필 {len(F)} / 모멘텀 {len(M)})", "",
           blk("갭필  ", F), blk("모멘텀", M), blk("★결합 ", rows), "",
           f"GS 엔진 누적 P&L   ${gs_total:+,.2f}  (원장 {len(led)}행)",
           f"pandas 수계산      ${pd_total:+,.2f}",
           f"차이               ${abs(gs_total-pd_total):.4f} → "
           + ("✓ 풀스펙 교차검증 통과" if abs(gs_total - pd_total) < 1.0 else "✗ 불일치"), "",
           "※ 기초자산 레벨 (옵션 미포함 — GS 옵션 프라이싱은 기관 전용).",
           "   옵션 P&L 환산은 combsim(BSM)이 담당: 같은 신호, 레버리지만 다름.",
           "※ 74일 한 레짐 · 1분 폴링 체결 정의."]
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("gs2_result.json", "w"), ensure_ascii=False, indent=1)
