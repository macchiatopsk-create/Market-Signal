"""GS Quant independent execution-engine cross-check for the QQQ gap strategy.

Purpose
-------
Use Goldman Sachs' open-source gs_quant PredefinedAssetEngine with our own
stored QQQ 1-minute data. Signal/exit timestamps are generated with the same
strict path rules as gapafter.py, while gs_quant independently processes
orders, fills, holdings and cash P&L.

Important: this validates execution/bookkeeping against a second engine. It
is NOT Goldman market data or Goldman option pricing. QQQ is represented by a
synthetic exchange-traded priceable whose price series is our stored QQQ close.
"""

import datetime as dt
import glob
import json
import traceback

import numpy as np
import pandas as pd
import yfinance as yf

import gs_quant
from gs_quant.backtests.actions import Action
from gs_quant.backtests.core import ValuationFixingType
from gs_quant.backtests.data_sources import DataManager
from gs_quant.backtests.order import OrderAtMarket
from gs_quant.backtests.predefined_asset_engine import PredefinedAssetEngine
from gs_quant.backtests.strategy import Strategy
from gs_quant.backtests.triggers import OrdersGeneratorTrigger
from gs_quant.data import DataFrequency
from gs_quant.instrument import IRBondFuture

DATA = "data/1m/QQQ_*.csv.gz"
GAP_MIN = 0.2
GAP_MAX = 1.5
COVER_MIN = 0.40
VIX_OPEN_MAX = 5.0
NOFILL_CUT = "11:30"
FINAL_CUT = "14:00"
TRAIL = 0.15


def load_1m():
    frames = []
    for path in sorted(glob.glob(DATA)):
        x = pd.read_csv(path, compression="gzip")
        if "ts" not in x.columns:
            continue
        x["ts"] = pd.to_datetime(x["ts"]).dt.tz_localize(None)
        x = x.set_index("ts")
        frames.append(x[["Open", "High", "Low", "Close"]])
    if not frames:
        return pd.DataFrame()
    x = pd.concat(frames).sort_index()
    return x[~x.index.duplicated(keep="last")]


def load_daily():
    d = yf.download("QQQ", period="2y", interval="1d", auto_adjust=False, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.dropna()
    d.index = pd.to_datetime(d.index).tz_localize(None)
    pc = d["Close"].shift(1)

    v = yf.Ticker("^VIX").history(period="2y")[["Open", "Close"]].dropna()
    try:
        v.index = v.index.tz_localize(None)
    except TypeError:
        pass
    v.index = pd.to_datetime(v.index).normalize()
    vm = (v["Open"] / v["Close"].shift(1) - 1) * 100

    out = {}
    for i in d.index:
        if pd.isna(pc.loc[i]):
            continue
        out[str(i.date())] = {
            "prev": float(pc.loc[i]),
            "open": float(d.loc[i, "Open"]),
            "gap": float((d.loc[i, "Open"] / pc.loc[i] - 1) * 100),
            "vix": float(vm.get(i.normalize(), np.nan)),
        }
    return out


def pnl_pct(entry, exit_, direction):
    # direction +1 = long QQQ (gap down -> CALL proxy)
    # direction -1 = short QQQ (gap up -> PUT proxy)
    return direction * (exit_ - entry) / entry * 100


def build_trade(day, info, bars, minutes, trail=TRAIL):
    date = pd.Timestamp(day).date()
    g = bars[bars.index.date == date]
    if g.empty:
        return None

    st = pd.Timestamp(f"{day} 09:30")
    en = st + pd.Timedelta(minutes=minutes)
    w = g[(g.index >= st) & (g.index < en)]
    if len(w) < minutes - 1:
        return None

    if not np.isfinite(info["vix"]) or abs(info["vix"]) >= VIX_OPEN_MAX:
        return None
    if abs(info["gap"]) < GAP_MIN or abs(info["gap"]) >= GAP_MAX:
        return None

    first_open = float(w["Open"].iloc[0])
    entry = float(w["Close"].iloc[-1])
    entry_ts = pd.Timestamp(w.index[-1]).to_pydatetime()
    gap_dollars = info["open"] - info["prev"]
    gap_up = gap_dollars > 0
    direction = -1 if gap_up else 1
    cover = ((first_open - entry) / gap_dollars) if gap_up else ((entry - first_open) / abs(gap_dollars))
    if cover < COVER_MIN:
        return None

    target = float(info["prev"])
    prefilled = float(w["Low"].min()) <= target if gap_up else float(w["High"].max()) >= target
    if prefilled:
        return {"day": day, "prefilled": True, "cover": cover}

    fill_ts = None
    for ts, r in g[g.index >= en].iterrows():
        hit = float(r["Low"]) <= target if gap_up else float(r["High"]) >= target
        if hit:
            fill_ts = pd.Timestamp(ts)
            break

    if fill_ts is None or fill_ts.strftime("%H:%M") > NOFILL_CUT:
        cut = g[g.index <= pd.Timestamp(f"{day} {NOFILL_CUT}")]
        if cut.empty:
            return None
        exit_ts = pd.Timestamp(cut.index[-1])
        close_exit = float(cut["Close"].iloc[-1])
        return {
            "day": day,
            "prefilled": False,
            "filled": False,
            "cover": cover,
            "direction": direction,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts.to_pydatetime(),
            "entry": entry,
            "ideal_exit": close_exit,
            "close_exit": close_exit,
            "reason": "NO_FILL_1130",
            "ideal_pct": pnl_pct(entry, close_exit, direction),
            "close_pct": pnl_pct(entry, close_exit, direction),
        }

    # Strict path rule: the fill bar is excluded. On every later bar, check the
    # stop implied by the PRIOR favorable extreme before updating that extreme.
    best = target
    ideal_exit = None
    close_exit = None
    exit_ts = None
    reason = None
    post = g[g.index > fill_ts]

    for ts, r in post.iterrows():
        ts = pd.Timestamp(ts)
        stop = best * (1 + trail / 100) if gap_up else best * (1 - trail / 100)
        stop_hit = float(r["High"]) >= stop if gap_up else float(r["Low"]) <= stop
        if stop_hit:
            exit_ts = ts
            ideal_exit = float(stop)
            close_exit = float(r["Close"])
            reason = "TRAIL"
            break

        best = min(best, float(r["Low"])) if gap_up else max(best, float(r["High"]))
        if ts.strftime("%H:%M") >= FINAL_CUT:
            exit_ts = ts
            ideal_exit = float(r["Close"])
            close_exit = float(r["Close"])
            reason = "14:00_CUT"
            break

    if exit_ts is None:
        rr = post[post.index <= pd.Timestamp(f"{day} {FINAL_CUT}")]
        if rr.empty:
            rr = post
        if rr.empty:
            return None
        exit_ts = pd.Timestamp(rr.index[-1])
        ideal_exit = float(rr["Close"].iloc[-1])
        close_exit = ideal_exit
        reason = "14:00_CUT"

    return {
        "day": day,
        "prefilled": False,
        "filled": True,
        "cover": cover,
        "direction": direction,
        "entry_ts": entry_ts,
        "exit_ts": exit_ts.to_pydatetime(),
        "fill_ts": fill_ts.to_pydatetime(),
        "entry": entry,
        "ideal_exit": ideal_exit,
        "close_exit": close_exit,
        "reason": reason,
        "ideal_pct": pnl_pct(entry, ideal_exit, direction),
        "close_pct": pnl_pct(entry, close_exit, direction),
    }


def pf(values):
    gp = sum(x for x in values if x > 0)
    gl = -sum(x for x in values if x <= 0)
    return gp / gl if gl else float("inf")


def pf_text(x):
    return "NA(no losses)" if not np.isfinite(x) else f"{x:.2f}"


def max_drawdown_pct(trade_returns_pct):
    equity = 1.0
    peak = 1.0
    mdd = 0.0
    for r in trade_returns_pct:
        equity *= 1 + r / 100
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)
    return mdd * 100


def trimmed_pf(values, n_remove):
    if len(values) <= n_remove:
        return np.nan
    idx = np.argsort(values)[::-1]
    keep = np.ones(len(values), dtype=bool)
    keep[idx[:n_remove]] = False
    return pf([values[i] for i in range(len(values)) if keep[i]])


def half_pf(values):
    mid = len(values) // 2
    if mid == 0 or mid == len(values):
        return np.nan, np.nan
    return pf(values[:mid]), pf(values[mid:])


class ScheduleTrigger(OrdersGeneratorTrigger):
    def __init__(self, schedule, instrument):
        self.schedule = schedule
        self.instrument = instrument
        super().__init__(actions=[Action()])

    def get_trigger_times(self):
        return sorted({ts.time() for ts in self.schedule})

    def generate_orders(self, state, backtest=None):
        items = self.schedule.get(pd.Timestamp(state).to_pydatetime(), [])
        return [
            OrderAtMarket(
                instrument=self.instrument,
                quantity=item["qty"],
                generation_time=state,
                execution_datetime=state,
                source=item["source"],
            )
            for item in items
        ]


def run_gs_engine(trades, bars):
    if not trades:
        return {"ok": False, "error": "no trades"}

    instrument = IRBondFuture(currency="USD", name="QQQProxy")
    close_series = bars["Close"].astype(float).copy()

    dm = DataManager()
    dm.add_data_source(close_series, DataFrequency.REAL_TIME, instrument, ValuationFixingType.PRICE)

    # Build one long/short order at entry and the exact opposite at exit.
    schedule = {}
    for t in trades:
        e = pd.Timestamp(t["entry_ts"]).to_pydatetime()
        x = pd.Timestamp(t["exit_ts"]).to_pydatetime()
        schedule.setdefault(e, []).append({"qty": t["direction"], "source": f"ENTRY_{t['day']}"})
        schedule.setdefault(x, []).append({"qty": -t["direction"], "source": f"EXIT_{t['day']}"})

    trigger = ScheduleTrigger(schedule, instrument)
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    engine = PredefinedAssetEngine(data_mgr=dm, calendars="weekend", tz=dt.timezone.utc)

    days = sorted({pd.Timestamp(x).date() for x in bars.index})
    bt = engine.run_backtest(
        strategy=strategy,
        start=days[0],
        end=days[-1],
        states=days,
        initial_value=100.0,
    )

    perf = bt.performance
    final_perf = float(perf.iloc[-1]) if hasattr(perf, "iloc") else float(list(perf.values())[-1])
    engine_abs_pnl = final_perf - 100.0
    manual_abs_pnl = sum(t["direction"] * (t["close_exit"] - t["entry"]) for t in trades)

    return {
        "ok": True,
        "engine_final": final_perf,
        "engine_abs_pnl": engine_abs_pnl,
        "manual_abs_pnl": manual_abs_pnl,
        "difference": engine_abs_pnl - manual_abs_pnl,
        "matched": abs(engine_abs_pnl - manual_abs_pnl) < 1e-8,
        "orders": 2 * len(trades),
    }


def summarize_track(name, trades, prefilled, engine_result):
    ideal = [t["ideal_pct"] for t in trades]
    close = [t["close_pct"] for t in trades]
    filled = sum(t["filled"] for t in trades)
    nofill = len(trades) - filled
    h1i, h2i = half_pf(ideal)
    h1c, h2c = half_pf(close)

    lines = [
        f"[{name}] tradable n={len(trades)} / filled={filled} / 11:30 no-fill={nofill} / prefilled-excluded={prefilled}",
        f"  IDEAL stop fill : win={sum(x>0 for x in ideal)}/{len(ideal)} PF={pf_text(pf(ideal))} avg={np.mean(ideal):+.3f}% med={np.median(ideal):+.3f}% MDD={max_drawdown_pct(ideal):.3f}%",
        f"  GS bar-close    : win={sum(x>0 for x in close)}/{len(close)} PF={pf_text(pf(close))} avg={np.mean(close):+.3f}% med={np.median(close):+.3f}% MDD={max_drawdown_pct(close):.3f}%",
        f"  top1 removed PF : ideal={pf_text(trimmed_pf(ideal,1))} / close={pf_text(trimmed_pf(close,1))}",
        f"  top2 removed PF : ideal={pf_text(trimmed_pf(ideal,2))} / close={pf_text(trimmed_pf(close,2))}",
        f"  half PF         : ideal={pf_text(h1i)}/{pf_text(h2i)} / close={pf_text(h1c)}/{pf_text(h2c)}",
    ]
    if engine_result.get("ok"):
        lines += [
            f"  GS engine check : final={engine_result['engine_final']:.6f} absPnL={engine_result['engine_abs_pnl']:+.6f}",
            f"                    manual={engine_result['manual_abs_pnl']:+.6f} diff={engine_result['difference']:+.12f} matched={engine_result['matched']}",
        ]
    else:
        lines.append(f"  GS engine check : FAILED {engine_result.get('error')}")
    return lines


def main():
    bars = load_1m()
    if bars.empty:
        raise RuntimeError("no stored QQQ 1m files found")
    daily = load_daily()
    have = sorted({str(x) for x in bars.index.date})

    out = [
        f"gs_quant={getattr(gs_quant, '__version__', 'unknown')}",
        f"stored QQQ 1m trading days={len(have)}",
        "RULES: gap 0.2~1.5% / |VIX open-prevclose|<5% / cover>=40% / first-bar close entry",
        "       no-fill 11:30 cut / after fill 0.15% trail / 14:00 final cut / fill bar excluded",
        "GS engine uses our QQQ minute CLOSE series and OrderAtMarket. Trail-hit exits execute at that minute's CLOSE,",
        "while IDEAL uses the exact theoretical stop price. Thus GS bar-close is an execution robustness cross-check.",
        "",
    ]

    json_tracks = {}
    for minutes, name in ((5, "5m"), (15, "15m"), (60, "1h")):
        trades = []
        prefilled = 0
        for day in have:
            info = daily.get(day)
            if not info:
                continue
            r = build_trade(day, info, bars, minutes)
            if not r:
                continue
            if r.get("prefilled"):
                prefilled += 1
            else:
                trades.append(r)

        engine_result = run_gs_engine(trades, bars) if trades else {"ok": False, "error": "no trades"}
        out.extend(summarize_track(name, trades, prefilled, engine_result) if trades else [f"[{name}] n=0"])
        out.append("")
        json_tracks[name] = {
            "trades": [
                {
                    **{k: v for k, v in t.items() if k not in ("entry_ts", "exit_ts", "fill_ts")},
                    "entry_ts": str(t.get("entry_ts")),
                    "exit_ts": str(t.get("exit_ts")),
                    "fill_ts": str(t.get("fill_ts")) if t.get("fill_ts") else None,
                }
                for t in trades
            ],
            "prefilled_excluded": prefilled,
            "engine": engine_result,
        }

    return "\n".join(out), json_tracks


if __name__ == "__main__":
    try:
        report, tracks = main()
        payload = {
            "at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "report": report,
            "tracks": tracks,
        }
    except Exception:
        report = "FAILED\n" + traceback.format_exc()
        payload = {"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": report}
    print(report)
    with open("gsquant_gap_result.json", "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
