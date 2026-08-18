"""개별 매크로 지표 단독의 다음날 QQQ 예측력.

벡터 매칭이 신호를 희석시켰을 수 있으므로, 지표 하나씩 직접 측정.
각 지표의 당일 변화율 -> 다음날 QQQ(09:30->종가) 수익률
스피어만 상관 + 구간별 성과 + 반반검증
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

FEATS={"DX-Y.NYB":"달러인덱스","^TNX":"10년물","^TYX":"30년물","^FVX":"5년물",
       "CL=F":"WTI유가","^VIX":"VIX","GC=F":"금","HG=F":"구리","HYG":"HY채권",
       "TLT":"장기채","EURUSD=X":"EURUSD","JPY=X":"USDJPY","^N225":"닛케이","^GDAXI":"DAX"}

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
    nxt=pd.DataFrame({"n_ret":((q["Close"]/q["Open"]-1)*100).shift(-1),
                      "n_gap":((q["Open"]/q["Close"].shift(1)-1)*100).shift(-1),
                      "n_rng":(((q["High"]-q["Low"])/q["Open"])*100).shift(-1)})
    out.append("각 지표 당일 변화율 → 다음날 QQQ (09:30→종가)")
    out.append(f"베이스라인: 평균 {nxt['n_ret'].mean():+.4f}% · 상승 {(nxt['n_ret']>0).mean()*100:.1f}% "
               f"· 레인지 {nxt['n_rng'].mean():.3f}%")
    out.append("")
    summ=[]
    for tk,nm in FEATS.items():
        try:
            d=norm(yf.Ticker(tk).history(period="2y")[["Close"]].dropna())
            chg=((d["Close"]/d["Close"].shift(1)-1)*100).rename("x")
            j=pd.DataFrame(chg).join(nxt,how="inner").dropna()
            if len(j)<200: out.append(f"[{nm}] 표본부족 {len(j)}"); continue
            rho=j["x"].rank().corr(j["n_ret"].rank())
            rho_r=j["x"].abs().rank().corr(j["n_rng"].rank())
            # 5분위
            j["q"]=pd.qcut(j["x"],5,labels=False,duplicates="drop")
            out.append(f"[{nm}] n={len(j)} · 방향상관 ρ={rho:+.3f} · |변화|↔다음날레인지 ρ={rho_r:+.3f}")
            half=j.index[len(j)//2]
            for k in sorted(j["q"].dropna().unique()):
                s=j[j["q"]==k]; n=len(s); up=int((s["n_ret"]>0).sum()); ci=wilson(up,n)
                f1=s[s.index<half]; f2=s[s.index>=half]
                r1=(f1["n_ret"]>0).mean()*100 if len(f1)>=15 else float("nan")
                r2=(f2["n_ret"]>0).mean()*100 if len(f2)>=15 else float("nan")
                out.append(f"    Q{int(k)+1} ({s['x'].min():+.2f}~{s['x'].max():+.2f}%) n={n:3d} "
                           f"다음날 {s['n_ret'].mean():+.3f}% 상승 {up/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
                           f"레인지 {s['n_rng'].mean():.3f}% | 반반 {r1:4.0f}/{r2:4.0f}")
            summ.append((nm,rho,rho_r,len(j)))
            out.append("")
        except Exception as e:
            out.append(f"[{nm}] 실패 {type(e).__name__}")
    out.append("="*70)
    out.append("[요약] 방향 상관 절대값 순")
    for nm,rho,rr,n in sorted(summ,key=lambda x:-abs(x[1])):
        out.append(f"  {nm:10s} 방향ρ {rho:+.3f} · 레인지ρ {rr:+.3f} (n={n})")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("single_result.json","w"),ensure_ascii=False,indent=1)
