"""갭 전략 → 0DTE 옵션 손익 환산 (델타 기반).

기초자산 백테스트 결과에 옵션 특성을 씌운다.
  프리미엄 변동 = 기초변동% x (스팟/프리미엄) x 델타   ← 레버리지
  0DTE ATM 델타 0.5, ITM 0.75, 딥ITM 0.9
  프리미엄 추정: ATM = 스팟 x IV x sqrt(T)/sqrt(252) 근사, ITM = 내재가치 + 시간가치
  세타: 보유 시간(분) 비례로 시간가치 감소
  비용: 왕복 스프레드

조건: QQQ 갭업 0.2~1.0% · 09:30 진입 · TP 전날종가 · SL 갭50% 역행 · 14:30 컷
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

VIXDEF=16.0
def wilson(k,n):
    if n==0: return (0.0,0.0)
    p,z=k/n,1.96; d=1+z*z/n
    c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (round(max(0,c-h)*100,1),round(min(1,c+h)*100,1))

def load():
    v=yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna()
    try: v.index=v.index.tz_localize(None)
    except: pass
    v.index=pd.to_datetime(v.index).normalize()
    vo={str(pd.Timestamp(k).date()):float(x) for k,x in v["Open"].items()}
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
                if 0.2<=abs(gp)<1.0 and gap>0:
                    ep=O[0]; tgt=pc; stop=ep+abs(gap)*0.5
                    hitT=hitS=None
                    for i in range(1,min(5,len(C))):
                        if hitS is None and H[i]>=stop: hitS=i
                        if hitT is None and L[i]<=tgt: hitT=i
                        if hitT is not None or hitS is not None: break
                    if hitT is not None and (hitS is None or hitT<=hitS):
                        res,ux,bars="TGT",(ep-tgt)/ep*100,hitT
                    elif hitS is not None:
                        res,ux,bars="STOP",-(stop-ep)/ep*100,hitS
                    else:
                        idx=min(4,len(C)-1); res,ux,bars="CUT",(ep-C[idx])/ep*100,idx
                    rows.append(dict(d=ds,gp=abs(gp),spot=ep,ux=ux,res=res,
                                     hold=bars*60,vix=vo.get(ds,VIXDEF)))
        pc=C[-1]
    return rows

def price_atm(spot,vix,hours_left):
    """0DTE ATM 프리미엄 근사: S*sigma*sqrt(T)*0.4 (T=잔여시간/연)"""
    T=max(hours_left,0.1)/(6.5*252)
    return spot*(vix/100)*math.sqrt(T)*0.8

def simulate(rows,delta,spread_pct,label,out):
    res=[]
    for r in rows:
        spot=r["spot"]; vix=r["vix"]
        atm=price_atm(spot,vix,6.5)                 # 09:30 잔여 6.5시간
        if delta<=0.55:  prem=atm; intr=0.0
        else:
            # ITM: 내재가치 + 잔여 시간가치(델타 높을수록 시간가치 작음)
            moneyness=(delta-0.5)*2*0.02            # 델타 0.75 -> 스팟대비 1% ITM
            intr=spot*moneyness
            prem=intr+atm*(1-(delta-0.5)*1.6)
        move=spot*r["ux"]/100                        # 기초 변동(달러, 이익방향 +)
        gain=move*delta
        # 세타: 보유시간만큼 시간가치 감소
        tv=prem-intr
        hours=r["hold"]/60
        tv_left=max(0.0, tv*math.sqrt(max(6.5-hours,0)/6.5))
        theta_loss=tv-tv_left
        cost=prem*spread_pct/100*2                   # 왕복 스프레드
        net=gain-theta_loss-cost
        res.append(dict(d=r["d"],prem=prem,pnl_usd=net*100,pnl_pct=net/prem*100,res=r["res"]))
    n=len(res); w=sum(1 for x in res if x["pnl_usd"]>0); ci=wilson(w,n)
    g=sum(x["pnl_usd"] for x in res if x["pnl_usd"]>0); l=-sum(x["pnl_usd"] for x in res if x["pnl_usd"]<=0)
    s2=sorted(res,key=lambda x:-x["pnl_usd"])[2:]
    g2=sum(x["pnl_usd"] for x in s2 if x["pnl_usd"]>0); l2=-sum(x["pnl_usd"] for x in s2 if x["pnl_usd"]<=0)
    ds=sorted(x["d"] for x in res); half=ds[len(ds)//2]
    def _pf(z):
        a=sum(x["pnl_usd"] for x in z if x["pnl_usd"]>0); b=-sum(x["pnl_usd"] for x in z if x["pnl_usd"]<=0)
        return (a/b) if b>0 else 99.0
    out.append(f"  {label:34s} 프리미엄평균 ${sum(x['prem'] for x in res)/n:6.2f} "
               f"| 승률 {w/n*100:5.1f}%({ci[0]:.0f}~{ci[1]:.0f}) PF {g/l if l else 99:5.2f} "
               f"|상위2 {g2/l2 if l2 else 99:5.2f} | 건당 ${sum(x['pnl_usd'] for x in res)/n:+7.2f} "
               f"합계 ${sum(x['pnl_usd'] for x in res):+8.0f} | 반반 {_pf([x for x in res if x['d']<half]):.2f}/"
               f"{_pf([x for x in res if x['d']>=half]):.2f}")

def main():
    rows=load()
    out=[f"QQQ 갭업 0.2~1.0% · 09:30 진입 · 2년 {len(rows)}건 (연 {len(rows)/2:.0f}회)",
         "0DTE 풋 매수 · 프리미엄은 VIX 기반 근사 · 세타는 보유시간 비례 · 비용=왕복 스프레드",""]
    rc={}
    for r in rows: rc[r["res"]]=rc.get(r["res"],0)+1
    out.append(f"기초자산 기준: {rc} · 평균 {sum(r['ux'] for r in rows)/len(rows):+.3f}% "
               f"· 평균보유 {sum(r['hold'] for r in rows)/len(rows):.0f}분\n")
    for delta,sp,lab in ((0.50,1.5,"ATM 델타0.50 스프레드1.5%"),
                         (0.50,3.0,"ATM 델타0.50 스프레드3.0%"),
                         (0.75,1.0,"ITM 델타0.75 스프레드1.0%"),
                         (0.75,2.0,"ITM 델타0.75 스프레드2.0%"),
                         (0.90,1.0,"딥ITM 델타0.90 스프레드1.0%"),
                         (0.90,2.5,"딥ITM 델타0.90 스프레드2.5%")):
        simulate(rows,delta,sp,lab,out)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gapopt_result.json","w"),ensure_ascii=False,indent=1)
