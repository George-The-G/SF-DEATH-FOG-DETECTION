import numpy as np, inspect, textwrap
from microclimate import wind
src=inspect.getsource(wind.run)
src=src.replace("        f0, f1 = f1, f0\n","        f0, f1 = f1, f0\n        if s<6 or s in (50,300):\n            macro(f0, rho_f, u_f); wp.synchronize(); _u=u_f.numpy(); _r=rho_f.numpy(); print('step',s,'u@29',np.round(_u[:,29,25,5]/U_LAT,4),'u@28',np.round(_u[:,28,25,5]/U_LAT,4),'u@27',np.round(_u[:,27,25,5]/U_LAT,4),'rho@29',_r[0,29,25,5]); \n",1)
ns=dict(wind.__dict__); exec(textwrap.dedent(src),ns)
nx,ny,nz=100,50,30
s=np.zeros((nx,ny,nz),bool); s[30:40,20:30,1:11]=True
ns['run'](s,270,steps=301,avg_steps=1,avg_every=1,log=lambda *a:None)
