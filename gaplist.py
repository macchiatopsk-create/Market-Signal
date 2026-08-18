"""최종 스펙 조건충족일 목록 (옵션 실측용).
QQQ · 09:30 시가 갭 0.2~1.0% · VIX개장변화 |5%| 제외 · 갭업만(숏=풋)
"""
import json, datetime as dt, traceback
import yfinance as yf
import pandas as pd

def main():
    v=yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna()
    try: v.index=v.index.tz_localize(None)
    except: pass
    v.index=pd.to_datetime(v.index).normalize()
    vch=(v["Open"]/v["Close"].shift(1)-1)*100
    vm={str(pd.Timestamp(k).date()):float(x) for k,x in vch.dropna().items()}

    df=yf.download("QQQ",period="2y",interval="1h",prepost=False,auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    df=df[(df.index.time>=dt.time(9,30))&(df.index.time<dt.time(16,0))]
    rows=[]; pc=None
    for d in sorted(set(df.index.date)):
        g=df[df.index.date==d]
        if len(g)<5:
            if len(g): pc=float(g["Close"].iloc[-1])
            continue
        ds=str(d)
        O=[float(x) for x in g["Open"]];H=[float(x) for x in g["High"]]
        L=[float(x) for x in g["Low"]];C=[float(x) for x in g["Close"]]
        if pc:
            vx=vm.get(ds)
            if vx is None or abs(vx)<5.0:
                gap=O[0]-pc; gp=gap/pc*100
                if 0.2<=abs(gp)<1.0 and gap>0:        # 갭업만
                    ep=O[0]; tgt=pc
                    stop=ep+abs(gap)*0.5
                    hitT=hitS=None
                    for i in range(1,min(5,len(C))):
                        if hitS is None and H[i]>=stop: hitS=i
                        if hitT is None and L[i]<=tgt: hitT=i
                        if hitT is not None or hitS is not None: break
                    if hitT is not None and (hitS is None or hitT<=hitS): res,pnl="TGT",abs(tgt-ep)/ep*100
                    elif hitS is not None: res,pnl="STOP",-abs(stop-ep)/ep*100
                    else:
                        idx=min(4,len(C)-1); res,pnl="CUT",(ep-C[idx])/ep*100
                    rows.append(dict(d=ds,gap=round(gp,3),entry=round(ep,2),
                                     target=round(tgt,2),stop=round(stop,2),
                                     room=round(abs(tgt-ep)/ep*100,3),res=res,
                                     pnl=round(pnl,3),vix=round(vx,2) if vx else None))
        pc=C[-1]
    cut=str(dt.date.today()-dt.timedelta(days=170))
    recent=[r for r in rows if r["d"]>=cut]
    out=[f"전체 {len(rows)}일 · 최근 6개월 {len(recent)}일",
         f"{'날짜':11s} {'갭%':>6s} {'진입':>8s} {'타깃':>8s} {'손절':>8s} {'거리%':>6s} {'VIX%':>6s} {'결과':5s} {'손익%':>7s}"]
    for r in recent:
        out.append(f"{r['d']:11s} {r['gap']:+6.3f} {r['entry']:8.2f} {r['target']:8.2f} "
                   f"{r['stop']:8.2f} {r['room']:6.3f} {(r['vix'] or 0):+6.2f} {r['res']:5s} {r['pnl']:+7.3f}")
    return out,rows

if __name__=="__main__":
    try: o,rows=main()
    except Exception: o=["실패:\n"+traceback.format_exc()]; rows=[]
    txt="\n".join(o); print(txt)
    json.dump({"report":txt,"days":rows},open("gaplist_result.json","w"),ensure_ascii=False,indent=1)
