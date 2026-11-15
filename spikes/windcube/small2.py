import numpy as np
from microclimate import wind
nx,ny,nz=100,50,30
s=np.zeros((nx,ny,nz),bool); s[30:40,20:30,1:11]=True
for steps in (2,20,300):
    u,d=wind.run(s,270,steps=steps,avg_steps=1,avg_every=1,log=lambda *a:None)
    u=u.astype(float)
    print(steps,"front wall node (29,25,5):",np.round(u[:,29,25,5],4),"(29,25,2):",np.round(u[:,29,25,2],4),"(28,25,5)",np.round(u[:,28,25,5],4),"(29,19,5)",np.round(u[:,29,19,5],4),"(35,25,11)",np.round(u[:,35,25,11],4))
