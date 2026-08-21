"""GS Quant execution-engine cross-check for the QQQ gap strategy.

Uses Goldman Sachs' open-source gs_quant PredefinedAssetEngine with our stored
QQQ 1-minute data. GS data/pricing APIs are NOT used.
"""
import datetime as dt
import glob, json, traceback
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

DATA="data/1m/QQQ_*.csv.gz"
GAP_MIN,GAP_MAX=.2,1.5
COVER_MIN=.40
VIX_OPEN_MAX=5.0
NOFILL_CUT="11:30"
FINAL_CUT="14:00"
TRAIL=.15


def load_1m():
    fs=[]
    for p in sorted(glob.glob(DATA)):
        x=pd.read_csv(p,compression="gzip")
        if "ts" not in x.columns: continue
        x["ts"]=pd.to_datetime(x["ts"]).dt.tz_localize(None)
        x=x.set_index("ts")
        fs.append(x[["Open","High","Low","Close"]])
    if not fs: return pd.DataFrame()
    x=pd.concat(fs).sort_index()
    return x[~x.index.duplicated(keep="last")]


def load_daily():
    d=yf.download("QQQ",period="2y",interval="1d",auto_adjust=False,progress=False)
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    d=d.dropna(); d.index=pd.to_datetime(d.index).tz_localize(None); pc=d["Close"].shift(1)
    v=yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna()
    try: v.index=v.index.tz_localize(None)
    except TypeError: pass
    v.index=pd.to_datetime(v.index).normalize(); vm=(v["Open"]/v["Close"].shift(1)-1)*100
    out={}
    for i in d.index:
        if pd.isna(pc.loc[i]): continue
        out[str(i.date())]={
            "prev":float(pc.loc[i]),"open":float(d.loc[i,"Open"]),
            "gap":float((d.loc[i,"Open"]/pc.loc[i]-1)*100),
            "vix":float(vm.get(i.normalize(),np.nan)),
        }
    return out


def pnl_pct(entry,exit_,direction):
    return direction*(exit_-entry)/entry*100


def build_trade(day,info,bars,minutes,trail=TRAIL):
    g=bars[bars.index.date==pd.Timestamp(day).date()]
    if g.empty: return None
    st=pd.Timestamp(f"{day} 09:30"); en=st+pd.Timedelta(minutes=minutes)
    w=g[(g.index>=st)&(g.index<en)]
    if len(w)<minutes-1: return None
    if not np.isfinite(info["vix"]) or abs(info["vix"])>=VIX_OPEN_MAX: return None
    if abs(info["gap"])<GAP_MIN or abs(info["gap"])>=GAP_MAX: return None

    o=float(w["Open"].iloc[0]); entry=float(w["Close"].iloc[-1]); entry_ts=pd.Timestamp(w.index[-1])
    gapd=info["open"]-info["prev"]; gap_up=gapd>0; direction=-1 if gap_up else 1
    cover=((o-entry)/gapd) if gap_up else ((entry-o)/abs(gapd))
    if cover<COVER_MIN: return None
    target=float(info["prev"])
    pre=float(w["Low"].min())<=target if gap_up else float(w["High"].max())>=target
    if pre: return {"day":day,"prefilled":True,"cover":cover}

    fill=None
    for ts,r in g[g.index>=en].iterrows():
        hit=float(r["Low"])<=target if gap_up else float(r["High"])>=target
        if hit: fill=pd.Timestamp(ts); break

    if fill is None or fill.strftime("%H:%M")>NOFILL_CUT:
        cut=g[g.index<=pd.Timestamp(f"{day} {NOFILL_CUT}")]
        if cut.empty: return None
        x_ts=pd.Timestamp(cut.index[-1]); x=float(cut["Close"].iloc[-1])
        return {"day":day,"prefilled":False,"filled":False,"cover":cover,"direction":direction,
                "entry_ts":entry_ts.to_pydatetime(),"exit_ts":x_ts.to_pydatetime(),"entry":entry,
                "ideal_exit":x,"close_exit":x,"reason":"NO_FILL_1130",
                "ideal_pct":pnl_pct(entry,x,direction),"close_pct":pnl_pct(entry,x,direction)}

    best=target; ideal_exit=close_exit=exit_ts=reason=None
    post=g[g.index>fill]
    for ts,r in post.iterrows():
        ts=pd.Timestamp(ts)
        stop=best*(1+trail/100) if gap_up else best*(1-trail/100)
        hit=float(r["High"])>=stop if gap_up else float(r["Low"])<=stop
        if hit:
            exit_ts=ts; ideal_exit=float(stop); close_exit=float(r["Close"]); reason="TRAIL"; break
        best=min(best,float(r["Low"])) if gap_up else max(best,float(r["High"]))
        if ts.strftime("%H:%M")>=FINAL_CUT:
            exit_ts=ts; ideal_exit=close_exit=float(r["Close"]); reason="14:00_CUT"; break
    if exit_ts is None:
        rr=post[post.index<=pd.Timestamp(f"{day} {FINAL_CUT}")]
        if rr.empty: rr=post
        if rr.empty: return None
        exit_ts=pd.Timestamp(rr.index[-1]); ideal_exit=close_exit=float(rr["Close"].iloc[-1]); reason="14:00_CUT"
    return {"day":day,"prefilled":False,"filled":True,"cover":cover,"direction":direction,
            "entry_ts":entry_ts.to_pydatetime(),"exit_ts":exit_ts.to_pydatetime(),"fill_ts":fill.to_pydatetime(),
            "entry":entry,"ideal_exit":ideal_exit,"close_exit":close_exit,"reason":reason,
            "ideal_pct":pnl_pct(entry,ideal_exit,direction),"close_pct":pnl_pct(entry,close_exit,direction)}


def pf(v):
    gp=sum(x for x in v if x>0); gl=-sum(x for x in v if x<=0)
    return gp/gl if gl else float("inf")

def pft(x): return "NA(no losses)" if not np.isfinite(x) else f"{x:.2f}"

def trimmed(v,k):
    if len(v)<=k:return np.nan
    ids=np.argsort(v)[::-1]; drop=set(ids[:k]); return pf([x for i,x in enumerate(v) if i not in drop])
def halves(v):
    m=len(v)//2
    return (np.nan,np.nan) if m==0 or m==len(v) else (pf(v[:m]),pf(v[m:]))
def mdd(v):
    e=p=1.; d=0.
    for r in v:
        e*=1+r/100; p=max(p,e); d=min(d,e/p-1)
    return d*100


class ScheduleTrigger(OrdersGeneratorTrigger):
    def __init__(self,schedule,instrument):
        self.schedule=schedule; self.instrument=instrument; super().__init__(actions=[Action()])
    def get_trigger_times(self): return sorted({ts.time() for ts in self.schedule})
    def generate_orders(self,state,backtest=None):
        items=self.schedule.get(pd.Timestamp(state).to_pydatetime(),[])
        return [OrderAtMarket(instrument=self.instrument,quantity=z["qty"],generation_time=state,
                              execution_datetime=state,source=z["source"]) for z in items]


def run_gs(trades,bars):
    if not trades:return {"ok":False,"error":"no trades"}
    inst=IRBondFuture(currency="USD",name="QQQProxy")
    dm=DataManager(); dm.add_data_source(bars["Close"].astype(float),DataFrequency.REAL_TIME,inst,ValuationFixingType.PRICE)
    sched={}
    for t in trades:
        e=pd.Timestamp(t["entry_ts"]).to_pydatetime(); x=pd.Timestamp(t["exit_ts"]).to_pydatetime()
        sched.setdefault(e,[]).append({"qty":t["direction"],"source":"ENTRY_"+t["day"]})
        sched.setdefault(x,[]).append({"qty":-t["direction"],"source":"EXIT_"+t["day"]})
    eng=PredefinedAssetEngine(data_mgr=dm,calendars="weekend",tz=dt.timezone.utc)
    bt=eng.run_backtest(Strategy(None,[ScheduleTrigger(sched,inst)]),
                        start=min(bars.index).date(),end=max(bars.index).date(),
                        states=sorted({x.date() for x in bars.index}),initial_value=100.)
    perf=bt.performance
    first=float(perf.iloc[0]); final=float(perf.iloc[-1])
    engine_pnl=final-first
    manual=sum(t["direction"]*(t["close_exit"]-t["entry"]) for t in trades)
    return {"ok":True,"engine_start":first,"engine_final":final,"engine_abs_pnl":engine_pnl,
            "manual_abs_pnl":manual,"difference":engine_pnl-manual,
            "matched":abs(engine_pnl-manual)<1e-8,"orders":2*len(trades)}


def summary(name,trades,pref,er):
    a=[t["ideal_pct"] for t in trades]; c=[t["close_pct"] for t in trades]
    h1a,h2a=halves(a); h1c,h2c=halves(c); filled=sum(t["filled"] for t in trades)
    L=[f"[{name}] tradable n={len(trades)} / filled={filled} / 11:30 no-fill={len(trades)-filled} / prefilled-excluded={pref}",
       f"  IDEAL stop fill : win={sum(x>0 for x in a)}/{len(a)} PF={pft(pf(a))} avg={np.mean(a):+.3f}% med={np.median(a):+.3f}% MDD={mdd(a):.3f}%",
       f"  GS bar-close    : win={sum(x>0 for x in c)}/{len(c)} PF={pft(pf(c))} avg={np.mean(c):+.3f}% med={np.median(c):+.3f}% MDD={mdd(c):.3f}%",
       f"  top1 removed PF : ideal={pft(trimmed(a,1))} / close={pft(trimmed(c,1))}",
       f"  top2 removed PF : ideal={pft(trimmed(a,2))} / close={pft(trimmed(c,2))}",
       f"  half PF         : ideal={pft(h1a)}/{pft(h2a)} / close={pft(h1c)}/{pft(h2c)}"]
    if er.get("ok"):
        L += [f"  GS engine check : start={er['engine_start']:.6f} final={er['engine_final']:.6f} absPnL={er['engine_abs_pnl']:+.6f}",
              f"                    manual={er['manual_abs_pnl']:+.6f} diff={er['difference']:+.12f} matched={er['matched']}"]
    return L


def main():
    bars=load_1m()
    if bars.empty: raise RuntimeError("no stored QQQ 1m files")
    daily=load_daily(); have=sorted({str(x) for x in bars.index.date})
    out=[f"gs_quant={getattr(gs_quant,'__version__','unknown')}",f"stored QQQ 1m trading days={len(have)}",
         "RULES: gap 0.2~1.5% / |VIX open-prevclose|<5% / cover>=40% / first-bar close entry",
         "       no-fill 11:30 cut / after fill 0.15% trail / 14:00 final cut / fill bar excluded",
         "GS engine = independent order/fill/cash bookkeeping on our QQQ minute CLOSE series.",
         "GS bar-close = alternative exit: if trail is touched intrabar, execute at that minute close (not guaranteed better/worse).",""]
    tracks={}
    for mins,name in ((5,"5m"),(15,"15m"),(60,"1h")):
        tr=[]; pref=0
        for day in have:
            if day not in daily: continue
            r=build_trade(day,daily[day],bars,mins)
            if not r: continue
            if r.get("prefilled"): pref+=1
            else: tr.append(r)
        er=run_gs(tr,bars) if tr else {"ok":False,"error":"no trades"}
        if tr: out += summary(name,tr,pref,er)
        else: out += [f"[{name}] n=0"]
        out.append("")
        js=[]
        for t in tr:
            z={k:v for k,v in t.items() if k not in ("entry_ts","exit_ts","fill_ts")}
            z["entry_ts"]=str(t.get("entry_ts")); z["exit_ts"]=str(t.get("exit_ts")); z["fill_ts"]=str(t.get("fill_ts")) if t.get("fill_ts") else None
            js.append(z)
        tracks[name]={"trades":js,"prefilled_excluded":pref,"engine":er}
    return "\n".join(out),tracks

if __name__=="__main__":
    try:
        report,tracks=main(); payload={"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":report,"tracks":tracks}
    except Exception:
        report="FAILED\n"+traceback.format_exc(); payload={"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":report}
    print(report)
    json.dump(payload,open("gsquant_gap_result.json","w"),ensure_ascii=False,indent=2)
