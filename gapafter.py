"""40%+ gap-cover -> gap-fill -> post-fill continuation test on stored QQQ 1m data.
No intrabar look-ahead: fill bar is excluded from trailing; on each 1m bar,
the prior trailing stop is checked before updating the favorable extreme.
"""
import json, datetime as dt, traceback, glob
import pandas as pd
import numpy as np
import yfinance as yf

DATA="data/1m/QQQ_*.csv.gz"

def wilson(k,n):
    if not n: return (0.0,0.0)
    z=1.96; p=k/n; d=1+z*z/n
    c=(p+z*z/(2*n))/d; h=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return round(max(0,c-h)*100,1),round(min(1,c+h)*100,1)

def load_daily():
    d=yf.download("QQQ",period="2y",interval="1d",auto_adjust=False,progress=False)
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    d=d.dropna(); d.index=pd.to_datetime(d.index).tz_localize(None)
    pc=d["Close"].shift(1); gap=(d["Open"]/pc-1)*100
    v=yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna()
    try: v.index=v.index.tz_localize(None)
    except: pass
    v.index=pd.to_datetime(v.index).normalize(); vm=(v["Open"]/v["Close"].shift(1)-1)*100
    return {str(i.date()):dict(prev=float(pc.loc[i]),open=float(d["Open"].loc[i]),gap=float(gap.loc[i]),vix=float(vm.get(i.normalize(),np.nan)))
            for i in d.index if pd.notna(pc.loc[i])}

def load_1m():
    frames=[]
    for p in sorted(glob.glob(DATA)):
        x=pd.read_csv(p,compression="gzip")
        if "ts" not in x: continue
        x["ts"]=pd.to_datetime(x["ts"]); x=x.set_index("ts")
        frames.append(x[["Open","High","Low","Close"]])
    if not frames: return pd.DataFrame()
    x=pd.concat(frames).sort_index(); return x[~x.index.duplicated(keep="last")]

def simulate(day, info, bars, minutes, trail):
    g=bars[bars.index.date==pd.Timestamp(day).date()]
    if g.empty: return None
    start=pd.Timestamp(f"{day} 09:30"); end=start+pd.Timedelta(minutes=minutes)
    w=g[(g.index>=start)&(g.index<end)]
    if len(w)<minutes-1: return None
    o=float(w["Open"].iloc[0]); c=float(w["Close"].iloc[-1]); gap=info["open"]-info["prev"]
    if abs(info["gap"])<0.2 or abs(info["gap"])>=1.5: return None
    sgn=1 if gap>0 else -1
    cover=((o-c)/gap) if sgn>0 else ((c-o)/abs(gap))
    if cover<0.40: return None
    entry=c; target=info["prev"]
    if (float(w["Low"].min())<=target if sgn>0 else float(w["High"].max())>=target):
        return dict(day=day,minutes=minutes,cover=cover,filled_pre=True)
    fill=None
    for ts,r in g[g.index>=end].iterrows():
        if (float(r.Low)<=target if sgn>0 else float(r.High)>=target): fill=ts; break
    if fill is None or fill.strftime("%H:%M")>"11:30":
        return dict(day=day,minutes=minutes,cover=cover,filled=False,reason="NO_FILL_1130")
    fill_px=target; post=g[g.index>fill]
    if post.empty: return None
    post_max=max(((fill_px-float(r.Low))/fill_px*100) if sgn>0 else ((float(r.High)-fill_px)/fill_px*100) for _,r in post.iterrows())
    best_px=fill_px; exit_ts=None; exit_px=None; reason=None
    for ts,r in post.iterrows():
        stop=(best_px*(1+trail/100)) if sgn>0 else (best_px*(1-trail/100))
        hit=(float(r.High)>=stop) if sgn>0 else (float(r.Low)<=stop)
        if hit:
            exit_ts=ts; exit_px=stop; reason="TRAIL"; break
        best_px=min(best_px,float(r.Low)) if sgn>0 else max(best_px,float(r.High))
        if ts.strftime("%H:%M")>="14:00":
            exit_ts=ts; exit_px=float(r.Close); reason="14:00_CUT"; break
    if exit_ts is None:
        rr=post[post.index<=pd.Timestamp(f"{day} 14:00")]
        if rr.empty: rr=post
        exit_px=float(rr["Close"].iloc[-1]); exit_ts=rr.index[-1]; reason="14:00_CUT"
    pnl=((entry-exit_px)/entry*100) if sgn>0 else ((exit_px-entry)/entry*100)
    fill_pnl=((entry-fill_px)/entry*100) if sgn>0 else ((fill_px-entry)/entry*100)
    return dict(day=day,minutes=minutes,cover=cover,filled=True,fill_time=str(fill),fill_pnl=fill_pnl,post_max=post_max,pnl=pnl,reason=reason,hold_min=(exit_ts-fill).total_seconds()/60)

def summarize(trades,label):
    n=len(trades)
    if not n: return f"{label}: n=0"
    w=sum(t["pnl"]>0 for t in trades); gp=sum(t["pnl"] for t in trades if t["pnl"]>0); gl=-sum(t["pnl"] for t in trades if t["pnl"]<=0)
    pf=gp/gl if gl else 99.0; ci=wilson(w,n)
    return f"{label}: n={n} 승률={w/n*100:.1f}% CI({ci[0]:.1f}~{ci[1]:.1f}) PF={pf:.2f} 평균={np.mean([t['pnl'] for t in trades]):+.3f}%"

def main():
    daily=load_daily(); bars=load_1m(); days_have=set(str(x) for x in bars.index.date)
    out=[f"저장된 1분봉 거래일={len(days_have)}","QQQ gap 0.2~1.5% · |VIX open/prev-close|<5% · cover>=40% · 진입=첫봉 종가","갭필 실패 11:30 컷 · 갭필 후 trail · 최종 14:00 컷","look-ahead 방지: fill bar는 trail에서 제외, 각 1분봉은 기존 trail 먼저 확인 후 extreme 갱신",""]
    universe_by_tf={}
    for minutes,name in [(5,"5m"),(15,"15m"),(60,"1h")]:
        trades=[]; eligible=0; nofill=0; prefill=0
        for day,info in daily.items():
            if day not in days_have or not np.isfinite(info["vix"]) or abs(info["vix"])>=5: continue
            r=simulate(day,info,bars,minutes,0.15)
            if not r: continue
            eligible+=1
            if r.get("filled_pre"): prefill+=1
            elif r.get("filled"): trades.append(r)
            else: nofill+=1
        universe_by_tf[name]=trades
        out.append(f"[{name}] 조건충족={eligible} · 진입 후 갭필={len(trades)} · 11:30 미필={nofill} · 진입봉내 이미 필={prefill}")
        out.append(summarize(trades,"40%+ trail 0.15%"))
        if trades:
            vals=[t["post_max"] for t in trades]
            out.append(f"  갭필후 추가진행 평균={np.mean(vals):.3f}% 중앙={np.median(vals):.3f}% 90%tile={np.percentile(vals,90):.3f}%")
            out.append(f"  추가진행 > 갭필수익={sum(t['post_max']>abs(t['fill_pnl']) for t in trades)}/{len(trades)} ({sum(t['post_max']>abs(t['fill_pnl']) for t in trades)/len(trades)*100:.1f}%)")
        out.append("")
    out.append("[5m cover>=40% · trail 폭 비교]")
    universe=[t["day"] for t in universe_by_tf.get("5m",[])]
    for t in (0.10,0.15,0.20,0.30,0.50):
        rs=[]
        for day in universe:
            r=simulate(day,daily[day],bars,5,t)
            if r and r.get("filled"): rs.append(r)
        out.append(summarize(rs,f"trail {t:.2f}%"))
    if universe:
        dates=sorted(universe); mid=len(dates)//2
        for tag,ds in (("전반",dates[:mid]),("후반",dates[mid:])):
            rs=[]
            for day in ds:
                r=simulate(day,daily[day],bars,5,0.15)
                if r and r.get("filled"): rs.append(r)
            out.append(summarize(rs,f"5m 40%+ {tag}"))
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},open("gapafter_result.json","w"),ensure_ascii=False,indent=1)
