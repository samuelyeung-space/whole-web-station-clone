"""Extend unchanged MAR features to explicit consumed tapes; never fit a model.
Uses the original mean_regime_research.enrich function and saved JPR scores.
No future training backfill. Missing scores remain missing required features.
"""
import argparse
from collections import Counter
import gc
import gzip
import json
from pathlib import Path
import mean_regime_research as ma
import replay_saved_scores as saved


def prepare(mapping, scores_path, model_registration, model_audit, mar_registration, output):
    mr=json.loads(Path(mar_registration).read_text())
    if saved.sha(ma.__file__)!=mr['code_sha256']:
        raise ValueError('original MAR enrichment source changed')
    reg=json.loads(Path(model_registration).read_text())
    allowed=set(reg['input_sha256'].values())
    audit=json.loads(Path(model_audit).read_text())
    for r in audit:
        if 'model' in r and r['latest_label_ms']>=r['day']*saved.DAY_MS:
            raise ValueError('model label not strictly earlier UTC day')
    scores={(r['market_id'],r['recv_ms']):r['scores'] for r in saved.read(scores_path)}
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    result={'schema':'mar-existing-feature-extension-v1','inputs':[],
      'input_data_role':'already_consumed_development','model_fitting':False,
      'scores_sha256':saved.sha(scores_path),'model_audit_sha256':saved.sha(model_audit),
      'model_registration_sha256':saved.sha(model_registration),
      'mar_registration_sha256':saved.sha(mar_registration),
      'enrichment_code_sha256':saved.sha(ma.__file__),'adapter_sha256':saved.sha(__file__),
      'selection_bias':'Original book_5 and MAR thresholds selected using development; earlier-date extension remains retrospective development.',
      'not_official_twap':'trailing observed Binance price means; no settlement TWAP sample path',
      'formal_oos_result':None}
    for spec in mapping:
        endpoint,prices=map(Path,spec.split('=',1))
        if saved.sha(endpoint) not in allowed:raise ValueError('unregistered endpoint')
        dest=out/endpoint.name
        if dest.exists():raise ValueError('existing output cannot be overwritten')
        events,coverage=ma.enrich(endpoint,prices,scores)
        events[0]['dataset_id']+=':saved_mar_features'
        events[0]['mar_saved_features']={k:v for k,v in result.items() if k!='inputs'}
        events[0]['mar_saved_features'].update(endpoint_sha256=saved.sha(endpoint),prices_sha256=saved.sha(prices))
        counts=Counter(e['type'] for e in events)
        counts['decisions_with_book_5']=sum(e['type']=='decision' and 'book_5' in e['features'] for e in events)
        pending=dest.with_suffix(dest.suffix+'.pending')
        with gzip.open(pending,'wt',compresslevel=1) as f:
            for e in events:f.write(json.dumps(e,separators=(',',':'),allow_nan=False)+'\n')
        if sum(1 for _ in saved.read(pending))!=len(events):raise ValueError('record conservation failure')
        pending.replace(dest)
        result['inputs'].append({'input_path':str(endpoint),'input_sha256':saved.sha(endpoint),
          'prices_path':str(prices),'prices_sha256':saved.sha(prices),'output_path':str(dest),
          'output_sha256':saved.sha(dest),'event_counts':dict(counts),'feature_coverage':coverage})
        (out/'progress.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result['inputs'][-1]),flush=True)
        del events;gc.collect()
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mapping',action='append',required=True,help='endpoint-path=prices-path')
    for name in ('scores','model-registration','model-audit','mar-registration','output'):ap.add_argument('--'+name,required=True)
    a=ap.parse_args();prepare(a.mapping,a.scores,a.model_registration,a.model_audit,a.mar_registration,a.output)
