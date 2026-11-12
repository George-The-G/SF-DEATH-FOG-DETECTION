#!/usr/bin/env python3
"""Step 3 'Done when': a 72 h run on the full city static table, hourly output, tracer table, 3 h lag blocks: time and peak RAM.

    USERPROFILE=<home with deathfog-data> python tools/bench_72h.py [live_static.npz]
"""
import ctypes
import sys
import time
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microclimate import city, weather, wetness  # noqa: E402


def peak_mb():
    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    p = PMC()
    p.cb = ctypes.sizeof(PMC)
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    ctypes.windll.psapi.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(p), p.cb)
    return p.PeakWorkingSetSize / 1e6


npz = sys.argv[1] if len(sys.argv) > 1 else str(Path.home().parent / "deathfog-data" / "live" / "live_static.npz")
t0 = time.time()
P = city.load_static(npz)
t_load = time.time() - t0
D = Path.home() / "deathfog-data"
data = weather.fetch(37.76, -122.4, D / "raw" / "weather_37.76_-122.4.json")
start = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
ser = weather.series(data, start, start + timedelta(hours=72), 10)
t1 = time.time()
W, diag = wetness.simulate(P, ser, 10, tracer_dirs=P["tracer"], dirs_deg=P["dirs"], lag_block_h=3, out_every=6, keep_lit=False)
t_run = time.time() - t1
print(f"N={len(P['x'])} steps={len(ser['t_utc'])} output frames={W.shape[0]} load {t_load:.1f} s, run {t_run:.1f} s, peak RAM {peak_mb():.0f} MB")
print(f"W mean over frames: max {W.mean(axis=1).max():.1f}, last {W[-1].mean():.1f}, finite {np.isfinite(W).all()}, blocks {len(diag['block_dirs'])}")
ok = t_run < 300 and peak_mb() < 2048
print("PASS" if ok else "FAIL", "(< 5 min and < 2 GB)")
sys.exit(0 if ok else 1)
