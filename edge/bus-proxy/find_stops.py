#!/usr/bin/env python3
"""Find the 511 stop code and position of a stop by name. Run on YOUR computer; the key is typed hidden and never stored or printed.
   python3 edge/bus-proxy/find_stops.py "Rhode Island" "15th"
Prints matching Muni stops (id, name, lat, lon). Field names follow 511's docs and are unverified here; if nothing prints, run with --raw."""
import getpass, json, sys, urllib.parse, urllib.request
words = [w.lower() for w in sys.argv[1:] if not w.startswith('--')]
key = getpass.getpass('511 API key (hidden): ').strip()
url = 'https://api.511.org/transit/stops?' + urllib.parse.urlencode({'api_key': key, 'operator_id': 'SF', 'format': 'json'})
try:
    raw = urllib.request.urlopen(url, timeout=30).read().decode('utf-8-sig')
except Exception as e:
    sys.exit('request failed: ' + str(e).replace(key, '***'))
if '--raw' in sys.argv: print(raw[:3000]); sys.exit()
d = json.loads(raw); pts = d.get('Contents', {}).get('dataObjects', {}).get('ScheduledStopPoint', [])
n = 0
for p in pts:
    name = p.get('Name', '')
    if all(w in name.lower() for w in words):
        loc = p.get('Location', {}); print(p.get('id'), '|', name, '|', loc.get('Latitude'), loc.get('Longitude')); n += 1
print(n, 'match(es) of', len(pts))
