"""매크로 지표 수집 가능 여부 진단."""
import yfinance as yf, pandas as pd, json
T={"^TNX":"10년물","^TYX":"30년물","^FVX":"5년물","^IRX":"13주","CL=F":"WTI유가",
   "BZ=F":"브렌트","DX-Y.NYB":"달러인덱스","^VIX":"VIX","^VIX3M":"VIX3M",
   "GC=F":"금","HG=F":"구리","^MOVE":"MOVE","HYG":"HY채권","IEF":"7-10Y채권",
   "TLT":"20Y+채권","EURUSD=X":"EURUSD","JPY=X":"USDJPY","^N225":"닛케이",
   "^GDAXI":"DAX","ES=F":"S&P선물","NQ=F":"나스닥선물","RTY=F":"러셀선물"}
R=[]
for tk,nm in T.items():
    try:
        d=yf.Ticker(tk).history(period="2y")[["Open","Close"]].dropna()
        if len(d)<100: R.append(f"  {tk:12s} {nm:10s} 부족 n={len(d)}"); continue
        try: d.index=d.index.tz_localize(None)
        except: pass
        chg=(d["Close"]/d["Close"].shift(1)-1)*100
        ochg=(d["Open"]/d["Close"].shift(1)-1)*100     # 개장 시점 변화 = 09:30에 알 수 있음
        R.append(f"  {tk:12s} {nm:10s} OK n={len(d):4d} {d.index[0].date()}~{d.index[-1].date()} "
                 f"| 최근종가 {d['Close'].iloc[-1]:9.2f} | 일변동 표준편차 {chg.std():.2f}% "
                 f"| 개장변화 유효 {int(ochg.notna().sum())}")
    except Exception as e:
        R.append(f"  {tk:12s} {nm:10s} 실패 {type(e).__name__}")
txt="\n".join(R); print(txt)
json.dump({"report":txt},open("macroprobe_result.json","w"),ensure_ascii=False,indent=1)
