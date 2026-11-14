/* Lat/lon to the viewer's scene metres. Scene axes (see data/meta.json): x = east metres from E 540100, z = -(north metres from N 4171500),
   UTM zone 10N (WGS84). Standard transverse Mercator series; good to well under a metre over a city. */
const A = 6378137, F = 1 / 298.257223563, K0 = 0.9996, E2 = F * (2 - F), EP2 = E2 / (1 - E2), LON0 = -123 * Math.PI / 180;
export const X0 = 540100, Y0 = 4171500;
export function llToScene(lat, lon) {
  const p = lat * Math.PI / 180, l = lon * Math.PI / 180, s = Math.sin(p), c = Math.cos(p), t = Math.tan(p);
  const N = A / Math.sqrt(1 - E2 * s * s), T = t * t, C = EP2 * c * c, a = c * (l - LON0);
  const M = A * ((1 - E2 / 4 - 3 * E2 * E2 / 64 - 5 * E2 ** 3 / 256) * p - (3 * E2 / 8 + 3 * E2 * E2 / 32 + 45 * E2 ** 3 / 1024) * Math.sin(2 * p)
    + (15 * E2 * E2 / 256 + 45 * E2 ** 3 / 1024) * Math.sin(4 * p) - (35 * E2 ** 3 / 3072) * Math.sin(6 * p));
  const E = 500000 + K0 * N * (a + (1 - T + C) * a ** 3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * EP2) * a ** 5 / 120);
  const Nn = K0 * (M + N * t * (a * a / 2 + (5 - T + 9 * C + 4 * C * C) * a ** 4 / 24 + (61 - 58 * T + T * T + 600 * C - 330 * EP2) * a ** 6 / 720));
  return { x: E - X0, z: -(Nn - Y0) };
}
/* inverse of llToScene (a few Newton steps; metre accuracy is plenty), used by the mock feed to speak lat/lon like the real one */
export function sceneToLL(x, z) {
  let lat = 37.76 + (-z + Y0 - 4179000) / 111200, lon = -122.44 + (x + X0 - 548500) / 88000;
  for (let i = 0; i < 4; i++) {
    const a = llToScene(lat, lon), b = llToScene(lat + 1e-5, lon), c = llToScene(lat, lon + 1e-5);
    const dxdlat = (b.x - a.x) / 1e-5, dzdlat = (b.z - a.z) / 1e-5, dxdlon = (c.x - a.x) / 1e-5, dzdlon = (c.z - a.z) / 1e-5;
    const ex = x - a.x, ez = z - a.z, det = dxdlat * dzdlon - dxdlon * dzdlat;
    lat += (ex * dzdlon - ez * dxdlon) / det; lon += (dxdlat * ez - dzdlat * ex) / det;
  }
  return { lat, lon };
}
