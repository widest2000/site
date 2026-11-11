"""200일선 국면별 이후 코스피·코스닥 수익률 (Yahoo 일봉 2000~ + 네이버 최근일 보정)"""
import json,datetime,re,statistics as st
def yahoo(p):
    r=json.load(open(p))["chart"]["result"][0]
    return {datetime.datetime.fromtimestamp(t+9*3600,datetime.timezone.utc).date():x for t,x in zip(r["timestamp"],r["indicators"]["quote"][0]["close"]) if x}
def naver(p):
    out={}
    for row in re.findall(r'data="([^"]+)"',open(p,encoding="euc-kr",errors="ignore").read()):
        f=row.split("|"); out[datetime.date(int(f[0][:4]),int(f[0][4:6]),int(f[0][6:]))]=float(f[4])
    return out
K=yahoo("/tmp/D_%5EKS11.json"); Q=yahoo("/tmp/D_%5EKQ11.json")
NK=naver("/tmp/L_KOSPI.xml"); NQ=naver("/tmp/L_KOSDAQ.xml")
# 2014-08 이후는 네이버(프로젝트 표준)로 덮어쓴다
cut=min(NK)  # 네이버 첫날(2014-08-18)부터는 네이버 날짜·값만 쓴다
K={d:v for d,v in K.items() if d<cut}; Q={d:v for d,v in Q.items() if d<cut}
K.update(NK); Q.update(NQ)
dates=sorted(set(K)&set(Q))
k=[K[d] for d in dates]; q=[Q[d] for d in dates]
def ma(a,i,n): return sum(a[i-n+1:i+1])/n
state=[None]*len(dates)
for i in range(199,len(dates)):
    ka=k[i]>ma(k,i,200); qa=q[i]>ma(q,i,200)
    state[i]="both_up" if ka and qa else ("both_down" if not ka and not qa else "mixed")
res={}
for H,name in [(21,"1개월"),(63,"3개월"),(126,"6개월")]:
    for s in ["both_up","mixed","both_down"]:
        for idx,arr in [("KOSPI",k),("KOSDAQ",q)]:
            rr=[(arr[i+H]/arr[i]-1)*100 for i in range(199,len(dates)-H) if state[i]==s]
            res[(H,s,idx)]=(len(rr),round(st.mean(rr),1),round(st.median(rr),1),round(sum(x>0 for x in rr)/len(rr)*100),round(min(rr),1))
for key,v in res.items(): print(key,v)
# 국면 일수 비율
from collections import Counter
c=Counter(s for s in state if s); tot=sum(c.values()); print({k:(v,round(v/tot*100)) for k,v in c.items()}, dates[199], dates[-1])
# 둘 다 아래 구간(연속 구간) 목록
eps=[];cur=None
for i in range(199,len(dates)):
    if state[i]=="both_down":
        if cur is None: cur=[dates[i],dates[i]]
        else: cur[1]=dates[i]
    else:
        if cur: eps.append(tuple(cur)); cur=None
if cur: eps.append(tuple(cur))
di={d:i for i,d in enumerate(dates)}
lens=[(e[0],e[1],di[e[1]]-di[e[0]]+1) for e in eps]
long=[e for e in lens if e[2]>=60]
print("episodes",len(eps),"60거래일 이상",len(long)); [print(e) for e in long]
print("20거래일 미만",sum(1 for e in lens if e[2]<20))
print("current state",state[-1],dates[-1])
print("--- 하위10%/상위10% (6개월, 3개월)")
for H in (63,126):
    for s in ["both_up","mixed","both_down"]:
        rr=sorted((k[i+H]/k[i]-1)*100 for i in range(199,len(dates)-H) if state[i]==s)
        n=len(rr); print(H,s,"p10",round(rr[int(n*0.1)],1),"p90",round(rr[int(n*0.9)],1),"sd",round(st.pstdev(rr),1))
