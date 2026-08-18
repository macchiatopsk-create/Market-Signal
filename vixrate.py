"""VIX × 금리(10년물·30년물) 교차 → 다음날 QQQ.

오늘(08-18) 상태: VIX +10.95%, 30Y +1.04%, 10Y 상승 = 'VIX 급등 + 금리 급등'
이 조합이 과거에 어땠는지, VIX 구간별로 금리가 방향을 가르는지 측정.
상승/하락 대칭 + 반반검증 포함.
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

def wilson(k,n):
    if n==0: return (0.0,0.0)
    p,z=k/n,1.96; d=1+z*z/n
    c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (round(max(0,c-h)*100,1),round(min(1,c+h)*100,1))

def norm(d):
    try: d.index=d.index.tz_localize(None)
    except: pass
    d.index=pd.to_datetime(d.index).normalize()
    return d[~d.index.duplicated(keep="last")]

def col(tk):
    d=norm(yf.Ticker(tk).history(period="2y")[["Close"]].dropna())
    return (d["Close"]/d["Close"].shift(1)-1)*100

def main():
    out=[]
    q=norm(yf.Ticker("QQQ").history(period="2y")[["Open","High","Low","Close"]].dropna())
    nxt=pd.DataFrame({
        "ret":((q["Close"]/q["Open"]-1)*100).shift(-1),
        "up":((q["High"]/q["Open"]-1)*100).shift(-1),
        "dn":((1-q["Low"]/q["Open"])*100).shift(-1)})
    df=pd.DataFrame({"vix":col("^VIX"),"t10":col("^TNX"),"t30":col("^TYX")}).join(nxt,how="inner").dropna()
    b=df
    out.append(f"n={len(df)} · 베이스라인 상승 {(b.ret>0).mean()*100:.1f}% "
               f"최대상승 {b.up.mean():.3f}% 최대하락 {b.dn.mean():.3f}%")
    out.append("")

    def rep(s,lab,ind="  "):
        n=len(s)
        if n<12: out.append(f"{ind}{lab:30s} n={n:3d} 표본부족"); return
        u=int((s.ret>0).sum()); ci=wilson(u,n)
        ds=sorted(s.index); half=ds[len(ds)//2]
        f1=s[s.index<half]; f2=s[s.index>=half]
        r1=(f1.ret>0).mean()*100 if len(f1)>=8 else float("nan")
        r2=(f2.ret>0).mean()*100 if len(f2)>=8 else float("nan")
        ua=s[s.ret>0].ret.mean() if u else 0; da=s[s.ret<=0].ret.mean() if n-u else 0
        g=s[s.ret>0].ret.sum(); l=-s[s.ret<=0].ret.sum()
        out.append(f"{ind}{lab:30s} n={n:3d} 상승 {u/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
                   f"| 최대↑ {s.up.mean():.3f}% 최대↓ {s.dn.mean():.3f}% "
                   f"| 상승일 {ua:+.3f}% 하락일 {da:+.3f}% PF {g/l if l else 99:4.2f} "
                   f"| 반반 {r1:4.0f}/{r2:4.0f}")

    VB=[(-99,-5,"VIX ≤-5%"),(-5,-2,"VIX -5~-2%"),(-2,2,"VIX -2~+2%"),
        (2,5,"VIX +2~5%"),(5,10,"VIX +5~10%"),(10,99,"VIX +10%↑")]
    out.append("[VIX 단독 — 세분화]")
    for lo,hi,lab in VB: rep(df[(df.vix>=lo)&(df.vix<hi)],lab)
    out.append("")
    out.append("[VIX × 10년물]")
    for lo,hi,lab in VB:
        sub=df[(df.vix>=lo)&(df.vix<hi)]
        rep(sub[sub.t10>0],f"{lab} & 10Y↑")
        rep(sub[sub.t10<=0],f"{lab} & 10Y↓")
    out.append("")
    out.append("[VIX × 30년물]")
    for lo,hi,lab in VB:
        sub=df[(df.vix>=lo)&(df.vix<hi)]
        rep(sub[sub.t30>0],f"{lab} & 30Y↑")
        rep(sub[sub.t30<=0],f"{lab} & 30Y↓")
    out.append("")
    out.append("[VIX↑ & 금리 둘 다 같은 방향 — 오늘 유형]")
    rep(df[(df.vix>=5)&(df.t10>0)&(df.t30>0)],"VIX+5↑ & 10Y↑ & 30Y↑")
    rep(df[(df.vix>=5)&(df.t10<=0)&(df.t30<=0)],"VIX+5↑ & 10Y↓ & 30Y↓")
    rep(df[(df.vix>=10)&(df.t10>0)&(df.t30>0)],"VIX+10↑ & 10Y↑ & 30Y↑")
    rep(df[(df.vix<=-5)&(df.t10>0)&(df.t30>0)],"VIX-5↓ & 10Y↑ & 30Y↑")
    out.append("")
    out.append("[금리 단독 — 참고]")
    for lo,hi,lab in ((-99,-2,"10Y ≤-2%"),(-2,0,"10Y -2~0%"),(0,2,"10Y 0~2%"),(2,99,"10Y +2%↑")):
        rep(df[(df.t10>=lo)&(df.t10<hi)],lab)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("vixrate_result.json","w"),ensure_ascii=False,indent=1)
