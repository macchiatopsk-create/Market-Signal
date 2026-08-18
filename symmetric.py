"""매크로 지표 → 다음날 QQQ, 상승/하락 대칭 측정.

이전 리포트는 '상승 %'만 봐서 하락 쪽 정보가 빠졌다.
이번엔 고점/저점을 분리하고, 롱·숏 양쪽 관점을 모두 기록.
  MFE_up : 09:30 대비 장중 최대 상승폭  (롱 수익 잠재력)
  MFE_dn : 09:30 대비 장중 최대 하락폭  (숏 수익 잠재력)
  종가 방향 · 상승/하락 각각의 평균 폭
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

FEATS={"DX-Y.NYB":"달러인덱스","^VIX":"VIX","^TNX":"10년물","CL=F":"WTI유가",
       "GC=F":"금","HYG":"HY채권","^GDAXI":"DAX","^N225":"닛케이"}

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
    nxt=pd.DataFrame({
        "ret":((q["Close"]/q["Open"]-1)*100).shift(-1),
        "up":((q["High"]/q["Open"]-1)*100).shift(-1),      # 최대 상승폭
        "dn":((1-q["Low"]/q["Open"])*100).shift(-1),       # 최대 하락폭(양수)
    })
    b=nxt.dropna()
    out.append("각 지표 당일 변화 → 다음날 QQQ (09:30 기준). 상승·하락 대칭 측정")
    out.append(f"베이스라인 n={len(b)}: 종가 상승 {(b['ret']>0).mean()*100:.1f}% / 하락 {(b['ret']<=0).mean()*100:.1f}%"
               f" · 최대상승 {b['up'].mean():.3f}% · 최대하락 {b['dn'].mean():.3f}%"
               f" · 상승일 평균 {b[b.ret>0]['ret'].mean():+.3f}% · 하락일 평균 {b[b.ret<=0]['ret'].mean():+.3f}%")
    out.append("")
    for tk,nm in FEATS.items():
        try:
            d=norm(yf.Ticker(tk).history(period="2y")[["Close"]].dropna())
            chg=((d["Close"]/d["Close"].shift(1)-1)*100).rename("x")
            j=pd.DataFrame(chg).join(nxt,how="inner").dropna()
            if len(j)<200: continue
            j["q"]=pd.qcut(j["x"],5,labels=False,duplicates="drop")
            out.append(f"[{nm}] n={len(j)}")
            out.append(f"    {'구간':22s} {'n':>4s} {'상승%':>6s} {'하락%':>6s} "
                       f"{'최대상승':>8s} {'최대하락':>8s} {'상승일평균':>9s} {'하락일평균':>9s}")
            for k in sorted(j["q"].dropna().unique()):
                s=j[j["q"]==k]; n=len(s)
                u=int((s["ret"]>0).sum()); dd=n-u
                cu=wilson(u,n); cd=wilson(dd,n)
                ua=s[s.ret>0]["ret"].mean() if u else 0
                da=s[s.ret<=0]["ret"].mean() if dd else 0
                out.append(f"    Q{int(k)+1} ({s['x'].min():+.2f}~{s['x'].max():+.2f}%){'':<3s} {n:4d} "
                           f"{u/n*100:5.1f}% {dd/n*100:5.1f}% {s['up'].mean():7.3f}% {s['dn'].mean():7.3f}% "
                           f"{ua:+8.3f}% {da:+8.3f}%")
            # 절대값 기준 (U자 확인)
            j["a"]=j["x"].abs()
            j["aq"]=pd.qcut(j["a"],4,labels=False,duplicates="drop")
            out.append(f"    -- |변화| 기준 (방향 무관, 변동성 관점) --")
            for k in sorted(j["aq"].dropna().unique()):
                s=j[j["aq"]==k]; n=len(s)
                out.append(f"    A{int(k)+1} (|{s['a'].min():.2f}~{s['a'].max():.2f}|%){'':<2s} {n:4d} "
                           f"{(s['ret']>0).mean()*100:5.1f}% {(s['ret']<=0).mean()*100:5.1f}% "
                           f"{s['up'].mean():7.3f}% {s['dn'].mean():7.3f}% "
                           f"{'레인지 '+format(s['up'].mean()+s['dn'].mean(),'.3f')+'%':>20s}")
            out.append("")
        except Exception as e:
            out.append(f"[{nm}] 실패 {type(e).__name__}: {e}")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("symmetric_result.json","w"),ensure_ascii=False,indent=1)
