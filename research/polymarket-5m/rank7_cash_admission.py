"""Prospectively pinned cash guard layered over the unchanged rank7 v2 gate.

Old gates/protocols are retained. This additional admission identity must be
registered before its replay; it cannot retroactively upgrade an old result.
"""
import argparse
import gzip
import json
from pathlib import Path
import seven_day_ranking as base
from rank7_cash_integrity import audit_cash,read_cash_csv


def assess(protocol,run_root,cache_root=None):
    root=Path(run_root).resolve()
    result=dict(schema='rank7-cash-assessment-v1',status='blocked',ranking=[],unranked=[],
                formal_oos_result=None,qualified_strategies=0,net_pnl_usd=None)
    try:
        base.require(protocol.get('schema')=='rank7-cash-protocol-v1','cash protocol identity required')
        expected={n:base.sha(Path(__file__).with_name(n)) for n in
                  ('rank7_cash_admission.py','rank7_cash_integrity.py')}
        base.require(protocol.get('cash_admission_code_sha256')==expected,'cash admission code identity mismatch')
        registration=base.read(root/'registration.json')
        base.require(base.utc(protocol['registered_at_utc'])<=base.utc(registration['registered_at_utc']),
                     'cash guard registered after replay; old results cannot inherit it')
        checked=base.assess(protocol['rank7_protocol'],root,cache_root)
        base.require(checked['status']=='assessed_development_only',
                     'base rank7 gate: '+'; '.join(checked.get('reasons',[])))
        completion=base.read(root/'completion.json');evidence={}
        for scenario in registration['scenarios']:
            name=scenario['name'];directory=root/name
            audit=base.read(directory/'audit.json')
            base.require(audit['reporting']['cash_basis']=='engine_recorded_event',
                         'original ordered cash events required')
            with gzip.open(directory/'attempts.jsonl.gz','rt') as stream:
                attempts=[json.loads(line,object_pairs_hook=base._object,
                    parse_constant=lambda x:base.require(False,'nonfinite attempt JSON: '+x)) for line in stream]
            evidence[name]=audit_cash(attempts,read_cash_csv(directory/'cash_ledger.csv'),audit['summary'])
            for filename in ('audit.json','attempts.jsonl.gz','cash_ledger.csv'):
                base.require(base.sha(directory/filename)==completion['output_sha256'][name+'/'+filename],
                             'cash inputs changed during verification')
        result.update(checked)
        result.update(schema='rank7-cash-assessment-v1',cash_evidence=evidence,
                      cash_protocol_sha256=base.fingerprint(protocol),cash_admission_code_sha256=expected)
    except (ValueError,KeyError,TypeError,OSError,EOFError) as error:
        result.update(status='blocked',reasons=[str(error)],ranking=[],unranked=[])
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('protocol','run','cache','out'):p.add_argument('--'+name,required=True)
    a=p.parse_args();r=assess(base.read(a.protocol),a.run,a.cache)
    Path(a.out).write_text(json.dumps(r,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    return 2 if r['status']=='blocked' else 0


if __name__=='__main__':raise SystemExit(main())
