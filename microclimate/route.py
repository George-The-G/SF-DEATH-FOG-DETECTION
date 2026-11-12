"""Skate-route finder: cheapest downhill street path from the start node to the bus stop at the bottom of the hill."""
import heapq
import math


def downhill_routes(zone, start, max_m=1600, climb_penalty=40.0, snap_m=40.0, min_drop_m=10.0, top=3):
    """Dijkstra where climbing costs extra, so paths prefer going down. Returns up to `top` candidates sorted by drop."""
    cost, prev, length = {start: 0.0}, {}, {start: 0.0}
    heap = [(0.0, start)]
    while heap:
        c, u = heapq.heappop(heap)
        if c > cost.get(u, 1e18):
            continue
        for v in zone.adj.get(u, ()):
            d = zone.dist(u, v)
            L = length[u] + d
            if L > max_m:
                continue
            rise = max(0.0, zone.z(v) - zone.z(u))
            nc = c + d + climb_penalty * rise
            if nc < cost.get(v, 1e18):
                cost[v], prev[v], length[v] = nc, u, L
                heapq.heappush(heap, (nc, v))
    out = []
    for lat, lon, name in zone.bus:
        x, y = zone.t.transform(lon, lat)
        best = min(length, key=lambda n: math.hypot(zone.xy(n)[0] - x, zone.xy(n)[1] - y))
        if math.hypot(zone.xy(best)[0] - x, zone.xy(best)[1] - y) > snap_m:
            continue
        drop = zone.z(start) - zone.z(best)
        if drop < min_drop_m or length[best] < 150:
            continue
        path, n = [best], best
        while n != start:
            n = prev[n]
            path.append(n)
        path.reverse()
        climb = sum(max(0.0, zone.z(b) - zone.z(a)) for a, b in zip(path, path[1:]))
        out.append({"stop": (lat, lon, name), "path": path, "length_m": round(length[best]), "drop_m": round(drop, 1),
                    "climb_m": round(climb, 1)})
    seen, uniq = set(), []
    for o in sorted(out, key=lambda o: -o["drop_m"]):
        k = (round(o["stop"][0], 4), round(o["stop"][1], 4))
        if k not in seen:
            seen.add(k)
            uniq.append(o)
    return uniq[:top]
