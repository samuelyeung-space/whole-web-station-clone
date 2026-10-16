"""Preregister and execute all MEM scenarios; no outcome-driven selection."""
import argparse,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from minimal_error_motion_strategy import candidates

def run(root):
    root=Path(root).resolve();code=Path(__file__).resolve().parent
    feature=json.loads((root/'features/completion.json').read_text())
    protocol={'registered_at_utc':datetime.now(timezone.utc).isoformat(),'rules':candidates(),
        'initial_cash_usd':1000,'requested_shares':100,'latencies_ms':[500,600,1000],
        'stresses':['base','half_depth','plus_1cent'],'expected_isolated_rows':162,'expected_shared_rows':9,
        'shared_rules':[c['id'] for c in candidates() if not c.get('control')],
        'shared_priority':'canonical catalog order, all8 new rules only; no PnL selection',
        'formal_oos_result':None}
    p=root/'batch_protocol.json'
    if p.exists():raise ValueError('protocol already exists; use explicit content-addressed resume')
    p.write_text(json.dumps(protocol,indent=2)+'\n')
    def call(name,args):
        r=subprocess.run([sys.executable,str(code/'replay_lab.py'),*args],text=True,capture_output=True,cwd=code)
        (root/(name+'.stdout.log')).write_text(r.stdout);(root/(name+'.stderr.log')).write_text(r.stderr)
        if r.returncode:raise RuntimeError(name+' failed; logs retained')
        value=json.loads(r.stdout.strip().splitlines()[-1]);print(json.dumps({'phase':name,**value}),flush=True)
        return value
    imported=call('import',['import','--input',feature['output'],'--input-type','normalized','--cache-root',str(root/'cache')])
    outputs={}
    for mode in ('isolated','shared'):
        specs=['minimal_error_motion_strategy:*'] if mode=='isolated' else ['minimal_error_motion_strategy:'+i for i in protocol['shared_rules']]
        outputs[mode]=call(mode,['run','--cache',imported['cache_path'],'--out',str(root/(mode+'_results')),
            '--strategy',*specs,'--mode',mode,'--shares','100','--cash','1000',
            '--latencies-ms','500,600,1000','--stresses','base,half_depth,plus_1cent'])
    (root/'batch_completion.json').write_text(json.dumps({'status':'completed_development_simulation','outputs':outputs,'formal_oos_result':None},indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);run(p.parse_args().root)
