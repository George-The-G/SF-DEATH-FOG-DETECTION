import datetime as dt
import time
import tempfile
from pathlib import Path

import numpy as np

from microclimate import city
from microclimate.live import fog3d as F3, fog5

D = 'C:/Users/George/source/repos/SF-DEATH-FOG-DETECTION/data/live/'
S0 = city.load_static(D + 'live_static.npz')
S = {'tr80': S0['tr80'], 'dirs': S0['dirs']}
T3, _ = F3.load(D + 'live_tracer3d.npz', S['dirs'])
n = 79 * 6
t = np.arange(n) / 6.0
F = dict(ser=dict(wind_dir=270 + 15 * np.sin(t / 7), wind_speed_10m=4 + 2 * np.sin(t / 5) ** 2),
         s_series=np.clip(np.sin((t - 14) / 24 * 2 * np.pi) * 1.6, 0, 1), fog_top=300 + 120 * np.sin((t - 10) / 24 * 2 * np.pi))
t0 = dt.datetime(2026, 11, 14, 20, tzinfo=dt.timezone.utc)
now = t0 + dt.timedelta(hours=30)
xs = 540100.0 + 80 * (np.arange(214) + .5)
ys = 4171500.0 + 20 * 811 - 80 * (np.arange(203) + .5)
p = np.full((203, 214), 0.7, np.float32)
c = np.ones((203, 214), np.float32)
nowu = now.timestamp()
scan_at = lambda tt: (p, c) if (tt <= nowu and tt % 300 == 0) else None
for name, sa in (('no scans', lambda tt: None), ('scans every 5 min', scan_at)):
    with tempfile.TemporaryDirectory() as d:
        t1 = time.time()
        info = fog5.write(d, F, S, T3, None, t0, now, sa, xs, ys, log=lambda *_: None)
        print(name, 'seconds %.1f' % (time.time() - t1), {k: info[k] for k in ('frames', 'volumes', 'bytes', 'kinds')})
