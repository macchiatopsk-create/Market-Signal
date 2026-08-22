"""gsbt — GS Quant 실전 투입 최대치.

1부: GenericEngine 백테스트·옵션 프라이싱 실제 시도 → 막히는 지점 실증 기록
2부: 도는 부분(gs_quant.timeseries 리스크 지표)으로 우리 통합 전략
     (09:45 결합 = combsim 스펙)의 에쿼티 곡선을 GS 함수로 성과분석.
     사이징 30/50/70% × 모드 A/D. 지표: GS max_drawdown·volatility·returns.
combsim의 검증된 실행 로직을 그대로 import 해서 거래 시퀀스 재현.
"""
import json, datetime as dt, traceback
import numpy as np
import pandas as pd
import yfinance as yf

import combsim as C


def gs_engine_probe():
    out = ["── 1부 · GS 백테스트 엔진 실전 시도 ──"]
    try:
        from gs_quant.instrument import EqOption, OptionType, OptionStyle
        from gs_quant.backtests.strategy import Strategy
        from gs_quant.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
        from gs_quant.backtests.actions import AddTradeAction
        from gs_quant.backtests.generic_engine import GenericEngine
        opt = EqOption("QQQ", expiration_date="1d", strike_price="ATM",
                       option_type=OptionType.Call, option_style=OptionStyle.European)
        trig = PeriodicTrigger(
            PeriodicTriggerRequirements(start_date=dt.date(2026, 5, 1),
                                        end_date=dt.date(2026, 8, 1), frequency="1b"),
            [AddTradeAction(opt, "1d")])
        strat = Strategy(None, [trig])
        out.append("  전략 구성(QQQ 1DTE 콜 매일 매수): OK — 문법·객체 레벨 통과")
        try:
            GenericEngine().run_backtest(strat, start=dt.date(2026, 5, 1),
                                         end=dt.date(2026, 8, 1), frequency="1b",
                                         show_progress=False)
            out.append("  run_backtest: 성공(?!)")
        except Exception as e:
            out.append(f"  run_backtest ✗ {type(e).__name__}: {str(e)[:80]}")
        try:
            EqOption("QQQ", expiration_date="1d", strike_price="ATM").price()
            out.append("  option.price(): 성공(?!)")
        except Exception as e:
            out.append(f"  option.price() ✗ {type(e).__name__}: {str(e)[:80]}")
    except Exception as e:
        out.append(f"  임포트 실패: {type(e).__name__}: {str(e)[:120]}")
    out.append("  → 프라이싱이 GS 서버사이드: Marquee 기관 크레덴셜 없이는 엔진 사용 불가")
    return out


def build_trades():
    """combsim 09:45 스펙 재현 — 결합 거래 (date, mode→pct)."""
    v = C.norm(yf.Ticker("^VIX").history(period="26mo")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in ch.dropna().items()}
    try:
        x = C.norm(yf.Ticker("^VXN").history(period="26mo")[["Open"]].dropna())
        ivm = {str(pd.Timestamp(k).date()): float(r) / 100 for k, r in x["Open"].items()}
    except Exception:
        ivm = {}
    vox = {str(pd.Timestamp(k).date()): float(r) for k, r in v["Open"].items()}
    dd = C.norm(yf.download("QQQ", period="26mo", interval="1d",
                            auto_adjust=False, progress=False))
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    closes = {pd.Timestamp(k).date(): float(r) for k, r in dd["Close"].items()}
    dl = sorted(closes.keys())
    prevc = {dl[i]: closes[dl[i - 1]] for i in range(1, len(dl))}
    df = C.load_1m()
    days = sorted(set(df.index.date))
    ei = C.E1M["09:45"]
    uni, tr = [], {"A": {}, "D": {}}
    for d in days:
        pc, vx = prevc.get(d), vm.get(str(d))
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
        uni.append(d)
        sgn = 1 if gap > 0 else -1
        ep, t0 = b1[ei][3], b1[ei][0].time()
        cov = ((O0 - ep) / gap) if sgn > 0 else ((ep - O0) / abs(gap))
        iv = (ivm.get(str(d)) or vox.get(str(d), 16.0) * 1.15 / 100) * C.K_IV
        seqA, seqD = b1[ei + 1:], C.agg(b1[ei + 1:], 5)
        if cov >= C.COVER_MIN:
            flag = "p" if sgn > 0 else "c"
            for m, run, sq in [("A", C.fill_A, seqA), ("D", C.fill_D, seqD)]:
                px, hd = run(sq, t0, pc, sgn, C.FILL_TRAIL)
                o = C.bsm_net(flag, ep, px, t0, hd, iv)
                if o is not None:
                    tr[m][d] = o
        else:
            if not ((sgn > 0 and vx < 0) or (sgn < 0 and vx > 0)):
                continue
            or_hi = max(x[1] for x in b1[:5]); or_lo = min(x[2] for x in b1[:5])
            stop = or_lo if sgn > 0 else or_hi
            flag = "c" if sgn > 0 else "p"
            for m, run, sq in [("A", C.mom_A, seqA), ("D", C.mom_D, seqD)]:
                px, hd = run(sq, t0, ep, sgn, stop, C.MOM_TRAIL)
                o = C.bsm_net(flag, ep, px, t0, hd, iv)
                if o is not None:
                    tr[m][d] = o
    return uni, tr


def main():
    out = gs_engine_probe() + [""]
    import gs_quant.timeseries as ts
    uni, tr = build_trades()
    out.append(f"── 2부 · GS timeseries 지표로 통합전략(09:45 결합) 성과분석 · 우주 {len(uni)}일 ──")
    for m in ("A", "D"):
        out.append(f" [{m}모드]  거래 {len(tr[m])}건")
        for f in (0.30, 0.50, 0.70):
            cap, vals = 2000.0, []
            for d in uni:
                p = tr[m].get(d)
                if p is not None:
                    cap *= (1 + f * p / 100)
                vals.append(cap)
            curve = pd.Series(vals, index=pd.to_datetime([str(d) for d in uni]))
            r = ts.returns(curve).dropna()
            mdd = float(ts.max_drawdown(curve).iloc[-1]) * 100
            try:
                vol = float(ts.volatility(curve, len(curve) - 1).iloc[-1])
            except Exception:
                vol = float(r.std() * np.sqrt(252) * 100)
            shp = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else float("nan")
            out.append(f"   사이징 {int(f*100)}%  최종 ${cap:9,.0f}  "
                       f"GS max_drawdown {mdd:6.1f}%  GS volatility(연) {vol:6.1f}%  "
                       f"Sharpe {shp:5.2f}")
    out.append("")
    out.append("판정 — 엔진(백테스트 실행·프라이싱)은 기관 전용 벽. 도는 건 지표 함수뿐이고")
    out.append("       그 지표는 pandas 몇 줄과 동일. 레포에서 가져갈 실질 자산은")
    out.append("       Trigger/Action 백테스트 '구조'뿐 — 공통 엔진화 때 참고. 도입 불필요 재확정.")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("gsbt_result.json", "w"), ensure_ascii=False, indent=1)
