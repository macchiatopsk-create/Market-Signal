"""커버 임계 5% 단위 스윕 — 첫봉 크기별로 얼마나 커버하면 갭이 메워지는가.

첫봉: 5분 / 15분 / 1시간
커버: 5%~100% 5% 단위
측정: 조건충족 표본수 · 갭필 도달률 · 타깃거리 · 평균손익 · PF
VIX 개장변화 |x|>=5% 인 날은 제외 (그날은 방향이 정해진 날 — 형님 지시)
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

def wilson(k,n):
    if n==0: return (0.0,0.0)
    p,z=k/n,1.96; d=1+z*z/n
    c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (round(max(0,c-h)*100,1),round(min(1,c+h)*100,1))

def vix_open_chg():
    import time
    for i in range(4):
        try:
            d=yf.Ticker("^VIX").history(period="3y")[["Open","Close"]].dropna()
            if len(d)>100:
                try: d.index=d.index.tz_localize(None)
                except (TypeError,AttributeError): pass
                d.index=pd.to_datetime(d.index).normalize()
                d=d[~d.index.duplicated(keep="last")]
                ch=(d["Open"]/d["Close"].shift(1)-1)*100
                return {str(pd.Timestamp(k).date()):float(v) for k,v in ch.dropna().items()}
        except Exception: pass
        time.sleep(5*(2**i))
    return {}

def build(tk, interval, period, vmap):
    df=yf.download(tk,period=period,interval=interval,prepost=False,auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    df=df[(df.index.time>=dt.time(9,30))&(df.index.time<dt.time(16,0))]
    need={"5m":40,"15m":15,"1h":5}[interval]
    cut={"5m":60,"15m":20,"1h":4}[interval]
    rows=[]; pc=None
    for d in sorted(set(df.index.date)):
        g=df[df.index.date==d]
        if len(g)<need:
            if len(g): pc=float(g["Close"].iloc[-1])
            continue
        ds=str(d)
        O=[float(x) for x in g["Open"]];H=[float(x) for x in g["High"]]
        L=[float(x) for x in g["Low"]];C=[float(x) for x in g["Close"]]
        if pc:
            v=vmap.get(ds)
            if v is not None and abs(v)>=5.0:      # VIX 급변일 제외
                pc=C[-1]; continue
            gap=O[0]-pc; gp=gap/pc*100
            if abs(gp)>=0.05:
                sgn=1 if gap>0 else -1
                cover=((O[0]-C[0])/gap) if sgn>0 else ((C[0]-O[0])/abs(gap))
                ep=C[0]; tgt=pc; stop=H[0] if sgn>0 else L[0]
                hitT=hitS=None
                for i in range(1,min(cut+1,len(C))):
                    if hitS is None and ((H[i]>=stop) if sgn>0 else (L[i]<=stop)): hitS=i
                    if hitT is None and ((L[i]<=tgt) if sgn>0 else (H[i]>=tgt)): hitT=i
                    if hitT is not None or hitS is not None: break
                if hitT is not None and (hitS is None or hitT<=hitS): pnl,res=abs(tgt-ep)/ep*100,"TGT"
                elif hitS is not None: pnl,res=-abs(stop-ep)/ep*100,"STOP"
                else:
                    idx=min(cut,len(C)-1)
                    pnl=((ep-C[idx])/ep*100) if sgn>0 else ((C[idx]-ep)/ep*100); res="CUT"
                rows.append(dict(d=ds,sgn=sgn,gp=abs(gp),cover=cover,
                                 room=abs(tgt-ep)/ep*100,res=res,pnl=pnl))
        pc=C[-1]
    return rows

def main():
    vmap=vix_open_chg()
    out=[f"VIX 개장변화 맵 {len(vmap)}일 · |VIX변화|>=5% 인 날 제외",
         "진입=첫봉 종가 · 타깃=전날종가 · 손절=첫봉극점 · 컷 14:30"]
    for tk in ("QQQ","SPY"):
        for iv,per in (("5m","60d"),("15m","60d"),("1h","2y")):
            rows=build(tk,iv,per,vmap)
            days=len(set(r["d"] for r in rows))
            out.append(f"\n{'='*120}\n[{tk}] 첫봉={iv} {per} · 갭 발생 {len(rows)}일\n{'='*120}")
            for sgn,nm in ((1,"갭업→숏"),(-1,"갭다운→롱")):
                ss=[r for r in rows if r["sgn"]==sgn]
                out.append(f"  ── {nm} (전체 {len(ss)}일) ──")
                out.append(f"   {'커버≥':>6s} {'n':>4s} {'비율':>6s} {'갭':>7s} {'타깃거리':>8s} {'도달률':>7s} {'CI':>14s} {'평균':>8s} {'PF':>6s}")
                for th in [x/100 for x in range(5,105,5)]:
                    sel=[r for r in ss if r["cover"]>=th]
                    n=len(sel)
                    if n<5:
                        out.append(f"   {th*100:5.0f}% {n:4d}  표본부족"); continue
                    t=sum(1 for r in sel if r["res"]=="TGT")
                    ci=wilson(t,n)
                    g=sum(r["pnl"] for r in sel if r["pnl"]>0); l=-sum(r["pnl"] for r in sel if r["pnl"]<=0)
                    out.append(f"   {th*100:5.0f}% {n:4d} {n/max(1,len(ss))*100:5.1f}% "
                               f"{sum(r['gp'] for r in sel)/n:6.3f}% {sum(r['room'] for r in sel)/n:7.3f}% "
                               f"{t/n*100:6.1f}% ({ci[0]:5.1f}~{ci[1]:5.1f}) "
                               f"{sum(r['pnl'] for r in sel)/n:+7.3f}% {g/l if l else 99:6.2f}")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gapcover_result.json","w"),ensure_ascii=False,indent=1)
