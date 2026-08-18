"""QQQ 당일 등락 × VIX 변화 교차 → 다음날 QQQ.

패닉 강도를 두 축으로 측정: 얼마나 빠졌나(QQQ) × 공포가 얼마나 튀었나(VIX)
상승/하락 대칭 + 반반검증 + 연도별 분해 + 날짜 분포(국면 편중 확인)
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

def main():
    out=[]
    q=norm(yf.Ticker("QQQ").history(period="2y")[["Open","High","Low","Close"]].dropna())
    v=norm(yf.Ticker("^VIX").history(period="2y")[["Close"]].dropna())
    df=pd.DataFrame({
        "qd2d":(q["Close"]/q["Close"].shift(1)-1)*100,     # 당일 종가 기준 등락
        "qintra":(q["Close"]/q["Open"]-1)*100,             # 당일 09:30->종가
        "vix":(v["Close"]/v["Close"].shift(1)-1)*100,
    })
    df["ret"]=df["qintra"].shift(-1)
    df["up"]=((q["High"]/q["Open"]-1)*100).shift(-1)
    df["dn"]=((1-q["Low"]/q["Open"])*100).shift(-1)
    df=df.dropna()
    b=df
    out.append(f"n={len(df)} · 베이스라인 다음날 상승 {(b.ret>0).mean()*100:.1f}% "
               f"최대↑ {b.up.mean():.3f}% 최대↓ {b.dn.mean():.3f}%")
    out.append("")

    def rep(s,lab,ind="  "):
        n=len(s)
        if n<10: out.append(f"{ind}{lab:26s} n={n:3d} 표본부족"); return
        u=int((s.ret>0).sum()); ci=wilson(u,n)
        ds=sorted(s.index); half=ds[len(ds)//2]
        f1=s[s.index<half]; f2=s[s.index>=half]
        r1=(f1.ret>0).mean()*100 if len(f1)>=6 else float("nan")
        r2=(f2.ret>0).mean()*100 if len(f2)>=6 else float("nan")
        ua=s[s.ret>0].ret.mean() if u else 0; da=s[s.ret<=0].ret.mean() if n-u else 0
        g=s[s.ret>0].ret.sum(); l=-s[s.ret<=0].ret.sum()
        out.append(f"{ind}{lab:26s} n={n:3d} 상승 {u/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
                   f"| 최대↑ {s.up.mean():.3f}% 최대↓ {s.dn.mean():.3f}% "
                   f"| 상승일 {ua:+.3f}% 하락일 {da:+.3f}% PF {g/l if l else 99:4.2f} "
                   f"| 반반 {r1:4.0f}/{r2:4.0f}")

    QB=[(-99,-2.0,"QQQ ≤-2%"),(-2.0,-1.0,"QQQ -2~-1%"),(-1.0,-0.3,"QQQ -1~-0.3%"),
        (-0.3,0.3,"QQQ -0.3~+0.3%"),(0.3,1.0,"QQQ +0.3~1%"),(1.0,99,"QQQ +1%↑")]
    VB=[(5,99,"VIX +5%↑"),(10,99,"VIX +10%↑"),(-99,-5,"VIX -5%↓")]

    out.append("[QQQ 당일 등락 단독]")
    for lo,hi,lab in QB: rep(df[(df.qd2d>=lo)&(df.qd2d<hi)],lab)
    out.append("")
    for vlo,vhi,vlab in VB:
        out.append(f"[{vlab} 조건에서 QQQ 등락별]")
        sub=df[(df.vix>=vlo)&(df.vix<vhi)]
        rep(sub,f"{vlab} 전체")
        for lo,hi,lab in QB: rep(sub[(sub.qd2d>=lo)&(sub.qd2d<hi)],f"  {lab}")
        out.append("")
    out.append("[핵심 조합 — 패닉 강도별]")
    for ql,vl,lab in ((-1.0,3,"QQQ-1%↓ & VIX+3%↑"),(-1.0,5,"QQQ-1%↓ & VIX+5%↑"),
                      (-1.5,5,"QQQ-1.5%↓ & VIX+5%↑"),(-2.0,5,"QQQ-2%↓ & VIX+5%↑"),
                      (-1.0,10,"QQQ-1%↓ & VIX+10%↑"),(-2.0,10,"QQQ-2%↓ & VIX+10%↑")):
        rep(df[(df.qd2d<=ql)&(df.vix>=vl)],lab)
    out.append("")
    out.append("[연도별 — QQQ-1%↓ & VIX+5%↑]")
    sel=df[(df.qd2d<=-1.0)&(df.vix>=5)]
    for y in sorted(set(d.year for d in sel.index)):
        rep(sel[[d.year==y for d in sel.index]],str(y))
    out.append("")
    out.append(f"[해당일 목록 {len(sel)}건 — 국면 편중 확인]")
    for d0,r in sel.iterrows():
        out.append(f"  {str(d0.date())}  QQQ {r.qd2d:+.2f}% VIX {r.vix:+.1f}% → 다음날 {r.ret:+.2f}%")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("qqqvix_result.json","w"),ensure_ascii=False,indent=1)
