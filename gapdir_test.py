"""갭필 가정의 순수 방향 적중률.
가정: 첫봉이 갭의 30% 이상 되돌리면 갭 메우는 방향으로 간다.
질문: TP/SL 없이 홀드하면 그 방향이 맞는가? (갭필 여부가 아니라 방향 자체)
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

def wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k/n, 1.96; d = 1+z*z/n
    c = (p+z*z/(2*n))/d; h = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (round(max(0,c-h)*100,1), round(min(1,c+h)*100,1))

def build(tk):
    df = yf.download(tk, period="2y", interval="1h", prepost=False,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    df = df.dropna(); df.index = df.index.tz_convert("America/New_York")
    df = df[(df.index.time >= dt.time(9,30)) & (df.index.time < dt.time(16,0))]
    rows=[]; pc=None
    for d in sorted(set(df.index.date)):
        g = df[df.index.date==d]
        if len(g)<5:
            if len(g): pc=float(g["Close"].iloc[-1])
            continue
        O=[float(x) for x in g["Open"]]; C=[float(x) for x in g["Close"]]
        if pc:
            gap=O[0]-pc; gp=gap/pc*100
            if abs(gp)>=0.05:
                sgn = 1 if gap>0 else -1
                cover = ((O[0]-C[0])/gap) if sgn>0 else ((C[0]-O[0])/abs(gap))
                ep=C[0]
                i430 = min(4, len(C)-1)
                # 방향 = 갭 메우는 쪽 (갭업이면 하락, 갭다운이면 상승)
                r_eod = ((ep-C[-1])/ep*100) if sgn>0 else ((C[-1]-ep)/ep*100)
                r_430 = ((ep-C[i430])/ep*100) if sgn>0 else ((C[i430]-ep)/ep*100)
                rows.append(dict(d=str(d), sgn=sgn, gp=round(gp,3), cover=round(cover,3),
                                 eod=round(r_eod,3), h430=round(r_430,3)))
        pc=C[-1]
    return rows

def rep(ss, lab, out, key):
    n=len(ss)
    if n<10: out.append(f"    {lab:26s} n={n:4d} 표본부족"); return
    w=sum(1 for r in ss if r[key]>0); ci=wilson(w,n)
    g=sum(r[key] for r in ss if r[key]>0); l=-sum(r[key] for r in ss if r[key]<=0)
    s2=sorted(ss,key=lambda x:-x[key])[2:]
    g2=sum(r[key] for r in s2 if r[key]>0); l2=-sum(r[key] for r in s2 if r[key]<=0)
    ds=sorted(r["d"] for r in ss); half=ds[len(ds)//2]
    def _pf(x):
        a=sum(r[key] for r in x if r[key]>0); b=-sum(r[key] for r in x if r[key]<=0)
        return (a/b) if b>0 else 99.0
    out.append(f"    {lab:26s} n={n:4d} 방향적중 {w/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
               f"평균 {sum(r[key] for r in ss)/n:+.3f}% PF {g/l if l else 99:5.2f} "
               f"|상위2제외 {g2/l2 if l2 else 99:5.2f} | 반반 {_pf([r for r in ss if r['d']<half]):5.2f}/"
               f"{_pf([r for r in ss if r['d']>=half]):5.2f}")

def main():
    out=["가정: 첫봉 커버>=30% -> 갭 메우는 방향. TP/SL 없이 홀드 시 방향이 맞는가",
         "(양수 = 갭 메우는 방향으로 갔음)"]
    for tk in ("QQQ","SPY"):
        rows=build(tk)
        for key,kn in (("h430","10:30 → 14:30 홀드"),("eod","10:30 → 종가 홀드")):
            out.append(f"\n{'='*104}\n[{tk}] {kn} · {len(rows)}일\n{'='*104}")
            for sgn,nm in ((1,"갭업(하락 베팅)"),(-1,"갭다운(상승 베팅)")):
                ss=[r for r in rows if r["sgn"]==sgn]
                out.append(f"  ── {nm} ──")
                rep(ss,"전체 갭 (베이스라인)",out,key)
                rep([r for r in ss if r["cover"]>=0.3],"커버 >=30%",out,key)
                rep([r for r in ss if r["cover"]<0.3],"커버 <30%",out,key)
                rep([r for r in ss if r["cover"]>=0.5],"커버 >=50%",out,key)
                rep([r for r in ss if 0.3<=r["cover"]<1.0],"커버 30~100%",out,key)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gapdir_result.json","w"),ensure_ascii=False,indent=1)
