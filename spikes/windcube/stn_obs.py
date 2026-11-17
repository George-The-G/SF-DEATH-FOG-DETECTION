import json, urllib.request, io, numpy as np, datetime as dt
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"wind-check"}),timeout=40).read().decode()
# CO-OPS Fort Point (9414290) and Pier 1 (9414311), Oct 15 - Nov 14 2026
obs={}
for sid,name in (("9414290","FTPC1"),("9414311","PXOC1")):
    t=json.loads(get(f"https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=wind&station={sid}&begin_date=20261015&end_date=20261114&time_zone=gmt&units=metric&format=json&application=wind_check"))
    rows=[(r["t"],float(r["s"]),float(r["d"])) for r in t.get("data",[]) if r["s"] and r["d"]]
    obs[name]={dt.datetime.strptime(a,"%Y-%m-%d %H:%M").replace(minute=0):(s,d) for a,s,d in rows[::10]}  # ~hourly
# buoy 46026 offshore
txt=get("https://www.ndbc.noaa.gov/data/realtime2/46026.txt").splitlines()
buoy={}
for l in txt[2:]:
    p=l.split()
    try: t=dt.datetime(int(p[0]),int(p[1]),int(p[2]),int(p[3])); d=float(p[5]); s=float(p[6])
    except: continue
    if p[5]!="MM" and p[6]!="MM": buoy[t]=(s,d)
print("buoy hours",len(buoy),{k:len(v) for k,v in obs.items()})
for name,o in obs.items():
    r=[];ra=[]
    for t,(s,d) in o.items():
        if t in buoy:
            bs,bd=buoy[t]
            if 250<=bd<=290 and bs>=4 and 235<=d<=305: r.append(s/bs)
            if 250<=bd<=290 and bs>=4: ra.append(s/bs)
    print(name,"hours with offshore W wind (250-290, >=4 m/s) and local dir 235-305:",len(r),"median station/buoy speed",np.round(np.median(r),2) if r else None,"| any local dir:",len(ra),np.round(np.median(ra),2) if ra else None)
