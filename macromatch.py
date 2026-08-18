"""매크로 상태 유사일 검색.

09:30 시점에 확정된 매크로 지표들의 '개장 변화율'을 z-score 벡터로 만들고,
과거 2년에서 가장 유사한 날을 찾아 그날들의 QQQ 결과를 집계한다.

지표(전부 09:30 이전/시점 확정): 금리 곡선, 유가, 달러, VIX, 금, 구리,
                                크레딧, 환율, 해외지수, 미국 선물
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

FEATS = {"^TNX":"10Y","^TYX":"30Y","^FVX":"5Y","CL=F":"WTI","DX-Y.NYB":"DXY",
         "^VIX":"VIX","GC=F":"GOLD","HG=F":"COPPER","HYG":"HY","TLT":"TLT",
         "EURUSD=X":"EUR","JPY=X":"JPY","^N225":"NIKKEI","^GDAXI":"DAX",
         "ES=F":"ES","NQ=F":"NQ","RTY=F":"RTY"}

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
    # 1) 피처: 개장가 / 전일종가 - 1  (09:30에 알 수 있는 값)
    cols={}
    for tk,nm in FEATS.items():
        try:
            d=norm(yf.Ticker(tk).history(period="2y")[["Open","Close"]].dropna())
            cols[nm]=(d["Open"]/d["Close"].shift(1)-1)*100
        except Exception as e:
            out.append(f"  {nm} 수집 실패")
    F=pd.DataFrame(cols)
    # 커버리지 진단 후, 결측 많은 피처 제외 + 나머지는 0(변화없음)으로 채움
    cov=F.notna().mean()
    out.append("피처 커버리지: " + " · ".join(f"{c} {cov[c]*100:.0f}%" for c in F.columns))
    keep=[c for c in F.columns if cov[c]>=0.90]
    out.append(f"사용 피처 {len(keep)}개 (커버리지 90%+): {', '.join(keep)}")
    dropped=[c for c in F.columns if c not in keep]
    if dropped: out.append(f"제외: {', '.join(dropped)}")
    F=F[keep]
    Z=((F-F.mean())/F.std()).fillna(0.0)     # 개별 결측만 0 처리, 행은 유지

    # 2) 타깃: QQQ 당일 09:30->종가, 장중 저점/고점
    q=norm(yf.Ticker("QQQ").history(period="2y")[["Open","High","Low","Close"]].dropna())
    tgt=pd.DataFrame({
        "ret":(q["Close"]/q["Open"]-1)*100,
        "lo":(q["Low"]/q["Open"]-1)*100,
        "hi":(q["High"]/q["Open"]-1)*100,
        "gap":(q["Open"]/q["Close"].shift(1)-1)*100,
    }).dropna()
    df=Z.join(tgt,how="inner")
    df=df.dropna(subset=["ret","lo","hi","gap"])
    if len(df)<100: return out+[f"표본 부족 {len(df)}"]

    feat_cols=[c for c in Z.columns if c in df.columns]
    today=df.index[-1]
    hist=df.iloc[:-1]
    v0=df.loc[today,feat_cols].values.astype(float)

    # 3) 유클리드 거리
    M=hist[feat_cols].values.astype(float)
    dist=np.sqrt(((M-v0)**2).sum(axis=1))
    hist=hist.copy(); hist["dist"]=dist
    hist=hist.sort_values("dist")

    out.append(f"기준일 {today.date()} · 피처 {len(feat_cols)}개 · 과거 {len(hist)}일")
    out.append("오늘 매크로 (전일종가 대비 개장 변화율, z-score):")
    raw=F.loc[today]
    line=[]
    for c in feat_cols:
        line.append(f"{c} {raw[c]:+.2f}%(z{df.loc[today,c]:+.1f})")
    for i in range(0,len(line),4):
        out.append("  " + " · ".join(line[i:i+4]))
    out.append(f"  QQQ 갭 {df.loc[today,'gap']:+.3f}%")
    out.append("")

    def agg(sel,lab):
        n=len(sel)
        if n<5: out.append(f"  {lab:22s} n={n:3d} 부족"); return
        up=int((sel["ret"]>0).sum()); ci=wilson(up,n)
        out.append(f"  {lab:22s} n={n:3d} | 종가 평균 {sel['ret'].mean():+.3f}% "
                   f"중앙 {sel['ret'].median():+.3f}% | 상승 {up/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
                   f"| 저점 {sel['lo'].mean():+.3f}% 고점 {sel['hi'].mean():+.3f}% "
                   f"| 레인지 {(sel['hi']-sel['lo']).mean():.3f}%")

    out.append("[유사도 상위 N일 집계]")
    agg(hist, "전체 (베이스라인)")
    for k in (10,20,30,50):
        agg(hist.head(k), f"가장 유사한 {k}일")
    out.append("")
    out.append("[가장 유사한 15일 상세]")
    out.append(f"  {'날짜':11s} {'거리':>5s} {'QQQ갭':>7s} {'09:30→종가':>10s} {'저점':>8s} {'고점':>8s}")
    for d_,r in hist.head(15).iterrows():
        out.append(f"  {str(d_.date()):11s} {r['dist']:5.2f} {r['gap']:+7.2f}% "
                   f"{r['ret']:+10.2f}% {r['lo']:+7.2f}% {r['hi']:+7.2f}%")
    out.append("")
    out.append("[피처별 오늘 값이 극단인지 — 과거 백분위]")
    pct=[]
    for c in feat_cols:
        p=(hist[c]<df.loc[today,c]).mean()*100
        pct.append(f"{c} {p:4.0f}%")
    for i in range(0,len(pct),6):
        out.append("  " + " · ".join(pct[i:i+6]))
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("macromatch_result.json","w"),ensure_ascii=False,indent=1)
