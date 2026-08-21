"""Post-gap-fill exit sweep using stored QQQ 1m data + GS Quant bookkeeping cross-check.

Signal is frozen: gap 0.2~1.5%, |VIX open-prevclose|<5%, cover>=40%,
entry at first-window close, prefilled entry-window days excluded, no-fill cut 11:30.
Only the POST-FILL exit changes.
"""
import datetime as dt
import json
import math
import traceback

import numpy as np
import pandas as pd

from gs_quant.backtests.actions import Action
from gs_quant.backtests.core import ValuationFixingType
from gs_quant.backtests.data_sources import DataManager
from gs_quant.backtests.order import OrderAtMarket
from gs_quant.backtests.predefined_asset_engine import PredefinedAssetEngine
from gs_quant.backtests.strategy import Strategy
from gs_quant.backtests.triggers import OrdersGeneratorTrigger
from gs_quant.data import DataFrequency
from gs_quant.instrument import IRBondFuture

import gsquant_gaptest as base

FINAL_CUT = "14:00"
NOFILL_CUT = "11:30"


def pct(entry, exit_px, direction):
    return direction * (exit_px - entry) / entry * 100.0


def get_signal(day, info, bars, minutes):
    date = pd.Timestamp(day).date()
    g = bars[bars.index.date == date]
    if g.empty:
        return None
    st = pd.Timestamp(f"{day} 09:30")
    en = st + pd.Timedelta(minutes=minutes)
    w = g[(g.index >= st) & (g.index < en)]
    if len(w) < minutes - 1:
        return None
    if not np.isfinite(info["vix"]) or abs(info["vix"]) >= 5.0:
        return None
    if abs(info["gap"]) < 0.2 or abs(info["gap"]) >= 1.5:
        return None

    first_open = float(w["Open"].iloc[0])
    entry = float(w["Close"].iloc[-1])
    entry_ts = pd.Timestamp(w.index[-1])
    gap_dollars = info["open"] - info["prev"]
    gap_up = gap_dollars > 0
    direction = -1 if gap_up else 1
    cover = ((first_open - entry) / gap_dollars) if gap_up else ((entry - first_open) / abs(gap_dollars))
    if cover < 0.40:
        return None

    target = float(info["prev"])
    prefilled = float(w["Low"].min()) <= target if gap_up else float(w["High"].max()) >= target
    if prefilled:
        return {"prefilled": True, "day": day, "cover": cover}

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
        exit_px = float(cut["Close"].iloc[-1])
        return {
            "prefilled": False, "filled": False, "day": day, "cover": cover,
            "entry": entry, "entry_ts": entry_ts, "direction": direction,
            "target": target, "gap_up": gap_up, "g": g,
            "nofill_ts": exit_ts, "nofill_px": exit_px,
        }

    return {
        "prefilled": False, "filled": True, "day": day, "cover": cover,
        "entry": entry, "entry_ts": entry_ts, "direction": direction,
        "target": target, "gap_up": gap_up, "g": g, "fill_ts": fill_ts,
    }


def cut_bar(g, day):
    z = g[g.index <= pd.Timestamp(f"{day} {FINAL_CUT}")]
    return z.iloc[-1], pd.Timestamp(z.index[-1])


def runner_trail(sig, trail):
    g, fill_ts, target, gap_up = sig["g"], sig["fill_ts"], sig["target"], sig["gap_up"]
    post = g[g.index > fill_ts]
    best = target
    for ts, r in post.iterrows():
        ts = pd.Timestamp(ts)
        stop = best * (1 + trail / 100) if gap_up else best * (1 - trail / 100)
        hit = float(r["High"]) >= stop if gap_up else float(r["Low"]) <= stop
        if hit:
            return float(stop), float(r["Close"]), ts, "TRAIL"
        best = min(best, float(r["Low"])) if gap_up else max(best, float(r["High"]))
        if ts.strftime("%H:%M") >= FINAL_CUT:
            return float(r["Close"]), float(r["Close"]), ts, "14:00"
    r, ts = cut_bar(g, sig["day"])
    return float(r["Close"]), float(r["Close"]), ts, "14:00"


def runner_fixed(sig, extension):
    g, fill_ts, target, gap_up = sig["g"], sig["fill_ts"], sig["target"], sig["gap_up"]
    post = g[g.index > fill_ts]
    px_target = target * (1 - extension / 100) if gap_up else target * (1 + extension / 100)
    for ts, r in post.iterrows():
        ts = pd.Timestamp(ts)
        hit = float(r["Low"]) <= px_target if gap_up else float(r["High"]) >= px_target
        if hit:
            return float(px_target), float(r["Close"]), ts, "FIXED"
        if ts.strftime("%H:%M") >= FINAL_CUT:
            return float(r["Close"]), float(r["Close"]), ts, "14:00"
    r, ts = cut_bar(g, sig["day"])
    return float(r["Close"]), float(r["Close"]), ts, "14:00"


def make_trade(sig, kind, value=None):
    entry, direction = sig["entry"], sig["direction"]
    if not sig["filled"]:
        p = pct(entry, sig["nofill_px"], direction)
        return {
            "day": sig["day"], "filled": False, "cover": sig["cover"],
            "ideal_pct": p, "close_pct": p,
            "legs_close": [(1.0, sig["nofill_ts"], sig["nofill_px"])],
            "entry": entry, "entry_ts": sig["entry_ts"], "direction": direction,
            "reason": "NO_FILL_1130",
        }

    fill_ts, fill_px = sig["fill_ts"], sig["target"]
    fillbar_close = float(sig["g"].loc[fill_ts, "Close"])

    if kind == "fill":
        ideal_p = pct(entry, fill_px, direction)
        close_p = pct(entry, fillbar_close, direction)
        legs = [(1.0, fill_ts, fillbar_close)]
        reason = "FILL"
    elif kind == "trail":
        ideal_exit, close_exit, exit_ts, reason = runner_trail(sig, value)
        ideal_p = pct(entry, ideal_exit, direction)
        close_p = pct(entry, close_exit, direction)
        legs = [(1.0, exit_ts, close_exit)]
    elif kind == "fixed":
        ideal_exit, close_exit, exit_ts, reason = runner_fixed(sig, value)
        ideal_p = pct(entry, ideal_exit, direction)
        close_p = pct(entry, close_exit, direction)
        legs = [(1.0, exit_ts, close_exit)]
    elif kind == "partial_trail":
        ideal_exit, close_exit, exit_ts, reason2 = runner_trail(sig, value)
        fill_ideal = pct(entry, fill_px, direction)
        runner_ideal = pct(entry, ideal_exit, direction)
        fill_close = pct(entry, fillbar_close, direction)
        runner_close = pct(entry, close_exit, direction)
        ideal_p = 0.5 * fill_ideal + 0.5 * runner_ideal
        close_p = 0.5 * fill_close + 0.5 * runner_close
        legs = [(0.5, fill_ts, fillbar_close), (0.5, exit_ts, close_exit)]
        reason = f"50FILL+50_{reason2}"
    elif kind == "partial_fixed":
        ideal_exit, close_exit, exit_ts, reason2 = runner_fixed(sig, value)
        ideal_p = 0.5 * pct(entry, fill_px, direction) + 0.5 * pct(entry, ideal_exit, direction)
        close_p = 0.5 * pct(entry, fillbar_close, direction) + 0.5 * pct(entry, close_exit, direction)
        legs = [(0.5, fill_ts, fillbar_close), (0.5, exit_ts, close_exit)]
        reason = f"50FILL+50_{reason2}"
    else:
        raise ValueError(kind)

    return {
        "day": sig["day"], "filled": True, "cover": sig["cover"],
        "ideal_pct": ideal_p, "close_pct": close_p, "legs_close": legs,
        "entry": entry, "entry_ts": sig["entry_ts"], "direction": direction,
        "reason": reason,
    }


def pf(vals):
    gp = sum(x for x in vals if x > 0)
    gl = -sum(x for x in vals if x <= 0)
    return gp / gl if gl else math.inf


def pft(x):
    if np.isnan(x):
        return "NA"
    return "NA(no losses)" if math.isinf(x) else f"{x:.2f}"


def trimmed(vals, n):
    if len(vals) <= n:
        return np.nan
    idx = np.argsort(vals)[::-1]
    keep = [v for i, v in enumerate(vals) if i not in set(idx[:n])]
    return pf(keep)


def half(vals):
    m = len(vals) // 2
    if m == 0 or m == len(vals):
        return np.nan, np.nan
    return pf(vals[:m]), pf(vals[m:])


def mdd(vals):
    eq = peak = 1.0
    dd = 0.0
    for v in vals:
        eq *= 1 + v / 100
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    return dd * 100


class ScheduleTrigger(OrdersGeneratorTrigger):
    def __init__(self, schedule, instrument):
        self.schedule = schedule
        self.instrument = instrument
        super().__init__(actions=[Action()])

    def get_trigger_times(self):
        return sorted({ts.time() for ts in self.schedule})

    def generate_orders(self, state, backtest=None):
        items = self.schedule.get(pd.Timestamp(state).to_pydatetime(), [])
        return [OrderAtMarket(
            instrument=self.instrument, quantity=item["qty"],
            generation_time=state, execution_datetime=state, source=item["source"]
        ) for item in items]


def gs_check(trades, bars):
    if not trades:
        return {"matched": False, "error": "no trades"}
    inst = IRBondFuture(currency="USD", name="QQQProxy")
    dm = DataManager()
    dm.add_data_source(bars["Close"].astype(float).copy(), DataFrequency.REAL_TIME, inst, ValuationFixingType.PRICE)
    schedule = {}
    manual = 0.0
    for t in trades:
        e = pd.Timestamp(t["entry_ts"]).to_pydatetime()
        direction = t["direction"]
        schedule.setdefault(e, []).append({"qty": direction, "source": f"ENTRY_{t['day']}"})
        manual -= direction * t["entry"]
        for frac, ts, px in t["legs_close"]:
            x = pd.Timestamp(ts).to_pydatetime()
            schedule.setdefault(x, []).append({"qty": -direction * frac, "source": f"EXIT_{t['day']}"})
            manual += direction * frac * px
    trig = ScheduleTrigger(schedule, inst)
    eng = PredefinedAssetEngine(data_mgr=dm, calendars="weekend", tz=dt.timezone.utc)
    days = sorted({pd.Timestamp(x).date() for x in bars.index})
    bt = eng.run_backtest(Strategy(None, triggers=[trig]), start=days[0], end=days[-1], states=days, initial_value=100.0)
    perf = bt.performance
    final = float(perf.iloc[-1]) if hasattr(perf, "iloc") else float(list(perf.values())[-1])
    diff = final - manual
    return {"matched": abs(diff) < 1e-8, "engine": final, "manual": manual, "diff": diff}


def summarize(label, trades, engine):
    ideal = [t["ideal_pct"] for t in trades]
    close = [t["close_pct"] for t in trades]
    h1i, h2i = half(ideal)
    h1c, h2c = half(close)
    return (
        f"{label:<20} n={len(trades)} | IDEAL win={sum(x>0 for x in ideal)}/{len(ideal)} PF={pft(pf(ideal))} avg={np.mean(ideal):+.3f}% med={np.median(ideal):+.3f}% MDD={mdd(ideal):.3f}% top1={pft(trimmed(ideal,1))} top2={pft(trimmed(ideal,2))} half={pft(h1i)}/{pft(h2i)}\n"
        f"{'':20}           CLOSE win={sum(x>0 for x in close)}/{len(close)} PF={pft(pf(close))} avg={np.mean(close):+.3f}% med={np.median(close):+.3f}% MDD={mdd(close):.3f}% top1={pft(trimmed(close,1))} top2={pft(trimmed(close,2))} half={pft(h1c)}/{pft(h2c)} | GS matched={engine.get('matched')} diff={engine.get('diff', float('nan')):+.9f}"
    )


def main():
    bars = base.load_1m()
    daily = base.load_daily()
    have = sorted({str(x) for x in bars.index.date})
    variants = [
        ("fill_now", "fill", None),
        ("trail_0.15", "trail", 0.15),
        ("trail_0.20", "trail", 0.20),
        ("trail_0.30", "trail", 0.30),
        ("fixed_+0.25", "fixed", 0.25),
        ("fixed_+0.50", "fixed", 0.50),
        ("50fill+trail0.20", "partial_trail", 0.20),
        ("50fill+trail0.30", "partial_trail", 0.30),
        ("50fill+fixed0.25", "partial_fixed", 0.25),
        ("50fill+fixed0.50", "partial_fixed", 0.50),
    ]

    out = [
        f"stored QQQ 1m trading days={len(have)}",
        "FROZEN SIGNAL: gap 0.2~1.5% / |VIX open-prevclose|<5% / cover>=40% / first-window close entry",
        "prefilled entry-window days excluded / no-fill 11:30 cut / post-fill tests only / fill bar excluded from runner / 14:00 final cut",
        "IDEAL = exact touched stop/target; CLOSE = triggering minute close. GS independently checks CLOSE-order bookkeeping.",
        "",
    ]
    result = {"tracks": {}}
    for minutes, name in ((5, "5m"), (15, "15m")):
        sigs = []
        prefilled = 0
        for day in have:
            info = daily.get(day)
            if not info:
                continue
            s = get_signal(day, info, bars, minutes)
            if not s:
                continue
            if s.get("prefilled"):
                prefilled += 1
            else:
                sigs.append(s)
        filled = sum(s["filled"] for s in sigs)
        out += [f"[{name}] tradable={len(sigs)} filled={filled} nofill={len(sigs)-filled} prefilled_excluded={prefilled}"]
        result["tracks"][name] = {"n": len(sigs), "filled": filled, "prefilled": prefilled, "variants": {}}
        for label, kind, value in variants:
            trades = [make_trade(s, kind, value) for s in sigs]
            eng = gs_check(trades, bars)
            out.append(summarize(label, trades, eng))
            result["tracks"][name]["variants"][label] = {
                "ideal": [t["ideal_pct"] for t in trades],
                "close": [t["close_pct"] for t in trades],
                "days": [t["day"] for t in trades],
                "gs": eng,
            }
        out.append("")
    report = "\n".join(out)
    print(report)
    payload = {"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": report, **result}
    with open("gsquant_exit_sweep_result.json", "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        txt = "FAILED\n" + traceback.format_exc()
        print(txt)
        with open("gsquant_exit_sweep_result.json", "w") as f:
            json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt}, f, ensure_ascii=False, indent=2)
        raise
