"""갭 지속(continuation) 가설 검증 — 형님 원안.

가설: 갭업 후 첫봉이 갭의 30% 이상 되돌리면(음봉), 그 눌림은 일시적이고
      다시 갭 방향(위)으로 간다. 갭다운이면 대칭으로 아래로.

갭필 검증과 동일한 잣대(터치 기준)로 잰다:
  타깃A = 당일 시가 O[0] 재터치      (첫봉 되돌림을 되돌리는 것)
  타깃B = 당일 첫봉 고점 H[0] 돌파   (더 엄격: 갭 방향 신고가)
  손절  = 전날 종가(갭필 지점) 도달  (갭이 메워지면 가설 붕괴)
진입 = 첫봉 종가(10:30) · 컷 14:30
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

def wilson(k,n):
    if n==0: return (0.0,0.0)
    p,z=k/n,1.96; d=1+z*z/n
    c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (round(max(0,c-h)*100,1),round(min(1,c+h)*100,1))

def build(tk):
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
        O=[float(x) for x in g["Open"]];H=[float(x) for x in g["High"]]
        L=[float(x) for x in g["Low"]];C=[float(x) for x in g["Close"]]
        if pc:
            gap=O[0]-pc; gp=gap/pc*100
            if abs(gp)>=0.05:
                sgn=1 if gap>0 else -1
                cover=((O[0]-C[0])/gap) if sgn>0 else ((C[0]-O[0])/abs(gap))
                ep=C[0]; cut=4
                tA=O[0]; tB=H[0] if sgn>0 else L[0]; stop=pc
                hitA=hitB=hitS=None
                for i in range(1,min(cut+1,len(C))):
                    if hitA is None and ((H[i]>=tA) if sgn>0 else (L[i]<=tA)): hitA=i
                    if hitB is None and ((H[i]>=tB) if sgn>0 else (L[i]<=tB)): hitB=i
                    if hitS is None and ((L[i]<=stop) if sgn>0 else (H[i]>=stop)): hitS=i
                # 손절보다 먼저 타깃 도달했는가
                okA = hitA is not None and (hitS is None or hitA<=hitS)
                okB = hitB is not None and (hitS is None or hitB<=hitS)
                pnlA = (abs(tA-ep)/ep*100) if okA else (-abs(stop-ep)/ep*100 if hitS is not None
                        else ((C[min(cut,len(C)-1)]-ep)/ep*100*sgn))
                rows.append(dict(d=str(d),sgn=sgn,gp=round(gp,3),cover=round(cover,3),
                                 okA=okA,okB=okB,stopped=(hitS is not None),pnlA=round(pnlA,3)))
        pc=C[-1]
    return rows

def rep(ss,lab,out,key="okA"):
    n=len(ss)
    if n<10: out.append(f"    {lab:22s} n={n:4d} 표본부족"); return
    k=sum(1 for r in ss if r[key]); ci=wilson(k,n)
    st=sum(1 for r in ss if r["stopped"])
    g=sum(r["pnlA"] for r in ss if r["pnlA"]>0); l=-sum(r["pnlA"] for r in ss if r["pnlA"]<=0)
    ds=sorted(r["d"] for r in ss); half=ds[len(ds)//2]
    r1=sum(1 for r in ss if r["d"]<half and r[key])/max(1,len([r for r in ss if r["d"]<half]))*100
    r2=sum(1 for r in ss if r["d"]>=half and r[key])/max(1,len([r for r in ss if r["d"]>=half]))*100
    out.append(f"    {lab:22s} n={n:4d} 도달 {k/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
               f"반반 {r1:5.1f}/{r2:5.1f} | 갭필(손절) {st/n*100:4.1f}% | PF {g/l if l else 99:5.2f}")

def main():
    out=["갭 지속 가설: 첫봉 되돌림 후 갭 방향 재개. 손절=전날종가(갭필). 컷 14:30",
         "타깃A=당일 시가 재터치 · 타깃B=첫봉 극점 돌파"]
    for tk in ("QQQ","SPY"):
        rows=build(tk)
        out.append(f"\n{'='*96}\n[{tk}] {len(rows)}일\n{'='*96}")
        for sgn,nm in ((1,"갭업 → 롱(시가 재터치)"),(-1,"갭다운 → 숏(시가 재터치)")):
            ss=[r for r in rows if r["sgn"]==sgn]
            out.append(f"  ── {nm} ──")
            for key,kn in (("okA","[타깃A 시가]"),("okB","[타깃B 극점]")):
                out.append(f"   {kn}")
                rep(ss,"전체 갭",out,key)
                rep([r for r in ss if r["cover"]>=0.3],"커버 ≥30%",out,key)
                rep([r for r in ss if r["cover"]>=0.5],"커버 ≥50%",out,key)
                rep([r for r in ss if r["cover"]<0.3],"커버 <30%",out,key)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gapcont_result.json","w"),ensure_ascii=False,indent=1)
