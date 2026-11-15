import sys, os, numpy as np
from microclimate import wind
slip=sys.argv[1]=="slip"
LK='C:/Users/George/deathfog-data/library/runs/gpu.lock'
assert not os.path.exists(LK); open(LK,'w').write(str(os.getpid()))
try:
    s=np.zeros((int(os.environ.get("NX","600")),int(os.environ.get("NY","40")),int(os.environ.get("NZ","40"))),bool)
    u,d=wind.run(s,270,steps=int(os.environ.get("STEPS","30000")),avg_steps=int(os.environ.get("AVG","6000")),sea_slip=slip,log=print)
finally: os.remove(LK)
print(sys.argv[1],d["nan"])
u=u.astype(float); sp=np.sqrt((u[:2,:,u.shape[2]//2]**2).sum(0))
for xi in (30,150,300,u.shape[1]-20):
    print(f" x={xi*20/1000:.1f} km:", " ".join(f"z{(k+.5)*20:.0f}={np.sqrt((u[:2,xi,u.shape[2]//2,k]**2).sum()):.2f}" for k in (1,2,3,5,10,20,35)))
np.save("C:/Users/George/deathfog-data/scratch/sea_last.npy",u.astype(np.float16))
j=u.shape[2]//2
print("ux(k) at x=30:",np.round(u[0,30,j,:12],3)); print("uz(k) at x=30:",np.round(u[2,30,j,:12],3))
print("ux along x at k=1:",np.round(u[0,[2,5,10,20,40,80,160,300],j,1],3))
print("ux along x at k=30:",np.round(u[0,[2,5,10,20,40,80,160,300],j,30],3))
print("ux across y at x=160,k=5:",np.round(u[0,160,::20,5],3))
