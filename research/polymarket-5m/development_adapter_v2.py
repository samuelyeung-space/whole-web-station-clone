"""Development-only historical Gamma metadata adapter.

Missing optional metadata is not a conflicting market definition. All explicit
definitions must agree, and the first locally received row remains the only
metadata available at the original mapping receipt time. Later metadata never
fills fields of that first row. The frozen mine500 adapter remains unchanged.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
import shutil
from pathlib import Path
import mine500_adapter as frozen_adapter


def first_raw_metadata(path):
    earliest, signatures, explicit_configs = {}, {}, {}
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            slug = str(row.get('slug', ''))
            if not slug.startswith('btc-updown-5m-'):
                continue
            now = row.get('_recorded_at_ms')
            if not frozen_adapter.finite(now):
                raise ValueError('raw market missing local receipt clock')
            outcomes, tokens = row['outcomes'], row['clobTokenIds']
            outcomes = json.loads(outcomes) if isinstance(outcomes,str) else outcomes
            tokens = json.loads(tokens) if isinstance(tokens,str) else tokens
            if set(outcomes) != {'Up','Down'} or len(tokens) != 2:
                raise ValueError('raw market token mapping invalid')
            fee = row.get('feeSchedule')
            rate = None
            if fee is not None:
                if fee.get('exponent') != 1 or fee.get('takerOnly') is not True:
                    raise ValueError('unsupported raw market fee schedule')
                rate = float(fee['rate'])
            mid = str(row['conditionId'])
            info = {'recv_ms':float(now),'slug':slug,
                'up_token_id':str(tokens[outcomes.index('Up')]),
                'down_token_id':str(tokens[outcomes.index('Down')]),
                'fee_rate':rate,'fee_provenance':'raw_gamma_feeSchedule' if rate is not None else 'missing',
                'resolution_source_url':row.get('resolutionSource'),
                'crypto_market_config':row.get('cryptoMarketConfig')}
            signature=(slug,info['up_token_id'],info['down_token_id'],rate,info['resolution_source_url'])
            if mid in signatures and signatures[mid] != signature:
                raise ValueError('market definition or fee changed; time-varying metadata adapter required')
            signatures[mid]=signature
            if info['crypto_market_config'] is not None:
                config=json.dumps(info['crypto_market_config'],sort_keys=True)
                if mid in explicit_configs and explicit_configs[mid] != config:
                    raise ValueError('conflicting explicit crypto market definitions')
                explicit_configs[mid]=config
            if mid not in earliest or now < earliest[mid]['recv_ms']:
                earliest[mid]=info
    return earliest


@contextmanager
def _scoped_metadata_reader():
    """Single-process CLI scope only; restoration also occurs on conversion error."""
    original=frozen_adapter.first_raw_metadata
    frozen_adapter.first_raw_metadata=first_raw_metadata
    try:
        yield
    finally:
        frozen_adapter.first_raw_metadata=original


def convert(*args,**kwargs):
    with _scoped_metadata_reader():
        result=frozen_adapter.convert(*args,**kwargs)
    result['development_adapter_version']='optional-metadata-causal-v2'
    result['adapter_code_sha256']=frozen_adapter._sha(Path(__file__))
    result['base_adapter_code_sha256']=frozen_adapter._sha(Path(frozen_adapter.__file__))
    result['metadata_policy']='First local receipt values retained; null optional cryptoMarketConfig never forward-filled; explicit definitions, mapping and fees must be consistent.'
    result['fresh_oos_authorized']=False
    return stamp_header(result)


def stamp_header(result):
    """Add evaluator identity without changing any causal event or timestamp."""
    path=Path(result['output'])
    before=frozen_adapter._sha(path)
    opener=gzip.open if path.suffix=='.gz' else open
    with opener(path,'rt',encoding='utf-8') as source:
        header=json.loads(source.readline())
        if header.get('type')!='dataset' or header.get('split')!='development':
            raise ValueError('header stamping is restricted to development datasets')
        codehash=frozen_adapter._sha(Path(__file__))
        basehash=frozen_adapter._sha(Path(frozen_adapter.__file__))
        additions={'development_adapter_code_sha256':codehash,
                   'base_adapter_code_sha256':basehash,
                   'development_adapter_version':'optional-metadata-causal-v2',
                   'metadata_policy':'First local receipt values retained; null optional cryptoMarketConfig never forward-filled; explicit definitions, mapping and fees must be consistent.',
                   'freeze_compatibility':'development_v2_not_old_confirmation'}
        unchanged=all(header.get(k)==v for k,v in additions.items())
        header.update(additions)
        if not unchanged:
            temp=path.with_name(path.name+'.header.tmp')
            kwargs={'compresslevel':1} if path.suffix=='.gz' else {}
            with opener(temp,'wt',encoding='utf-8',**kwargs) as target:
                target.write(json.dumps(header,separators=(',',':'),allow_nan=False)+'\n')
                shutil.copyfileobj(source,target)
    if not unchanged:temp.replace(path)
    result['pre_header_normalized_sha256']=result.get('pre_header_normalized_sha256',before)
    result['sha256']=frozen_adapter._sha(path)
    result['dataset']=header
    result.update(adapter_code_sha256=codehash,base_adapter_code_sha256=basehash,
                  header_stamp_changed=not unchanged)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifact',required=True);p.add_argument('--out',required=True)
    p.add_argument('--provenance',required=True);p.add_argument('--raw-markets',required=True)
    p.add_argument('--latency-projection',action='store_true')
    a=p.parse_args()
    print(json.dumps(convert(a.artifact,a.out,provenance=a.provenance,raw_markets=a.raw_markets,
                            latency_projection=a.latency_projection),indent=2))


if __name__=='__main__':main()
