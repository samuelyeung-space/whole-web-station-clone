"""Explicitly revalidate historical development tapes under existing receipt rules.

This is not a repair of venue timestamps, new OOS, or a frozen protocol update.
The published manifest is kept byte-for-byte. Only nonpositive/nonfinite trade
sentinels are removed, with every removed row retained in the audit. All receive
sequences, epoch transitions, mappings, labels, values, and counts are validated.
The obsolete all-token venue-clock gate is retired only in a derived manifest
after the existing receipt-clock validator passes. Per-token stale messages
remain in the tape and the existing replay ignores them when building a book.
"""
import argparse,collections,copy,csv,gzip,hashlib,io,json,math,sys
from contextlib import contextmanager
from pathlib import Path

REPO=Path(__file__).resolve().parent
sys.path.insert(0,str(REPO))
import h_replay_archive as archive

def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


FILES = ('manifest.json', 'source_events.jsonl.gz', 'clob_events.jsonl.gz',
         'market_registry.csv.gz', 'market_outcomes.csv.gz')


def audit_manifest_contract(manifest):
    """Require honest original gate summaries; never erase unrelated failures."""
    if manifest.get('schema') != 'polymarket-5m-strict-replay-v2':
        raise ValueError('unsupported strict archive schema')
    strict = manifest.get('completeness_checks')
    race = manifest.get('receipt_race_checks')
    for name, checks in (('completeness_checks', strict), ('receipt_race_checks', race)):
        if not isinstance(checks, dict) or not checks or any(type(v) is not bool for v in checks.values()):
            raise ValueError(name + ' must contain explicit boolean gates')
    strict_fail = sorted(k for k,v in strict.items() if not v)
    race_fail = sorted(k for k,v in race.items() if not v)
    if (sorted(manifest.get('failure_reasons', [])) != strict_fail
            or manifest.get('complete') is not (not strict_fail)):
        raise ValueError('original strict checks and completeness summary disagree')
    if (sorted(manifest.get('receipt_race_failure_reasons', [])) != race_fail
            or manifest.get('receipt_race_ready') is not (not race_fail)
            or race.get('strict_complete') is not manifest['complete']):
        raise ValueError('original receipt-race checks and summary disagree')
    if strict_fail != ['clob_source_time']:
        raise ValueError('unexpected original gate failure; manual investigation required')
    for k in ('started_ms', 'ended_ms'):
        if isinstance(manifest.get(k), bool) or not isinstance(manifest.get(k), (float,int)) or not math.isfinite(manifest[k]):
            raise ValueError('invalid recording interval')
    if manifest['started_ms'] >= manifest['ended_ms']:
        raise ValueError('invalid recording interval')
    return {'original_strict_failures':strict_fail, 'original_race_failures':race_fail,
            'unrelated_race_failures_retained':[k for k in race_fail if k != 'strict_complete']}


def derive_manifest_metadata(original, validated_counts):
    """Use only after streaming validation; retire only the obsolete global gate."""
    contract = audit_manifest_contract(original)
    if any(not isinstance(k, str) or type(v) is not int or v < 0
           for k, v in validated_counts.items()):
        raise ValueError('validated counts must be nonnegative integers')
    result = copy.deepcopy(original)
    result['counts'] = dict(validated_counts)
    checks = result['completeness_checks']
    removed = checks.pop('clob_source_time')
    # Filtering a sentinel must not leave a stale 'has trades' assertion.
    for key in ('spot_connection','spot_trade','spot_disconnect','futures_connection',
                'futures_bbo','futures_disconnect','clob_connection'):
        if key in checks:
            checks[key] = checks[key] and validated_counts.get(key, 0) > 0
    result['failure_reasons'] = sorted(k for k,v in checks.items() if not v)
    result['complete'] = not result['failure_reasons']
    race = result['receipt_race_checks']
    race['strict_complete'] = result['complete']
    for key in ('futures_trade_connection','futures_trade','futures_trade_disconnect',
                'deribit_connection','deribit_quote','deribit_disconnect'):
        if key in race:
            race[key] = race[key] and validated_counts.get(key, 0) > 0
    result['receipt_race_failure_reasons'] = sorted(k for k,v in race.items() if not v)
    result['receipt_race_ready'] = not result['receipt_race_failure_reasons']
    changes = {'schema':'development-manifest-transition-v2',
               'retired_check':{'name':'clob_source_time','original_value':removed},
               **contract, 'derived_strict_failures':result['failure_reasons'],
               'derived_race_failures':result['receipt_race_failure_reasons'],
               'receipt_race_ready_is_derived_not_forced':True,
               'original_unresolved_market_ids_preserved':result.get('unresolved_market_ids',[])}
    return result, changes


def finite_clock(value, name):
    if isinstance(value, bool) or value is None:
        raise ValueError('invalid ' + name)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('invalid ' + name)
    return value


class SourceEnvelope:
    """Check every raw row before filtering, including rejected trade epochs."""
    def __init__(self):
        self.previous = -math.inf
        self.sequences = collections.defaultdict(lambda: -1)
        self.active = dict.fromkeys(('spot', 'futures', 'futures_trade', 'deribit'))
        self.epochs = dict.fromkeys(self.active, 0)

    def observe(self, row, line_number):
        stream, kind = row['stream'], row['kind']
        receipt = finite_clock(row['recv_ms'], 'source receipt clock')
        belongs_to_stream = (kind.startswith(stream + '_')
                             or (stream == 'futures_trade' and kind == 'futures_trade'))
        if (stream not in self.active or not belongs_to_stream
                or row['seq'] != line_number - 1
                or row['stream_sequence'] != self.sequences[stream] + 1
                or receipt < self.previous):
            raise ValueError('source sequence/receive integrity violation')
        epoch = row['connection_epoch']
        if type(epoch) is not int or epoch <= 0:
            raise ValueError('invalid original source connection epoch')
        if kind == stream + '_connection':
            if self.active[stream] is not None or epoch <= self.epochs[stream]:
                raise ValueError('invalid original source open epoch')
            self.active[stream] = self.epochs[stream] = epoch
        elif self.active[stream] != epoch:
            raise ValueError('original source event outside active epoch')
        if 'source_ts_ms' not in row:
            raise ValueError('original source timestamp field absent')
        if kind in ('spot_trade', 'futures_trade'):
            finite_clock(row['source_ts_ms'], 'original trade source timestamp')
        if kind == stream + '_disconnect':
            self.active[stream] = None
        self.sequences[stream] = row['stream_sequence']
        self.previous = receipt

    def finish(self):
        if any(epoch is not None for epoch in self.active.values()):
            raise ValueError('original source EOF has open connection')


def validate_labels_and_mappings(mappings, outcomes, manifest):
    """Validate recorded availability; never move late labels to market close."""
    by_id = {}
    tokens = set()
    for row in mappings:
        mid = row['market_id']
        if not mid or mid in by_id:
            raise ValueError('empty or duplicate market mapping')
        finite_clock(row['recv_ms'], 'market mapping receipt clock')
        finite_clock(row['slot'], 'market slot')
        if row['slot'] % 300:
            raise ValueError('market mapping is not aligned to 5 minutes')
        pair = (row['up_token_id'], row['down_token_id'])
        if not all(pair) or len(set(pair)) != 2 or tokens.intersection(pair):
            raise ValueError('nonunique direct market tokens')
        tokens.update(pair)
        if 'fee_rate' in row:
            fee = finite_clock(row['fee_rate'], 'market fee rate')
            if not 0 <= fee <= 1:
                raise ValueError('invalid market fee rate')
        by_id[mid] = row
    resolved = set()
    for row in outcomes:
        mid = row['market_id']
        if mid not in by_id or mid in resolved or row['resolution_source'] != 'gamma':
            raise ValueError('invalid or duplicate Gamma label mapping/source')
        received = finite_clock(row['recv_ms'], 'label receipt clock')
        if received < (by_id[mid]['slot'] + 300) * 1000:
            raise ValueError('label received before market end')
        if row['source_ts_ms'] is not None:
            finite_clock(row['source_ts_ms'], 'label source clock')
        resolved.add(mid)
    if set(manifest.get('unresolved_market_ids', [])) != set(by_id) - resolved:
        raise ValueError('unresolved market inventory does not reconcile')
    return {'mapped_markets':len(by_id), 'resolved_markets':len(resolved),
            'unresolved_market_ids':sorted(set(by_id) - resolved),
            'label_receipt_clocks_modified':False}


@contextmanager
def deterministic_gzip_writer(path):
    with Path(path).open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0, compresslevel=1) as zipped:
            with io.TextIOWrapper(zipped, encoding='utf-8') as text:
                yield text


def verify_gzip(path):
    """Require every member's CRC and EOF trailer, not merely decodable rows."""
    decompressed_bytes = 0
    with gzip.open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            decompressed_bytes += len(chunk)
    return {'sha256':sha(path), 'compressed_bytes':Path(path).stat().st_size,
            'decompressed_bytes':decompressed_bytes, 'crc_and_trailer_validated':True}


def derive(runroot, output=None):
    runroot=Path(runroot).resolve(); original=runroot/'bundle-btc/strict'
    out=Path(output).resolve() if output else runroot/'bundle-btc/strict_development_revalidated_v2'
    out.mkdir(parents=True, exist_ok=False)
    manifest=json.loads((original/'manifest.json').read_text())
    audit_manifest_contract(manifest)
    original_hashes={n:sha(original/n) for n in FILES}
    counts=collections.Counter();stream_seq=collections.Counter();envelope=SourceEnvelope()
    dropped=[];written=0
    with gzip.open(original/'source_events.jsonl.gz','rt') as fi,deterministic_gzip_writer(out/'source_events.jsonl.gz') as fo:
        for line_no,line in enumerate(fi,1):
            e=json.loads(line);s=e['stream'];envelope.observe(e,line_no)
            counts[e['kind']]+=1
            if e['kind'] in ('spot_trade','futures_trade') and not all(math.isfinite(float(e[k])) and float(e[k])>0 for k in ('price','size')):
                dropped.append({'line_number':line_no,'reason':'nonfinite_or_nonpositive_trade_price_or_size','event':e});continue
            e.setdefault('raw_stream_sequence', e['stream_sequence']);e['seq']=written;e['stream_sequence']=stream_seq[s]
            written+=1;stream_seq[s]+=1;fo.write(json.dumps(e,separators=(',',':'))+'\n')
    envelope.finish()
    for n in ('clob_events.jsonl.gz','market_registry.csv.gz','market_outcomes.csv.gz'):
        target=out/n
        if not target.exists():target.symlink_to(original/n)
    # A fast decompressor may yield all rows while accepting a missing footer.
    # Verify with stdlib gzip before claiming a complete derived artifact.
    gzip_integrity = {n:verify_gzip(out/n) for n in FILES if n.endswith('.gz')}
    actual=collections.Counter();stale=collections.Counter();token_clock={};ticks=collections.Counter()
    for e in archive.iter_normalized_events(out/'source_events.jsonl.gz',family='source'):
        finite_clock(e['recv_ms'], 'source receipt clock')
        actual[e['kind']]+=1
    for e in archive.iter_normalized_events(out/'clob_events.jsonl.gz',family='clob'):
        finite_clock(e['recv_ms'], 'CLOB receipt clock')
        actual[e['kind']]+=1
        if e['kind'] in ('clob_connection','clob_error'):token_clock.clear()
        elif e['kind'] in ('clob_snapshot','clob_price_change'):
            token=e['asset_id'];t=float(e['source_ts_ms']);before=token_clock.get(token,-math.inf)
            if t<before:stale[e['kind']]+=1
            token_clock[token]=max(before,t)
            if e.get('tick_size') is not None:ticks[str(e['tick_size'])]+=1
    mappings=list(archive.iter_market_mappings(out/'market_registry.csv.gz')); outcomes=list(archive.iter_outcomes(out/'market_outcomes.csv.gz'))
    actual.update(markets=len(mappings),outcomes=len(outcomes));expected=dict(manifest['counts'])
    for row in dropped:expected[row['event']['kind']]-=1
    if dict(actual)!=expected:raise ValueError('counts fail reconciliation')
    label_audit=validate_labels_and_mappings(mappings,outcomes,manifest)
    if manifest['missing_resolved_market_ids'] or not manifest['recorder_complete']:raise ValueError('recording or resolved coverage incomplete')
    derived_manifest,manifest_transition=derive_manifest_metadata(manifest,dict(actual))
    audit={'manifest_transition':manifest_transition,'label_and_mapping_audit':label_audit,'gzip_integrity':gzip_integrity,'derivation_implementation_sha256':sha(Path(__file__)),
      'implementation_changes_vs_initial_script':['relative module import','recompute all remaining gates; never force receipt_race_ready','validate gate summary consistency','deterministic gzip','stdlib full gzip CRC/trailer validation','finite receipt clocks','validate every raw source envelope before sentinel filtering','validate labels/mappings and preserve unresolved inventory','refuse output overwrite'],
      'status':'development_only_no_oos','run_id':manifest['run_id'],'original_hashes':original_hashes,
      'frozen_evaluator_unchanged':True,'receipt_validator_sha256':sha(REPO/'h_replay_archive.py'),
      'existing_receipt_contract_commit':'95b631a89171584539d05582245661d9bed34170',
      'transformations':['finite-positive-trade filtering with original rows retained','remove historical clob_source_time whole-recording gate after full existing receipt validator passes'],
      'venue_timestamps_reordered_or_modified':False,'removed_old_gate_value':False,
      'source_counts_before':dict(counts),'dropped_count':len(dropped),'dropped':dropped,
      'validated_counts':dict(actual),'per_token_stale_within_epoch':dict(stale),'snapshot_tick_values':dict(ticks),
      'derived_source_sha256':sha(out/'source_events.jsonl.gz'),
      'limitations':['No raw aggressor side','No true maker queue or own fill receipts','Dynamic tick_size_change is absent from strict input; snapshot tick only','adapter does not emit min_order_size/tick_size; downstream execution constraint enrichment required','Unresolved markets must retain inventory and null terminal pnl']}
    (out/'original_manifest.json').write_bytes((original/'manifest.json').read_bytes())
    (out/'derivation_audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    manifest=derived_manifest
    manifest['provenance']={'status':'explicit_development_revalidation_not_formal_artifact','original_manifest_sha256':original_hashes['manifest.json'],'audit_sha256':sha(out/'derivation_audit.json'),'audit_path':'derivation_audit.json'}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    raw=sorted((runroot/'raw').rglob('markets.jsonl.gz'));merged=out/'raw_markets_merged.jsonl.gz'
    if not raw:raise ValueError('no raw Gamma market receipts; metadata not inferred')
    with merged.open('wb') as fout:
        for p in raw:
            with p.open('rb') as f:
                for chunk in iter(lambda:f.read(1048576),b''):fout.write(chunk)
    raw_gzip_integrity=verify_gzip(merged)
    result={k:v for k,v in audit.items() if k not in ('dropped',)};result.update(artifact=str(out),raw_markets=str(merged),raw_inputs={str(p):sha(p) for p in raw},raw_merged_sha256=sha(merged),raw_gzip_integrity=raw_gzip_integrity)
    if any(sha(original/n)!=h for n,h in original_hashes.items()):raise RuntimeError('original artifact changed during derivation')
    (out/'preparation_summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('runroot');p.add_argument('--out')
    args=p.parse_args();derive(args.runroot,args.out)
