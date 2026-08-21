"""40%+ gap-cover -> gap-fill -> post-fill continuation test on stored QQQ 1m data.
Strict path handling: no intrabar look-ahead in trailing; fill bar excluded.
"""
import json, datetime as dt, traceback, glob
import pandas as pd, numpy as np, yfinance as yf
DATA="data/1m/QQQ_*.csv.gz"

def wilson(k,n):
    if not n:return(0,0)
    z=1.96;p=k/n;d=1+z*z/n;c=(p+z*z/(2*n))/d;h=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return round(max(0,c-h)*100,1),round(min(1,c+h)*100,1)

def load_daily():
    d=yf.download("QQQ",period="2y",interval="1d",auto_adjust=False,progress=False)
    if isinstance(d.columns,pd.MultiIndex):d.columns=d.columns.get_level_values(0)
    d=d.dropna();d.index=pd.to_datetime(d.index).tz_localize(None);pc=d.Close.shift(1)
    v=yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna()
    try:v.index=v.index.tz_localize(None)
    except:pass
    v.index=pd.to_datetime(v.index).normalize();vm=(v.Open/v.Close.shift(1)-1)*100
    return {str(i.date()):dict(prev=float(pc.loc[i]),open=float(d.Open.loc[i]),gap=float((d.Open.loc[i]/pc.loc[i]-1)*100),vix=float(vm.get(i.normalize(),np.nan))) for i in d.index if pd.notna(pc.loc[i])}

def load_1m():
    fs=[]
    for p in glob.glob(DATA):
        x=pd.read_csv(p,compression="gzip")
        if "ts" in x:x.ts=pd.to_datetime(x.ts);x=x.set_index("ts");fs.append(x[["Open","High","Low","Close"]])
    if not fs:return pd.DataFrame()
    x=pd.concat(fs).sort_index();return x[~x.index.duplicated(keep="last")]

def sim(day,info,bars,minutes,trail):
    g=bars[bars.index.date==pd.Timestamp(day).date()]
    if g.empty:return None
    st=pd.Timestamp(f"{day} 09:30");en=st+pd.Timedelta(minutes=minutes);w=g[(g.index>=st)&(g.index<en)]
    if len(w)<minutes-1:return None
    o=float(w.Open.iloc[0]);c=float(w.Close.iloc[-1]);gap=info["open"]-info["prev"];sgn=1 if gap>0 else -1
    cover=((o-c)/gap) if sgn>0 else ((c-o)/abs(gap))
    if abs(info["gap"])<.2 or abs(info["gap"])>=1.5 or cover<.4:return None
    entry=c;target=info["prev"]
    if (float(w.Low.min())<=target if sgn>0 else float(w.High.max())>=target):return dict(day=day,pre=True,cover=cover)
    fill=None
    for ts,r in g[g.index>=en].iterrows():
        if (float(r.Low)<=target if sgn>0 else float(r.High)>=target):fill=ts;break
    if fill is None or fill.strftime("%H:%M")>"11:30":
        cut=g[g.index<=pd.Timestamp(f"{day} 11:30")]
        if cut.empty:return None
        px=float(cut.Close.iloc[-1]);p=((entry-px)/entry*100) if sgn>0 else ((px-entry)/entry*100)
        return dict(day=day,pre=False,filled=False,cover=cover,nofill_pnl=p)
    fill_px=target;fill_pnl=((entry-fill_px)/entry*100) if sgn>0 else ((fill_px-entry)/entry*100);post=g[g.index>fill]
    if post.empty:return None
    post_max=max(((fill_px-float(r.Low))/fill_px*100) if sgn>0 else ((float(r.High)-fill_px)/fill_px*100) for _,r in post.iterrows())
    best=fill_px;exit_px=None;reason=None
    for ts,r in post.iterrows():
        stop=best*(1+trail/100) if sgn>0 else best*(1-trail/100)
        if (float(r.High)>=stop) if sgn>0 else (float(r.Low)<=stop):exit_px=stop;reason="TRAIL";break
        best=min(best,float(r.Low)) if sgn>0 else max(best,float(r.High))
        if ts.strftime("%H:%M")>="14:00":exit_px=float(r.Close);reason="14:00";break
    if exit_px is None:
        rr=post[post.index<=pd.Timestamp(f"{day} 14:00")];rr=rr if not rr.empty else post;exit_px=float(rr.Close.iloc[-1]);reason="14:00"
    pnl=((entry-exit_px)/entry*100) if sgn>0 else ((exit_px-entry)/entry*100)
    return dict(day=day,pre=False,filled=True,cover=cover,fill_pnl=fill_pnl,post_max=post_max,pnl=pnl,reason=reason)

def stats(rs,label,key="pnl"):
    vals=[r[key] for r in rs if key in r];n=len(vals)
    if not n:return f"{label}: n=0"
    w=sum(x>0 for x in vals);gp=sum(x for x in vals if x>0);gl=-sum(x for x in vals if x<=0);pf=gp/gl if gl else 99;ci=wilson(w,n)
    return f"{label}: n={n} 승률={w/n*100:.1f}% CI({ci[0]:.1f}~{ci[1]:.1f}) PF={pf:.2f} 평균={np.mean(vals):+.3f}% 중앙={np.median(vals):+.3f}%"

def main():
    daily=load_daily();bars=load_1m();have=set(str(x) for x in bars.index.date)
    out=[f"1m 저장 거래일={len(have)}","QQQ gap 0.2~1.5% / |VIX open-prevclose|<5% / cover>=40% / entry=첫봉종가","11:30 미필 컷 / fill 후 trail / 14:00 최종컷 / fill bar 제외","",]
    for mins,name in [(5,"5m"),(15,"15m"),(60,"1h")]:
        sig=[];filled=[];nofill=[];prefill=[]
        for day,info in daily.items():
            if day not in have or not np.isfinite(info["vix"]) or abs(info["vix"])>=5:continue
            r=sim(day,info,bars,mins,.15)
            if not r:continue
            if r.get("pre"):prefill.append(r)
            elif r.get("filled"):sig.append(r);filled.append(r)
            elif "nofill_pnl" in r:sig.append(r);nofill.append(r)
        out.append(f"[{name}] 신호={len(sig)} · 갭필={len(filled)} · 11:30 미필={len(nofill)} · 진입봉내 이미 필={len(prefill)}")
        out.append(stats(sig,"  전체 전략 trail0.15"))
        out.append(stats(filled,"  갭필 즉시청산",key="fill_pnl") if filled else "  갭필 즉시청산: n=0")
        out.append(stats(filled,"  갭필 후 trail0.15"))
        if filled:
            vals=[x["post_max"] for x in filled]
            out.append(f"  갭필 후 최대 추가진행 평균={np.mean(vals):.3f}% 중앙={np.median(vals):.3f}% 90%tile={np.percentile(vals,90):.3f}%")
            out.append(f"  추가진행 > 갭필수익={sum(x['post_max']>abs(x['fill_pnl']) for x in filled)}/{len(filled)} ({sum(x['post_max']>abs(x['fill_pnl']) for x in filled)/len(filled)*100:.1f}%)")
        out.append("")
    out.append("[5m 동일 신호집합 · trail 비교]")
    universe=[]
    for day,info in daily.items():
        if day not in have or not np.isfinite(info["vix"]) or abs(info["vix"])>=5:continue
        r=sim(day,info,bars,5,.15)
        if r and not r.get("pre"):universe.append(day)
    for t in (.10,.15,.20,.30,.50):
        rs=[sim(day,daily[day],bars,5,t) for day in universe];rs=[r for r in rs if r and "pnl" in r]
        out.append(stats(rs,f"trail {t:.2f}%"))
    return out

if __name__=="__main__":
    try:r=main()
    except Exception:r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r);print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},open("gapafter_result.json","w"),ensure_ascii=False,indent=1)
