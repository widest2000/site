exec(open("study.py").read().split("res={}")[0])
gk=[None]*len(dates); gq=[None]*len(dates)
for i in range(199,len(dates)):
    gk[i]=(k[i]/ma(k,i,200)-1)*100; gq[i]=(q[i]/ma(q,i,200)-1)*100
import datetime as dt
def mn(a,b):
    idx=[i for i in range(199,len(dates)) if a<=dates[i]<=b]
    i1=min(idx,key=lambda i:gk[i]); i2=min(idx,key=lambda i:gq[i])
    return (dates[i1],round(gk[i1],1)),(dates[i2],round(gq[i2],1))
print("2022ep",mn(dt.date(2022,1,6),dt.date(2023,1,25)))
print("2024ep",mn(dt.date(2024,9,27),dt.date(2025,2,17)))
print("2023-01-26~2026-11-09",mn(dt.date(2023,1,26),dt.date(2026,11,9)))
print("2008ep",mn(dt.date(2008,6,9),dt.date(2009,3,24)))
# 지금보다 코스피 괴리가 더 낮았던 마지막 날
last=[dates[i] for i in range(199,len(dates)-1) if gk[i]<=-19.6]; print("kospi<=-19.6 last",last[-3:] if last else None, len(last))
lastq=[dates[i] for i in range(199,len(dates)-1) if gq[i]<=-30.7]; print("kosdaq<=-30.7 last",lastq[-3:] if lastq else None, len(lastq))
i=len(dates)-1
while state[i-1]=="both_down": i-=1
print("current both_down since",dates[i], "days",len(dates)-i)
print("2020-03 kospi min", mn(dt.date(2020,2,1),dt.date(2020,6,30)))
