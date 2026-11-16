import sys, pickle, datetime as dt, copy
sys.path.insert(0, sys.argv[1])
import numpy as np
from microclimate.live import compare, config as C
rr, mt, snds = pickle.load(open(sys.argv[2], "rb"))
now = dt.datetime(2026, 11, 12, 12, tzinfo=dt.timezone.utc)
# pad the model 6 h past its end (edge repeat) so the 72 h window is covered
pad = 6
rr = copy.deepcopy(rr)
t_last = rr["times"][-1]
rr["times"] = list(rr["times"]) + [t_last + dt.timedelta(hours=i + 1) for i in range(pad)]
ext = lambda a: np.concatenate([a, np.repeat(a[..., -1:], pad, axis=-1)], axis=-1)
rr["vars"] = {k: ext(v) for k, v in rr["vars"].items()}
rr["prof"] = {k: ext(v) for k, v in rr["prof"].items()}
snd = [s for s in snds if s["valid"] == now][0]
obs = dict(coops=None, metar={k: [r for r in v if r["t"] <= now] for k, v in mt.items()}, nws={}, sounding=snd)
rc = compare.run((), out_dir=sys.argv[3], now=now, top_fusion=True, gathered=(rr, obs, None, {}), log=print)
print("rc", rc)
