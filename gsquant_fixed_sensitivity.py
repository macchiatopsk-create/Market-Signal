"""Fixed post-gap-fill target sensitivity test.

Signal is deliberately frozen. Only the post-fill extension target changes.
Uses stored QQQ 1m data and GS Quant as an independent close-order bookkeeping check.
"""
import datetime as dt
import json
import math
import traceback

import numpy as np

import gsquant_exit_sweep as ex
import gsquant_gaptest as base

TARGETS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]


def pf(vals):
    gp = sum(x for x in vals if x > 0)
    gl = -sum(x for x in vals if x <= 0)
    return gp / gl if gl else math.inf


def pft(x):
    if isinstance(x, float) and np.isnan(x):
        return "NA"
    return "NA(no losses)" if math.isinf(x) else f"{x:.2f}"


def trimmed_pf(vals, n):
    if len(vals) <= n:
        return np.nan
    order = np.argsort(vals)[::-1]
    banned = set(order[:n])
    return pf([v for i, v in enumerate(vals) if i not in banned])


def half_pf(vals):
    m = len(vals) // 2
    if not m or m == len(vals):
        return np.nan, np.nan
    return pf(vals[:m]), pf(vals[m:])


def loo_pf(vals):
    if len(vals) < 2:
        return []
    return [pf(vals[:i] + vals[i + 1:]) for i in range(len(vals))]


def wilson(k, n):
    if not n:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    h = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return 100*max(0, c-h), 100*min(1, c+h)


def mdd(vals):
    eq = peak = 1.0
    dd = 0.0
    for v in vals:
        eq *= 1 + v/100
        peak = max(peak, eq)
        dd = min(dd, eq/peak - 1)
    return 100*dd


def target_reach(sig, target):
    if not sig.get("filled"):
        return False
    ideal_exit, _, _, reason = ex.runner_fixed(sig, target)
    return reason == "FIXED"


def summarize(vals):
    n = len(vals)
    wins = sum(v > 0 for v in vals)
    h1, h2 = half_pf(vals)
    loo = loo_pf(vals)
    finite_loo = [x for x in loo if np.isfinite(x)]
    return {
        "n": n,
        "wins": wins,
        "win_rate": 100*wins/n if n else np.nan,
        "win_ci": wilson(wins, n),
        "pf": pf(vals),
        "avg": float(np.mean(vals)) if vals else np.nan,
        "median": float(np.median(vals)) if vals else np.nan,
        "mdd": mdd(vals),
        "top1_pf": trimmed_pf(vals, 1),
        "top2_pf": trimmed_pf(vals, 2),
        "half_pf": (h1, h2),
        "loo_pf_min": min(finite_loo) if finite_loo else (math.inf if loo else np.nan),
        "loo_pf_median": float(np.median(finite_loo)) if finite_loo else (math.inf if loo else np.nan),
    }


def fmt(s):
    return (
        f"win={s['wins']}/{s['n']} ({s['win_rate']:.1f}%, CI {s['win_ci'][0]:.1f}-{s['win_ci'][1]:.1f}) "
        f"PF={pft(s['pf'])} avg={s['avg']:+.3f}% med={s['median']:+.3f}% MDD={s['mdd']:.3f}% "
        f"top1={pft(s['top1_pf'])} top2={pft(s['top2_pf'])} "
        f"half={pft(s['half_pf'][0])}/{pft(s['half_pf'][1])} "
        f"LOOmin={pft(s['loo_pf_min'])} LOOmed={pft(s['loo_pf_median'])}"
    )


def main():
    bars = base.load_1m()
    daily = base.load_daily()
    have = sorted({str(x) for x in bars.index.date})
    out = [
        f"stored QQQ 1m trading days={len(have)}",
        "FROZEN SIGNAL: gap 0.2~1.5% / |VIX open-prevclose|<5% / cover>=40% / first-window close entry",
        "prefilled entry-window days excluded / 11:30 no-fill cut / fill bar excluded / 14:00 final cut",
        "ONLY VARIABLE: fixed extension after gap-fill = 0.10%..0.40% in 0.05% steps",
        "IDEAL=touched target price; CLOSE=trigger-minute close; GS checks CLOSE bookkeeping.",
        "",
    ]
    payload = {"tracks": {}}

    for minutes, name in ((5, "5m"), (15, "15m")):
        sigs, prefilled = [], 0
        for day in have:
            info = daily.get(day)
            if not info:
                continue
            s = ex.get_signal(day, info, bars, minutes)
            if not s:
                continue
            if s.get("prefilled"):
                prefilled += 1
            else:
                sigs.append(s)

        filled = sum(bool(s.get("filled")) for s in sigs)
        out.append(f"[{name}] tradable={len(sigs)} filled={filled} nofill={len(sigs)-filled} prefilled_excluded={prefilled}")
        track = {"n": len(sigs), "filled": filled, "prefilled": prefilled, "targets": {}}

        for target in TARGETS:
            trades = [ex.make_trade(s, "fixed", target) for s in sigs]
            ideal = [float(t["ideal_pct"]) for t in trades]
            close = [float(t["close_pct"]) for t in trades]
            ideal_s = summarize(ideal)
            close_s = summarize(close)
            reach = sum(target_reach(s, target) for s in sigs if s.get("filled"))
            reach_n = filled
            gs = ex.gs_check(trades, bars)
            label = f"fixed +{target:.2f}%"
            out.append(f"  {label:<14} reach={reach}/{reach_n} | IDEAL {fmt(ideal_s)}")
            out.append(f"  {'':14}                 CLOSE {fmt(close_s)} | GS matched={gs.get('matched')} diff={gs.get('diff', float('nan')):+.9f}")
            track["targets"][f"{target:.2f}"] = {
                "reach": reach,
                "reach_n": reach_n,
                "ideal": ideal_s,
                "close": close_s,
                "ideal_values": ideal,
                "close_values": close,
                "gs": gs,
            }

        # Plateau diagnostic: robust candidates require top2 PF >1 and both half PF >1.
        robust = []
        for key, r in track["targets"].items():
            s = r["ideal"]
            h1, h2 = s["half_pf"]
            if np.isfinite(s["top2_pf"]) and s["top2_pf"] > 1 and h1 > 1 and h2 > 1:
                robust.append(float(key))
        out.append(f"  robust-band candidates (top2 PF>1 AND both halves>1): {robust if robust else 'NONE'}")
        out.append("")
        payload["tracks"][name] = track

    report = "\n".join(out)
    print(report)
    payload["at"] = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    payload["report"] = report
    with open("gsquant_fixed_sensitivity_result.json", "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        txt = traceback.format_exc()
        print(txt)
        with open("gsquant_fixed_sensitivity_result.json", "w") as f:
            json.dump({"error": txt}, f, ensure_ascii=False, indent=2)
        raise
