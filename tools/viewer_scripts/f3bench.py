import time, datetime as dt, tempfile, ctypes
from ctypes import wintypes
from pathlib import Path
import numpy as np
from microclimate import city, replay
from microclimate.live import outputs, fog3d as F3

def peak_mb():
    class PMC(ctypes.Structure):
        _fields_=[("cb",wintypes.DWORD),("PageFaultCount",wintypes.DWORD),("PeakWorkingSetSize",ctypes.c_size_t),("WorkingSetSize",ctypes.c_size_t),("a",ctypes.c_size_t),("b",ctypes.c_size_t),("c",ctypes.c_size_t),("d",ctypes.c_size_t),("PagefileUsage",ctypes.c_size_t),("PeakPagefileUsage",ctypes.c_size_t)]
    p=PMC(); p.cb=ctypes.sizeof(p)
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]; ctypes.windll.kernel32.GetCurrentProcess.restype=wintypes.HANDLE; ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(p), p.cb)
    return p.PeakWorkingSetSize/1e6
D='C:/Users/George/source/repos/SF-DEATH-FOG-DETECTION/data/live/'
S=city.load_static(D+'live_static.npz')
S={'tr80':S['tr80'],'dirs':S['dirs']}
n=72*6+1   # 72 h at 10 min
t=np.arange(n)/6.0
# synthetic fog night(s): a source that comes on around 20:00 each evening and goes off by 10:00, wind from 260-290, top 200-450 m
s=np.clip(np.sin((t-14)/24*2*np.pi)*1.6,0,1)
ser=dict(wind_dir=270+15*np.sin(t/7), wind_speed_10m=4+2*np.sin(t/5)**2)
top=300+120*np.sin((t-10)/24*2*np.pi)
Fo=dict(ser=ser,s_series=s,fog_top=top)
now=dt.datetime(2026,11,15,0,tzinfo=dt.timezone.utc)
th=[now-dt.timedelta(hours=18)+dt.timedelta(hours=k) for k in range(73)]
m0=peak_mb(); t0=time.time()
tiles,amps,Wts=outputs.fog_frames(S,{},Fo,None,th,6,return_amp=True)
t1=time.time()
Tz,_=F3.load(D+'live_tracer3d.npz',S['dirs'])
with tempfile.TemporaryDirectory() as d:
    t2=time.time()
    field,names=outputs.write_fog3d(Path(d),(Tz,{}),S,Fo,amps,Wts,6,th,now)
    t3=time.time()
    print('frames with files',len(names),'of',len(amps),'bytes',field['bytes'],'windowed',field.get('windowed_h'))
    print('write_fog3d seconds %.1f (2D fog_frames %.1f), peak MB before %.0f after %.0f'%(t3-t2,t1-t0,m0,peak_mb()))
    # sanity numbers on frames with fog: max over levels of the 3D file vs the 2D tile
    rs=[];aloft=[];cols=[]
    for f,fn in enumerate(field['files']):
        if not fn: continue
        from PIL import Image
        a=np.asarray(Image.open(Path(d)/fn)).astype(np.float32)
        vol=a.reshape(4,203,6,214).transpose(0,2,1,3).reshape(24,203,214)
        mx=vol.max(0); t2d=tiles[f].astype(np.float32)
        if mx.std()>0 and t2d.std()>0: rs.append(np.corrcoef(mx.ravel(),t2d.ravel())[0,1])
        fl=F3.encode  # noqa
        aloft.append(((mx>=16)&(t2d<4)).mean()); cols.append((mx>=16).mean())
    print('corr(max over levels, 2D tile): mean %.3f min %.3f over %d frames'%(np.mean(rs),np.min(rs),len(rs)))
    print('share of columns with fog aloft (>=16/255) but 2D tile <4: mean %.4f max %.4f (share of columns with 3D fog: mean %.3f)'%(np.mean(aloft),np.max(aloft),np.mean(cols)))
