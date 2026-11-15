import numpy as np, xlb, math
from xlb.compute_backend import ComputeBackend
from xlb.grid import grid_factory
from xlb.operator.boundary_condition import HalfwayBounceBackBC, FullwayBounceBackBC
from xlb.operator.stepper import IncompressibleNavierStokesStepper
from xlb.precision_policy import PrecisionPolicy
from scipy.ndimage import binary_erosion
prec=PrecisionPolicy.FP32FP32; be=ComputeBackend.WARP
vs=xlb.velocity_set.D3Q19(precision_policy=prec,compute_backend=be)
xlb.init(velocity_set=vs,default_backend=be,default_precision_policy=prec)
n=(40,30,30); grid=grid_factory(n,compute_backend=be)
s=np.zeros(n,bool); s[15:25,10:20,1:11]=True; s[:,:,0]=True
shell=s&~binary_erosion(s,structure=np.ones((3,3,3),bool),border_value=1)
idx=[tuple(a.tolist()) for a in np.where(shell)]
st=IncompressibleNavierStokesStepper(grid=grid,boundary_conditions=[HalfwayBounceBackBC(indices=idx)],collision_type="SmagorinskyLESBGK")
f0,f1,bm,mm=st.prepare_fields()
b=bm.numpy()[0]; m=mm.numpy()
print("bc ids:",{int(v):int((b==v).sum()) for v in np.unique(b)})
print("shell cells",shell.sum(),"missing total per id:")
for v in np.unique(b):
    print(v, int(m[:,b==v].sum()))
# check fluid neighbour of shell front face
print("bc at (14,15,5):",b[14,15,5],"missing",m[:,14,15,5].astype(int).sum(), " at shell (15,15,5):",b[15,15,5], "missing",m[:,15,15,5].sum())
