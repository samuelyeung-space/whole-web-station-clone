"""Synthetic integrity tests; none are market profitability evidence."""
import copy
import gzip
import json

import pytest

import mine500_development_archive as dev


def manifest():
    return {
        'schema':'polymarket-5m-strict-replay-v2',
        'started_ms':1, 'ended_ms':1000, 'complete':False,
        'completeness_checks':{'clob_source_time':False, 'spot_trade':True},
        'failure_reasons':['clob_source_time'],
        'receipt_race_checks':{'strict_complete':False, 'futures_trade':True},
        'receipt_race_failure_reasons':['strict_complete'],
        'receipt_race_ready':False, 'unresolved_market_ids':['unresolved'],
        'counts':{'spot_trade':2, 'futures_trade':1},
    }


def test_only_obsolete_gate_removed_and_original_is_unchanged():
    original = manifest()
    before = copy.deepcopy(original)
    result, audit = dev.derive_manifest_metadata(original, original['counts'])
    assert original == before
    assert result['complete'] and result['receipt_race_ready']
    assert result['unresolved_market_ids'] == ['unresolved']
    assert audit['original_race_failures'] == ['strict_complete']
    assert audit['derived_race_failures'] == []


def test_unrelated_receipt_race_failure_is_not_erased():
    original = manifest()
    original['receipt_race_checks']['deribit_race_width'] = False
    original['receipt_race_failure_reasons'].append('deribit_race_width')
    result, audit = dev.derive_manifest_metadata(original, original['counts'])
    assert result['complete'] is True
    assert result['receipt_race_ready'] is False
    assert result['receipt_race_failure_reasons'] == ['deribit_race_width']
    assert audit['unrelated_race_failures_retained'] == ['deribit_race_width']


@pytest.mark.parametrize('field,value', [
    ('complete', True), ('failure_reasons', []),
    ('receipt_race_ready', True), ('receipt_race_failure_reasons', []),
])
def test_dishonest_original_gate_summary_rejected(field, value):
    original = manifest()
    original[field] = value
    with pytest.raises(ValueError, match='summary|completeness'):
        dev.audit_manifest_contract(original)


def test_cannot_retire_another_strict_failure():
    original = manifest()
    original['completeness_checks']['source_stream_integrity'] = False
    original['failure_reasons'].append('source_stream_integrity')
    with pytest.raises(ValueError, match='unexpected original gate'):
        dev.audit_manifest_contract(original)


@pytest.mark.parametrize('count,strict,race', [
    ({'spot_trade':0,'futures_trade':1},False,False),
    ({'spot_trade':1,'futures_trade':0},True,False),
])
def test_filtering_last_usable_observation_cannot_leave_presence_true(count, strict, race):
    result, _ = dev.derive_manifest_metadata(manifest(), count)
    assert result['complete'] is strict
    assert result['receipt_race_ready'] is race


@pytest.mark.parametrize('count', [{'spot_trade':-1}, {'spot_trade':True}, {'spot_trade':1.5}])
def test_invalid_counts_rejected(count):
    with pytest.raises(ValueError, match='nonnegative integers'):
        dev.derive_manifest_metadata(manifest(), count)


def test_gzip_is_deterministic_independent_of_path(tmp_path):
    outputs = [tmp_path/'a.gz',tmp_path/'b.gz']
    for path in outputs:
        with dev.deterministic_gzip_writer(path) as f:
            f.write('{"recv_ms":1}\n')
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert gzip.decompress(outputs[0].read_bytes()) == b'{"recv_ms":1}\n'


@pytest.mark.parametrize('defect',['truncate_trailer','corrupt_crc','truncate_member'])
def test_gzip_integrity_rejects_incomplete_decodable_stream(tmp_path, defect):
    path=tmp_path/'broken.gz'
    content=gzip.compress(b'{"kind":"spot_trade"}\n',mtime=0)
    if defect=='truncate_trailer':
        content=content[:-8]
    elif defect=='corrupt_crc':
        content=content[:-8]+bytes([content[-8]^1])+content[-7:]
    else:
        content+=gzip.compress(b'second member\n',mtime=0)[:-3]
    path.write_bytes(content)
    with pytest.raises((EOFError,gzip.BadGzipFile)):
        dev.verify_gzip(path)


def test_gzip_integrity_supports_multiple_complete_members(tmp_path):
    path=tmp_path/'members.gz'
    path.write_bytes(gzip.compress(b'a\n',mtime=0)+gzip.compress(b'b\n',mtime=0))
    result=dev.verify_gzip(path)
    assert result['crc_and_trailer_validated']
    assert result['decompressed_bytes']==4


def event(kind, seq, epoch=1, **kwargs):
    return dict(kind=kind, seq=seq, recv_ms=seq, stream='spot',
                stream_sequence=seq, connection_epoch=epoch, source_ts_ms=seq, **kwargs)


def test_invalid_trade_cannot_hide_epoch_failure_by_being_filtered():
    checker = dev.SourceEnvelope()
    checker.observe(event('spot_connection', 0), 1)
    with pytest.raises(ValueError, match='outside active epoch'):
        checker.observe(event('spot_trade', 1, epoch=2, price=0, size=1), 2)


@pytest.mark.parametrize('bad', [float('nan'),float('inf'),True,None])
def test_invalid_trade_cannot_hide_receipt_clock_failure(bad):
    checker = dev.SourceEnvelope()
    checker.observe(event('spot_connection',0),1)
    row=event('spot_trade',1,price=0,size=1)
    row['recv_ms']=bad
    with pytest.raises(ValueError,match='source receipt clock'):
        checker.observe(row,2)


def test_source_envelope_closed_and_all_original_sequences_observed():
    checker=dev.SourceEnvelope()
    checker.observe(event('spot_connection',0),1)
    checker.observe(event('spot_trade',1,price=0,size=1),2)
    checker.observe(event('spot_trade',2,price=100,size=1),3)
    with pytest.raises(ValueError,match='open connection'):
        checker.finish()
    checker.observe(event('spot_disconnect',3),4)
    checker.finish()


def test_futures_trade_kind_matches_same_named_stream():
    checker=dev.SourceEnvelope()
    for seq,kind in enumerate(('futures_trade_connection','futures_trade','futures_trade_disconnect')):
        row=event(kind,seq)
        row['stream']='futures_trade'
        checker.observe(row,seq+1)
    checker.finish()


def mapped():
    return [dict(market_id='resolved',recv_ms=300000,slot=300,
                 up_token_id='u',down_token_id='d',fee_rate=.07),
            dict(market_id='unresolved',recv_ms=600000,slot=600,
                 up_token_id='u2',down_token_id='d2',fee_rate=.07)]


def labels():
    return [dict(market_id='resolved',recv_ms=900000,source_ts_ms=601000,
                 resolution_source='gamma',winner='Up')]


def test_late_label_receipt_preserved_and_unresolved_kept():
    rows=labels()
    before=copy.deepcopy(rows)
    result=dev.validate_labels_and_mappings(mapped(),rows,manifest())
    assert rows == before
    assert result['unresolved_market_ids']==['unresolved']
    assert result['label_receipt_clocks_modified'] is False


def test_missing_unresolved_inventory_rejected():
    original=manifest()
    original['unresolved_market_ids']=[]
    with pytest.raises(ValueError,match='inventory does not reconcile'):
        dev.validate_labels_and_mappings(mapped(),labels(),original)


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),None])
def test_nonfinite_label_clock_rejected(bad):
    rows=labels()
    rows[0]['recv_ms']=bad
    with pytest.raises(ValueError,match='label receipt clock'):
        dev.validate_labels_and_mappings(mapped(),rows,manifest())


def test_label_received_before_market_end_rejected():
    rows=labels()
    rows[0]['recv_ms']=599999
    with pytest.raises(ValueError,match='before market end'):
        dev.validate_labels_and_mappings(mapped(),rows,manifest())


def test_duplicate_conflicting_outcomes_rejected():
    rows=labels()*2
    with pytest.raises(ValueError,match='duplicate Gamma'):
        dev.validate_labels_and_mappings(mapped(),rows,manifest())


def test_tiny_derivation_preserves_original_and_audits_dropped_row(tmp_path):
    root=tmp_path/'run'
    original=root/'bundle-btc/strict'
    original.mkdir(parents=True)
    source=[event('spot_connection',0),event('spot_trade',1,price=0,size=0),
            event('spot_trade',2,price=100,size=1),event('spot_disconnect',3)]
    clob=[dict(kind='clob_connection',seq=0,recv_ms=0,source_ts_ms=None,connection_epoch=1),
          dict(kind='clob_snapshot',seq=1,recv_ms=1,source_ts_ms=1,connection_epoch=1,
               market_id='market',asset_id='up',bids=[{'price':.4,'size':5}],asks=[{'price':.5,'size':5}]),
          dict(kind='clob_error',seq=2,recv_ms=3,source_ts_ms=None,connection_epoch=1)]
    for name,rows in [('source_events.jsonl.gz',source),('clob_events.jsonl.gz',clob)]:
        with dev.deterministic_gzip_writer(original/name) as f:
            for row in rows:
                f.write(json.dumps(row)+'\n')
    with dev.deterministic_gzip_writer(original/'market_registry.csv.gz') as f:
        f.write('market_id,start_ts,up_token_id,down_token_id,updated_at,fee_rate\n')
        f.write('market,0,up,down,0,.07\n')
    with dev.deterministic_gzip_writer(original/'market_outcomes.csv.gz') as f:
        f.write('market_id,winner,resolution_ts,source,recorded_at\n')
        f.write('market,Up,301,gamma,310\n')
    raw=root/'raw'
    raw.mkdir()
    with dev.deterministic_gzip_writer(raw/'markets.jsonl.gz') as f:
        f.write('{}\n')
    m=manifest()
    m.update(run_id='synthetic',started_ms=0,ended_ms=4,recorder_complete=True,
             missing_resolved_market_ids=[],unresolved_market_ids=[],
             receipt_race_checks={'strict_complete':False},
             counts={'spot_connection':1,'spot_trade':2,'spot_disconnect':1,
                     'clob_connection':1,'clob_snapshot':1,'clob_error':1,
                     'markets':1,'outcomes':1})
    original_bytes=json.dumps(m).encode()
    (original/'manifest.json').write_bytes(original_bytes)
    hashes={n:dev.sha(original/n) for n in dev.FILES}
    result=dev.derive(root)
    out=root/'bundle-btc/strict_development_revalidated_v2'
    assert {n:dev.sha(original/n) for n in dev.FILES} == hashes
    assert (out/'original_manifest.json').read_bytes()==original_bytes
    audit=json.loads((out/'derivation_audit.json').read_text())
    assert audit['dropped_count']==1
    assert audit['dropped'][0]['event']==source[1]
    assert result['gzip_integrity']['source_events.jsonl.gz']['crc_and_trailer_validated']
    with gzip.open(out/'source_events.jsonl.gz','rt') as f:
        derived=[json.loads(line) for line in f]
    assert [r['seq'] for r in derived]==[0,1,2]
    assert [r['raw_stream_sequence'] for r in derived]==[0,2,3]
    assert [r['recv_ms'] for r in derived]==[0,2,3]
    assert json.loads((out/'manifest.json').read_text())['receipt_race_ready'] is True
    with pytest.raises(FileExistsError):
        dev.derive(root)
