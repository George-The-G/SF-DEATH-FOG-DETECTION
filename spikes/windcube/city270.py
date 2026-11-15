import os, json, time, numpy as np
from microclimate import wind
LK='C:/Users/George/deathfog-data/library/runs/gpu.lock'
assert not os.path.exists(LK),'gpu busy'
open(LK,'w').write(str(os.getpid()))
try:
    solid=np.load("C:/Users/George/deathfog-data/grids/solid_20m.npz")["solid"]
    u,d=wind.run(solid,270,steps=40000,avg_steps=8000)
finally:
    os.remove(LK)
print(json.dumps(d))
json.dump(d,open("C:/Users/George/deathfog-data/scratch/windcube_city2700_fixed.json","w"))
np.savez_compressed("C:/Users/George/deathfog-data/scratch/windcube_city2700_fixed.npz",u=u)
