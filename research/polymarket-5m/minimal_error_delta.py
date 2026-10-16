"""Compact MEM feature overlay; preserve a frozen base cache, never refit."""
import argparse,gzip,json
from pathlib import Path
from replay_lab import sha

def encode(base,derived,registration,out):
    reg=json.loads(Path(registration).read_text())
    if sha(base)!=reg['input_tape_sha256']:raise ValueError('frozen MFA base mismatch')
    rows=[]
    with gzip.open(derived,'rt') as stream:
        for line in stream:
            if '"type":"decision"' not in line:continue
            e=json.loads(line);f={k:v for k,v in e['features'].items() if k.startswith('mem_')}
            if f:rows.append({'market_id':e['market_id'],'recv_ms':e['recv_ms'],'features':f,
                'feature_available_ms':{k:e['feature_available_ms'][k] for k in f}})
    data={'schema':'mem-frozen-feature-overlay-v1','base_tape_sha256':sha(base),
        'derived_gzip_sha256':sha(derived),'dataset_id_suffix':':MEM-v1','rows':rows,
        'note':'gzip timestamps may differ on restore; verify canonical logical event hash with replay_dataset intake'}
    Path(out).write_text(json.dumps(data,separators=(',',':'),allow_nan=False)+'\n')
    print(json.dumps({'overlay_rows':len(rows),'bytes':Path(out).stat().st_size,'base_tape_sha256':data['base_tape_sha256']}))

def restored_lines(base,data):
    patches={(r['market_id'],r['recv_ms']):r for r in data['rows']}
    if len(patches)!=len(data['rows']):raise ValueError('duplicate overlay key')
    applied=0
    with gzip.open(base,'rt') as src:
        for line in src:
            if '"type":"dataset"' not in line and '"type":"decision"' not in line:
                yield line;continue
            e=json.loads(line)
            if e['type']=='dataset':
                if e['split']!='development':raise ValueError('consumed development only')
                e['dataset_id']+=data['dataset_id_suffix']
            else:
                patch=patches.get((e['market_id'],e['recv_ms']))
                if not patch:
                    yield line;continue
                for k,v in patch['features'].items():
                    if k in e['features']:raise ValueError('collision')
                    e['features'][k]=v;e['feature_available_ms'][k]=patch['feature_available_ms'][k]
                applied+=1
            yield json.dumps(e,separators=(',',':'),allow_nan=False)+'\n'
    if applied!=len(patches):raise ValueError('unmatched overlay')

def restore(base,delta,out):
    data=json.loads(Path(delta).read_text())
    if sha(base)!=data['base_tape_sha256']:raise ValueError('exact frozen MFA base required; no substitute/refit')
    dest=Path(out)
    if dest.exists():raise ValueError('do not overwrite existing tape')
    with gzip.open(dest,'wt',compresslevel=1) as dst:
        for line in restored_lines(base,data):dst.write(line)
    print(json.dumps({'applied':len(data['rows']),'status':'restored_development_features_only','pnl':None}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('encode','restore'));p.add_argument('--base',required=True)
    p.add_argument('--derived');p.add_argument('--registration');p.add_argument('--delta');p.add_argument('--out',required=True)
    a=p.parse_args()
    if a.mode=='encode':encode(a.base,a.derived,a.registration,a.out)
    else:restore(a.base,a.delta,a.out)
