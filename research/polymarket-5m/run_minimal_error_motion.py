"""Preregister and execute all MEM scenarios; no outcome-driven selection."""
import argparse,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from minimal_error_motion_strategy import candidates

def strategy_args(specs):
    return [item for spec in specs for item in ('--strategy',spec)]

def run(root,resume=False):
    root=Path(root).resolve();code=Path(__file__).resolve().parent
    feature=json.loads((root/'features/completion.json').read_text())
    protocol={'registered_at_utc':datetime.now(timezone.utc).isoformat(),'rules':candidates(),
        'initial_cash_usd':1000,'requested_shares':100,'latencies_ms':[500,600,1000],
        'stresses':['base','half_depth','plus_1cent'],'expected_isolated_rows':162,'expected_shared_rows':9,
        'shared_rules':[c['id'] for c in candidates() if not c.get('control')],
        'shared_priority':'canonical catalog order, all8 new rules only; no PnL selection',
        'formal_oos_result':None}
    p=root/'batch_protocol.json'
    if p.exists():
        if not resume:raise ValueError('protocol already exists; request explicit --resume')
        stored=json.loads(p.read_text())
        if {k:v for k,v in stored.items() if k!='registered_at_utc'}!={k:v for k,v in protocol.items() if k!='registered_at_utc'}:
            raise ValueError('resume protocol differs; no implicit new trial')
        protocol=stored
    else:
        if resume:raise ValueError('no protocol to resume')
        p.write_text(json.dumps(protocol,indent=2)+'\n')
    def call(name,args):
        r=subprocess.run([sys.executable,str(code/'replay_lab.py'),*args],text=True,capture_output=True,cwd=code)
        label=name+'.resume' if (root/(name+'.stdout.log')).exists() else name
        if (root/(label+'.stdout.log')).exists():raise ValueError('resume logs exist; do not overwrite attempts')
        (root/(label+'.stdout.log')).write_text(r.stdout);(root/(label+'.stderr.log')).write_text(r.stderr)
        if r.returncode:raise RuntimeError(name+' failed; logs retained')
        value=json.loads(r.stdout.strip().splitlines()[-1]);print(json.dumps({'phase':name,**value}),flush=True)
        return value
    if resume and (root/'import.stdout.log').exists():
        from replay_dataset import verify_cache
        from replay_lab import sha
        imported=json.loads((root/'import.stdout.log').read_text().strip().splitlines()[-1])
        verify_cache(imported['cache_path'])
        if sha(feature['output'])!=feature['sha256']:raise ValueError('source feature tape changed')
    else:
        imported=call('import',['import','--input',feature['output'],'--input-type','normalized','--cache-root',str(root/'cache')])
    outputs={}
    for mode in ('isolated','shared'):
        specs=['minimal_error_motion_strategy:*'] if mode=='isolated' else ['minimal_error_motion_strategy:'+i for i in protocol['shared_rules']]
        outputs[mode]=call(mode,['run','--cache',imported['cache_path'],'--out',str(root/(mode+'_results')),
            *strategy_args(specs),'--mode',mode,'--shares','100','--cash','1000',
            '--latencies-ms','500,600,1000','--stresses','base,half_depth,plus_1cent'])
    (root/'batch_completion.json').write_text(json.dumps({'status':'completed_development_simulation','outputs':outputs,'formal_oos_result':None},indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--resume',action='store_true')
    a=p.parse_args();run(a.root,a.resume)
