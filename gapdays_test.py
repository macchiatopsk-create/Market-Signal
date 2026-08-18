"""커버 25% 조건 충족일 목록 추출 (옵션 프리미엄 실측 검증용).
최근 6개월 (로빈후드 5분봉 조회 가능 범위) 안의 날짜만.
"""
import json, datetime as dt, traceback
import yfinance as yf
import pandas as pd

COVER=0.25
def main():
    out=[]; res={}
    # VIX 개장변화
    v=yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna()
    try: v.index=v.index.tz_localize(None)
    except: pass
    v.index=pd.to_datetime(v.index).normalize()
    vch=(v["Open"]/v["Close"].shift(1)-1)*100
    vmap={str(pd.Timestamp(k).date()):float(x) for k,x in vch.dropna().items()}

    for tk in ("QQQ",):
        df=yf.download(tk,period="2y",interval="1h",prepost=False,auto_adjust=False,progress=False)
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
                vx=vmap.get(ds)
                if vx is not None and abs(vx)>=5.0: pc=C[-1]; continue
                gap=O[0]-pc; gp=gap/pc*100
                if abs(gp)>=0.05:
                    sgn=1 if gap>0 else -1
                    cover=((O[0]-C[0])/gap) if sgn>0 else ((C[0]-O[0])/abs(gap))
                    if cover>=COVER:
                        ep=C[0]; tgt=pc; stop=H[0] if sgn>0 else L[0]
                        hitT=hitS=None
                        for i in range(1,min(5,len(C))):
                            if hitS is None and ((H[i]>=stop) if sgn>0 else (L[i]<=stop)): hitS=i
                            if hitT is None and ((L[i]<=tgt) if sgn>0 else (H[i]>=tgt)): hitT=i
                            if hitT is not None or hitS is not None: break
                        ok = hitT is not None and (hitS is None or hitT<=hitS)
                        rows.append(dict(d=ds,dir=("갭업/숏" if sgn>0 else "갭다운/롱"),
                                         gap=round(gp,3),cover=round(cover,3),
                                         entry=round(ep,2),target=round(tgt,2),
                                         stop=round(stop,2),room=round(abs(tgt-ep)/ep*100,3),
                                         filled=ok,vix=round(vx,2) if vx is not None else None))
            pc=C[-1]
        res[tk]=rows
        cutoff=str(dt.date.today()-dt.timedelta(days=175))
        recent=[r for r in rows if r["d"]>=cutoff]
        out.append(f"[{tk}] 커버>={COVER:.0%} 조건충족 전체 {len(rows)}일 · 최근 6개월 {len(recent)}일")
        out.append(f"{'날짜':11s} {'방향':9s} {'갭%':>7s} {'커버':>6s} {'진입':>8s} {'타깃':>8s} {'손절':>8s} {'거리%':>7s} {'VIX%':>6s} 결과")
        for r in recent:
            out.append(f"{r['d']:11s} {r['dir']:9s} {r['gap']:+7.3f} {r['cover']:6.2f} "
                       f"{r['entry']:8.2f} {r['target']:8.2f} {r['stop']:8.2f} {r['room']:7.3f} "
                       f"{(r['vix'] if r['vix'] is not None else 0):+6.2f} {'FILL' if r['filled'] else 'MISS'}")
    return out,res

if __name__=="__main__":
    try: o,res=main()
    except Exception: o=["실패:\n"+traceback.format_exc()]; res={}
    txt="\n".join(o); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt,"days":res},
              open("gapdays_result.json","w"),ensure_ascii=False,indent=1)
