import numpy as np, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from microclimate.paths import data_dir
D = str(data_dir())
def keys(p):
    try:
        z=np.load(p,allow_pickle=False,mmap_mode='r')
        if isinstance(z,np.ndarray): print(p, z.shape, z.dtype); return
        print(p); [print("   ",k, z[k].shape, z[k].dtype) for k in z.files]
    except Exception as e: print(p,"ERR",e)
for p in ["grids/solid_20m.npz","grids/terrain_20m.npy","grids/bldg_mean_h_20m.npy","refined/patch_corr_2700.npz","refined/patch_corr_2700_dx2p5.npz",
          "scratch/release_walls_k20v8/live_tracer3d_walls.npz","scratch/release_walls_k20v8/live_static_walls.npz","scratch/release_walls_k20v8/station_wind_walls.npz","live/live_extra.npz"]:
    keys(os.path.join(D,p))
z=np.load(os.path.join(D,"library/fog3d_2700_00.npz")); print("fog3d", z.files, z['c'].shape)
z=np.load(os.path.join(D,"library/wind_2700.npz")); print("wind", z.files, [z[k].shape for k in z.files])
print(os.listdir(os.path.join(D,"library_backup_prewalls_2026-11-16"))[:40])
print(os.listdir(os.path.join(D,"spikes/street5m_v2")))
print(os.listdir(os.path.join(D,"raw"))[:40])
