"""Pack the d3-celestial stars.6.json (BSD-3, from Hipparcos/Yale BSC, magnitude 6 and brighter, J2000)
into site/v3/stars.bin for the beta sky. Usage: python tools/pack_stars.py stars.6.json site/v3/stars.bin
Record (6 bytes, little endian): uint16 ra (0..360 deg), int16 dec (-90..90 deg), uint8 mag ((mag+2)*30), int8 bv (B-V*60).
Header: b'STR1' + uint32 count."""
import json, struct, sys
src, dst = sys.argv[1], sys.argv[2]
feats = json.load(open(src))["features"]
out = bytearray(b"STR1" + struct.pack("<I", len(feats)))
for f in sorted(feats, key=lambda f: f["properties"]["mag"]):
    lon, lat = f["geometry"]["coordinates"]
    ra = (lon % 360.0) / 360.0 * 65535
    de = lat / 90.0 * 32767
    mag = max(0, min(255, round((f["properties"]["mag"] + 2) * 30)))
    try: bv = float(f["properties"].get("bv") or 0.6)
    except ValueError: bv = 0.6
    out += struct.pack("<HhBb", round(ra), round(de), mag, max(-128, min(127, round(bv * 60))))
open(dst, "wb").write(out)
print(len(feats), "stars,", len(out), "bytes")
