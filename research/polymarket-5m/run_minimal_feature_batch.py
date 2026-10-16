"""Execute the preregistered MFA grid and all-rule fixed-priority shared control.

No selection from outcomes or isolated PnL. Run this only after feature build.
Logs and immutable replay-lab registrations are retained on failure.
"""
import argparse,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from minimal_feature_strategy import candidates


def run(root):
    root=Path(root).resolve();features=root/'features'
    complete=json.loads((features/'completion.json').read_text())
    if complete.get('formal_oos_result') is not None or not complete['fitting_completed']:
        raise ValueError('completed development feature build required')
    protocol={'registered_at_utc':datetime.now(timezone.utc).isoformat(),
        'candidates':[c['id'] for c in candidates()], 'shares':100,'initial_cash_usd':1000,
        'latencies_ms':[500,600,1000],'stresses':['base','half_depth','plus_1cent'],
        'modes':['isolated','shared'],'shared_priority':[c['id'] for c in candidates()],
        'shared_policy':'all8 canonical priority; earliest first signal consumes a market even if order fails; no PnL-based selection',
        'expected_isolated_rows':72,'expected_shared_rows':9,'formal_oos_result':None}
    p=root/'batch_protocol.json'
    if p.exists():raise ValueError('protocol exists; use content-addressed replay command for an explicit resume')
    p.write_text(json.dumps(protocol,indent=2)+'\n')
    code=Path(__file__).resolve().parent;lab=code/'replay_lab.py'
    def call(name,args):
        result=subprocess.run([sys.executable,str(lab),*args],cwd=code,text=True,capture_output=True)
        (root/(name+'.stdout.log')).write_text(result.stdout)
        (root/(name+'.stderr.log')).write_text(result.stderr)
        if result.returncode:raise RuntimeError(f'{name} failed; see retained logs, code={result.returncode}')
        value=json.loads(result.stdout.strip().splitlines()[-1])
        print(json.dumps({'phase':name,**{k:value[k] for k in ('cache_path','run_path','new_backtests','status') if k in value}}),flush=True)
        return value
    inputs=[item['output'] for item in complete['inputs']]
    imported=call('import',['import','--input',*inputs,'--input-type','normalized','--cache-root',str(root/'cache')])
    cache=imported['cache_path'];outputs={}
    for mode in ('isolated','shared'):
        outputs[mode]=call(mode,['run','--cache',cache,'--out',str(root/(mode+'_results')),
            '--strategy','minimal_feature_strategy:*','--mode',mode,'--shares','100','--cash','1000',
            '--latencies-ms','500,600,1000','--stresses','base,half_depth,plus_1cent'])
    (root/'batch_completion.json').write_text(json.dumps({'status':'completed_development_simulation',
        'outputs':outputs,'formal_oos_result':None},indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True)
    run(p.parse_args().root)
