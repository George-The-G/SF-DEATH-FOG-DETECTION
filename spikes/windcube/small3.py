import numpy as np
from microclimate import wind
from xlb.operator.stepper import IncompressibleNavierStokesStepper as S
orig=S.prepare_fields; cap={}
def pf(self,*a,**k):
    r=orig(self,*a,**k); cap['bm']=r[2].numpy()[0].copy(); cap['mm']=r[3].numpy().copy(); return r
S.prepare_fields=pf
nx,ny,nz=100,50,30
s=np.zeros((nx,ny,nz),bool); s[30:40,20:30,1:11]=True
wind.run(s,270,steps=1,avg_steps=1,avg_every=1,log=lambda *a:None)
b=cap['bm']; m=cap['mm']
print("unique ids",{int(v):int((b==v).sum()) for v in np.unique(b)})
for p in [(29,25,5),(30,25,5),(31,25,5),(35,25,11),(35,25,10),(28,25,5),(35,25,0),(35,25,1)]:
    print(p,"id",b[p],"missing",m[:,p[0],p[1],p[2]].astype(int).sum())
