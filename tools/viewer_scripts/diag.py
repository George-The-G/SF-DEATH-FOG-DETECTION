import json,urllib.request,numpy as np,io,os,time
from PIL import Image
B='https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/live_beta/'
def get(fn):
    if not os.path.exists(fn):
        for i in range(3):
            try:
                open(fn,'wb').write(urllib.request.urlopen(B+fn+'?x=9').read()); break
            except Exception as e:
                time.sleep(3)
    return open(fn,'rb').read()
idx=json.load(open('index.json')); f3=idx['fog3d']; g=idx['fog_grid']
wx=json.loads(get('weather.json'))
sheets=[np.asarray(Image.open(io.BytesIO(get(fn)))) for fn in idx['fog_files']]
tiles=[]
for sh,cnt in zip(sheets,g['tiles_per_file']):
    for k in range(cnt):
        r,c=divmod(k,g['cols']); tiles.append(sh[r*g['h']:(r+1)*g['h'],c*g['w']:(c+1)*g['w']].astype(np.float32))
rows=[]
for f,fn in enumerate(f3['files']):
    if not fn: continue
    a=np.asarray(Image.open(io.BytesIO(get(fn)))).astype(np.float32)
    vol=a.reshape(4,203,6,214).transpose(0,2,1,3).reshape(24,203,214)
    t=tiles[f]; mx=vol.max(0)
    def r(x):
        return np.corrcoef(x.ravel(),t.ravel())[0,1] if x.std()>0 and t.std()>0 else float('nan')
    rows.append((f,r(mx),r(vol[1]),r(vol[:4].max(0)),f3['top_layer'][f],wx['fog_top_m'][f],t.mean(),mx.mean(),(mx>=250).mean()))
rows=np.array(rows)
print('mean r: max-over-levels %.3f | level1 (37.5 m) %.3f | levels0-3 max %.3f'%tuple(np.nanmean(rows[:,[1,2,3]],axis=0)))
print('frame, r_max, r_lvl1, r_lvl0-3, top_layer, fog_top, tile_mean, 3Dmax_mean, share_saturated for frames r_max<0.8:')
for r in rows[rows[:,1]<0.8][:12]: print(np.round(r,2))
print('median r level1 %.3f, frames r_lvl1<0.8: %d of %d'%(np.nanmedian(rows[:,2]),(rows[:,2]<0.8).sum(),len(rows)))
