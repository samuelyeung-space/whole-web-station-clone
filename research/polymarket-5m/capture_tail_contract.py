"""Label-blind sealed-capture tail diagnostic, never replay admission.

Writer drain/CRC says nothing about feed freshness. This check examines ONLY
runtime metadata and a declared stream/age contract. It does not read quotes,
outcomes, signals or future health, and cannot certify intrarun continuity.
The default30s bound is a coarse engineering diagnostic, not the replay's250ms
quote-age limit, an empirical market guarantee or an OOS protocol amendment.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path

DEFAULT_REQUIRED={'binance-strict':30000,'clob-btc':30000,'rtds':30000}


def finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def check(runtime,required=None):
    required=DEFAULT_REQUIRED.copy() if required is None else dict(required)
    if not required or any(not isinstance(k,str) or not k or not finite(v) or v<=0 for k,v in required.items()):
        raise ValueError('explicit positive stream-age contract required')
    start,end=runtime.get('started_ms'),runtime.get('ended_ms')
    if not finite(start) or not finite(end) or not 0<=start<end:
        raise ValueError('valid sealed receipt-clock span required')
    writer=runtime.get('writer',{})
    latest=writer.get('latest_stream_enqueue_ms',{})
    if not isinstance(latest,dict):raise ValueError('stream metadata must be a mapping')
    drained=(runtime.get('complete') is True and writer.get('error') is None
        and writer.get('pending_bytes')==0 and type(writer.get('accepted')) is int
        and writer.get('accepted')==writer.get('written')==writer.get('flushed'))
    streams=[]
    for name,maximum in sorted(required.items()):
        at=latest.get(name);age=None
        if not finite(at):reason='missing_or_nonfinite_last_enqueue'
        elif not start<=at<=end:reason='last_enqueue_outside_sealed_run'
        else:
            age=end-at;reason='stale_tail' if age>maximum else None
        streams.append(dict(stream=name,last_enqueue_ms=at if finite(at) else None,
            silence_to_sealed_end_ms=age,maximum_diagnostic_age_ms=maximum,
            tail_contract_passed=reason is None,reason=reason))
    passed=drained and all(s['tail_contract_passed'] for s in streams)
    return dict(schema='capture-tail-diagnostic-v1',run_id=runtime.get('run_id'),
        runtime_complete=runtime.get('complete') is True,writer_drained=drained,
        status='tail_contract_passed_not_feed_admission' if passed else 'blocked_tail_contract',
        stream_contract=required,streams=streams,quotes_or_outcomes_read=False,
        intrarun_continuity_verified=False,complete_days_verified=False,
        execution_input_admitted=False,financial_replays=0,formal_oos_result=None,
        limits=['Coarse terminal diagnostic;not250ms quote freshness or feed uptime.',
            'Missing writes do not by themselves prove connection failure or the cause.',
            'Silence is measured to sealed end and includes any shutdown drain.',
            'Even a passed tail contract cannot admit strategy execution or complete calendar coverage.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();source=Path(a.runtime);out=Path(a.out)
    if out.exists():raise ValueError('refusing prior diagnostic overwrite')
    raw=source.read_bytes();result=check(json.loads(raw))
    result['runtime_sha256']=hashlib.sha256(raw).hexdigest()
    result['diagnostic_code_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with out.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps(dict(status=result['status'],writer_drained=result['writer_drained'],
        failed_streams=[x['stream'] for x in result['streams'] if not x['tail_contract_passed']],financial_replays=0)))
    return 0 if result['status'].startswith('tail_contract_passed') else 2


if __name__=='__main__':raise SystemExit(main())
