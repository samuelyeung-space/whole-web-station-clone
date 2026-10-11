"""Read-only joint first-signal panel from two completed development batches.

The protocol must be registered before their results. All 52 candidates enter
without outcome selection. Recorded depth remains a simulated capacity bound.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import mine500_deep_batch as common

SCENARIOS=('size5','size20','capacity500','extra_1c')
LATENCIES=(500,600,1000)
CASH_CEILING=1e12


def read_json(path):
    return json.loads(Path(path).read_text())


def batch_metadata(root, expected_trials):
    root=Path(root)
    if (root/'invalidated_run.json').exists():
        raise ValueError('invalidated input batch')
    done,registration=read_json(root/'completion.json'),read_json(root/'registration.json')
    if (done.get('new_registered_trials')!=expected_trials or done.get('simulations_only') is not True
            or done.get('formal_oos_result') is not None or registration.get('new_trials')!=expected_trials):
        raise ValueError('unexpected or incomplete input batch')
    ids=[c['id'] for c in registration['candidates']]
    if len(ids)!=expected_trials or len(set(ids))!=len(ids):
        raise ValueError('duplicate or wrong candidate catalog')
    if not registration.get('input_hashes') or not registration.get('code_hashes'):
        raise ValueError('missing batch provenance hashes')
    return registration,set(ids)


def validate_registrations(first,second,protocol):
    if first['input_hashes']!=second['input_hashes']:
        raise ValueError('batches use different normalized input hashes')
    shared=set(first['code_hashes'])&set(second['code_hashes'])
    required={'mine500_deep_batch.py','mine500_replay_v2.py','mine500_shared_portfolio.py'}
    if not required.issubset(shared) or any(first['code_hashes'][name]!=second['code_hashes'][name] for name in shared):
        raise ValueError('shared dependency hashes differ')
    if common.digest(Path(common.__file__))!=first['code_hashes']['mine500_deep_batch.py']:
        raise ValueError('current panel implementation differs from batch')
    a,b={c['id'] for c in first['candidates']},{c['id'] for c in second['candidates']}
    if a&b or len(a|b)!=52 or set(protocol['candidate_ids'])!=a|b:
        raise ValueError('candidate conflict or preregistration mismatch')
    if (protocol.get('schema')!='joint52-outcome-blind-panel-protocol-v1'
            or protocol.get('candidate_count')!=52 or protocol.get('new_panel_selection_trials')!=1
            or protocol.get('known_multiplicity_lower_bound_including_zoo100',0)<873
            or protocol.get('results') is not None or protocol.get('shared_portfolio_cash_usd')!=1000
            or protocol.get('intent_cash_ceiling_usd')!=CASH_CEILING
            or tuple(protocol.get('scenarios',[]))!=SCENARIOS
            or tuple(protocol.get('latencies_ms',[]))!=LATENCIES):
        raise ValueError('unexpected joint protocol')
    # Candidate registration is before fitting in both existing runners.
    registered=datetime.fromisoformat(protocol['registered_at_utc'])
    if any(registered>datetime.fromisoformat(r['registered_at_utc']) for r in (first,second)):
        raise ValueError('joint policy was registered after an input batch began')


def combine_intents(first,second,first_ids,second_ids,expected_config):
    """Reject capital-censored or mismatched sources before shared-cash replay."""
    required=dict(expected_config,initial_cash_usd=CASH_CEILING)
    if first.get('config')!=required or second.get('config')!=required:
        raise ValueError('intent execution configs differ or are not uncensored')
    if tuple(required['latencies_ms'])!=LATENCIES:
        raise ValueError('unexpected latency grid')
    if first_ids&second_ids or len(first_ids|second_ids)!=52:
        raise ValueError('candidate catalogs conflict')
    attempts=[]
    for doc,ids in ((first,first_ids),(second,second_ids)):
        summary={(r['candidate_id'],r['latency_ms']) for r in doc['summary']}
        if len(doc['summary'])!=len(ids)*3 or summary!={(cid,lag) for cid in ids for lag in LATENCIES}:
            raise ValueError('missing or unknown candidate summaries')
        seen=set()
        for row in doc['attempts']:
            key=row['candidate_id'],row['market_id'],row['latency_ms']
            if row['candidate_id'] not in ids or row['latency_ms'] not in LATENCIES or key in seen:
                raise ValueError('unknown or duplicate candidate intent')
            if row.get('status')=='rejected_insufficient_available_cash':
                raise ValueError('intent input was capital censored')
            seen.add(key)
            attempts.append(row)
    return {'config':required,'attempts':attempts,'summary':first['summary']+second['summary']}


def run(first_batch,second_batch,protocol_file,output):
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    roots=[Path(first_batch),Path(second_batch)]
    protocol=read_json(protocol_file)
    first,first_ids=batch_metadata(roots[0],40)
    second,second_ids=batch_metadata(roots[1],12)
    validate_registrations(first,second,protocol)
    tracked=[Path(protocol_file),Path(__file__),Path(common.__file__),
             Path(common.__file__).with_name('mine500_shared_portfolio.py')]
    for root in roots:
        tracked.extend(root/name for name in ('completion.json','registration.json'))
        for scenario in SCENARIOS:
            tracked.extend(root/(scenario+suffix) for suffix in
                ('_panel_intent_attempts.jsonl.gz','_panel_intent_replay.json'))
    hashes={str(p.resolve()):common.digest(p) for p in tracked}
    audit={'schema':'joint52-development-panel-v1','started_at_utc':datetime.now(timezone.utc).isoformat(),
        'protocol_sha256':hashes[str(Path(protocol_file).resolve())],'input_and_code_hashes':hashes,
        'candidate_count':52,'new_panel_selection_trials':1,
        'known_multiplicity_lower_bound_including_zoo100':873,
        'other_historical_adaptive_trials':'additional; not erased or fully enumerated',
        'selection_uses_outcomes_or_pnl':False,'formal_oos_result':None,'confirmed_strategies':0}
    common.write_json(output/'registration.json',audit)
    configs=[{s['name']:s['config'] for s in registration['scenarios']} for registration in (first,second)]
    results=[]
    for scenario in SCENARIOS:
        if scenario not in configs[0] or configs[0].get(scenario)!=configs[1].get(scenario):
            raise ValueError('registered scenario configs differ')
        docs=[]
        for root in roots:
            doc=read_json(root/(scenario+'_panel_intent_replay.json'))
            doc['attempts']=list(common.execution.read_jsonl(root/(scenario+'_panel_intent_attempts.jsonl.gz')))
            docs.append(doc)
        doc=combine_intents(*docs,first_ids,second_ids,configs[0][scenario])
        panels=common.panel_results(doc)
        for panel in panels:
            panel.update(scenario=scenario,policy=protocol['policy'],candidate_count=52,
                portfolio_cash_usd=1000.,intent_cash_ceiling_usd=CASH_CEILING,
                intent_capital_censoring=False,selection_uses_outcomes_or_pnl=False)
            results.append(panel)
        common.write_json(output/(scenario+'_panels.json'),panels)
    if len(results)!=12:
        raise ValueError('joint scenario accounting mismatch')
    if any(common.digest(path)!=value for path,value in hashes.items()):
        common.write_json(output/'invalidated_run.json',{'reason':'input_or_code_changed_during_read'})
        raise ValueError('joint input or code changed')
    common.write_json(output/'joint52_panels.json',results)
    common.write_json(output/'completion.json',dict(audit,completed_at_utc=datetime.now(timezone.utc).isoformat(),
        panel_scenarios=12,simulations_only=True,results_saved=True))
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deep-batch',required=True)
    parser.add_argument('--tree-batch',required=True)
    parser.add_argument('--protocol',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    run(args.deep_batch,args.tree_batch,args.protocol,args.out)
