import numpy as np
solid=np.load("C:/Users/George/deathfog-data/grids/solid_20m.npz")["solid"]
old=np.load("C:/Users/George/deathfog-data/library/wind_2700.npz")["u"].astype(np.float32)
new=np.load("C:/Users/George/deathfog-data/scratch/windcube_city2700_fixed.npz")["u"].astype(np.float32)
nx,ny,nz=solid.shape; print("grid",solid.shape)
top=nz-1-np.argmax(solid[:,:,::-1],axis=2)
ii,jj=np.meshgrid(np.arange(nx),np.arange(ny),indexing="ij")
def layer(f,k):
    kk=top+k; ok=kk<nz-6
    sp=np.sqrt((f[:,ii[ok],jj[ok],kk[ok]]**2).sum(0)); ux=f[0][ii[ok],jj[ok],kk[ok]]
    return sp,ux
print("layer k above highest solid: percentiles 5/25/50/75/95 of speed ratio (old | new), frac reversed ux<0 (old|new)")
for k in (1,2,3,5,8,12,20):
    so,uo=layer(old,k); sn,un=layer(new,k)
    p=lambda a:" ".join(f"{x:.2f}" for x in np.percentile(a,[5,25,50,75,95]))
    print(f" k={k:2d} (~{(k+0.5)*20:3.0f} m): old [{p(so)}] new [{p(sn)}]  rev {np.mean(uo<0):.3f} | {np.mean(un<0):.3f}")
# domain mean speed ratio by height above sea level over open columns
for z in (3,6,10,20,40,55):
    m=~solid[:,:,z]
    print(f" z={z} ({z*20} m): mean ratio old {np.sqrt((old[:,:,:,z]**2).sum(0))[m].mean():.3f} new {np.sqrt((new[:,:,:,z]**2).sum(0))[m].mean():.3f}")
# west->east gradient at k=2 (mean ux over x bands)
print("mean speed by x tenth, k=3: ")
so,_=layer(old,3); sn,_=layer(new,3)
ok=(top+3)<nz-6; xi=ii[ok]
for b in range(10):
    s=(xi>=b*nx//10)&(xi<(b+1)*nx//10)
    print(f"  x band {b}: old {so[s].mean():.3f} new {sn[s].mean():.3f}")
