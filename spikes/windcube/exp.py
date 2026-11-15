import numpy as np, sys, warp as wp, xlb
from xlb.compute_backend import ComputeBackend
from xlb.grid import grid_factory
from xlb.operator.boundary_condition import *
from xlb.operator.macroscopic import Macroscopic
from xlb.operator.stepper import IncompressibleNavierStokesStepper
from xlb.precision_policy import PrecisionPolicy
from scipy.ndimage import binary_erosion
from xlb.operator.boundary_condition.boundary_condition_registry import boundary_condition_registry
mode=sys.argv[1]
prec=PrecisionPolicy.FP32FP32; be=ComputeBackend.WARP
vs=xlb.velocity_set.D3Q19(precision_policy=prec,compute_backend=be)
xlb.init(velocity_set=vs,default_backend=be,default_precision_policy=prec)
boundary_condition_registry.__init__()
nx,ny,nz=100,50,30; U=0.05
grid=grid_factory((nx,ny,nz),compute_backend=be)
s=np.zeros((nx,ny,nz),bool); s[30:40,20:30,1:11]=True; s[:,:,0]=True
def box(sl):
    m=np.zeros((nx,ny,nz),bool); m[sl]=True; return m
L=box((0,slice(1,-1),slice(1,-1))); R=box((nx-1,slice(1,-1),slice(1,-1)))
F=box((slice(None),0,slice(None))); B=box((slice(None),ny-1,slice(None))); T=box((slice(None),slice(None),nz-1))
for m in (L,R): m[:,:,0]=False
for m in (F,B,T): m[:,:,0]=False
shell=s&~binary_erosion(s,structure=np.ones((3,3,3),bool),border_value=1)
ix=lambda m:[tuple(a.tolist()) for a in np.where(m)]
if mode=="orig":   # same BC mix as wind.run
    bcs=[RegularizedBC("velocity",prescribed_value=(U,0,0),indices=ix(L&~s)),ExtrapolationOutflowBC(indices=ix(R&~s)),EquilibriumBC(1.0,(U,0,0),indices=ix(F|B|T)),HalfwayBounceBackBC(indices=ix(shell&~(L|R|F|B|T)))]
elif mode=="noeq": # lateral/top as free-slip-less: use FullwayBB (no-slip walls) instead of equilibrium
    bcs=[RegularizedBC("velocity",prescribed_value=(U,0,0),indices=ix(L&~s)),ExtrapolationOutflowBC(indices=ix(R&~s)),FullwayBounceBackBC(indices=ix((F|B|T)&~(L|R))),HalfwayBounceBackBC(indices=ix(shell&~(L|R|F|B|T)))]
elif mode=="halfway_first":
    bcs=[HalfwayBounceBackBC(indices=ix(shell&~(L|R|F|B|T))),RegularizedBC("velocity",prescribed_value=(U,0,0),indices=ix(L&~s)),ExtrapolationOutflowBC(indices=ix(R&~s)),EquilibriumBC(1.0,(U,0,0),indices=ix(F|B|T))]
st=IncompressibleNavierStokesStepper(grid=grid,boundary_conditions=bcs,collision_type="SmagorinskyLESBGK")
f0,f1,bm,mm=st.prepare_fields()
macro=Macroscopic(compute_backend=be,precision_policy=prec,velocity_set=vs)
rho=grid.create_field(cardinality=1,dtype=prec.store_precision); u=grid.create_field(cardinality=3,dtype=prec.store_precision)
for t in range(1500):
    f0,f1=st(f0,f1,bm,mm,1.95,t); f0,f1=f1,f0
macro(f0,rho,u); wp.synchronize(); uu=u.numpy()/U
print(mode,"ux x=24..32 at yc,k=5:",np.round(uu[0,24:32,25,5],3)," roof uz:",np.round(uu[2,35,25,11:15],3))
