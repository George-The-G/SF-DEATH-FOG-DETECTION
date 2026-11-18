import numpy as np, sys
from microclimate import wind
H=10; nx,ny,nz=100,50,30
s=np.zeros((nx,ny,nz),bool); s[30:40,20:30,1:11]=True
for steps in (200,1000,5000):
    u,d=wind.run(s,270,steps=steps,avg_steps=1,avg_every=1,log=lambda *a:None)
    u=u.astype(float); print(steps,"ux row at yc,k=5 x=24..42:",np.round(u[0,24:42,25,5],2))
    print("   uz above roof x=35,k=11..16:",np.round(u[2,35,25,11:17],3), " rho", d["rho_min"],d["rho_max"])
