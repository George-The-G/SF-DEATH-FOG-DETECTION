import sys,numpy as np
tag=sys.argv[1]; H=int(sys.argv[2]) if len(sys.argv)>2 else 20
d=np.load(f"C:/Users/George/deathfog-data/scratch/windcube_{tag}.npz"); u=d["u"].astype(float); s=d["solid"]
nx,ny,nz=s.shape; yc=ny//2; x1=3*H+H
# wind from 270 => flow toward +x; ux = -sin(270)*U = +1
ux=u[0]
print("speed ratio / ux along lee centreline at z=H/2 (x/H from cube back face):")
for xh in (0.1,0.25,0.5,1,1.5,2,3,4,5):
    i=int(x1+xh*H); k=1+H//2
    print(f" x/H={xh}: ux={ux[i,yc,k]:.3f} speed={np.sqrt((u[:,i,yc,k]**2).sum()):.3f}")
# reattachment length: first x where ux>0 at z=2 cells
k=2; row=ux[x1:,yc,k]; neg=np.where(row<0)[0]
print("recirc length x/H at k=2:", (neg.max()+1)/H if len(neg) else 0.0, " min ux:", row.min())
print("front stagnation ux at x/H=-0.25, z=H/2:", ux[int(3*H-0.25*H),yc,1+H//2])
print("side max speed ratio:", np.sqrt((u**2).sum(0))[3*H:4*H, yc+H//2+1:yc+H//2+4, 1+H//2].max())
