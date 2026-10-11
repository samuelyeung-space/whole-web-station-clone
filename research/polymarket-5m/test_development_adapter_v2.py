import gzip,json
import pytest
import development_adapter_v2 as dev
import mine500_adapter as old

def row(t,config=None,**kw):
    r={'_recorded_at_ms':t,'slug':'btc-updown-5m-1000','conditionId':'m',
       'outcomes':['Up','Down'],'clobTokenIds':['u','d'],
       'feeSchedule':{'rate':.07,'exponent':1,'takerOnly':True},
       'resolutionSource':'chainlink-60s','cryptoMarketConfig':config}
    r.update(kw);return r

def tape(tmp_path,rows):
    p=tmp_path/'markets.jsonl.gz'
    with gzip.open(p,'wt') as f:
        for r in rows:f.write(json.dumps(r)+'\n')
    return p

@pytest.mark.parametrize('reverse',[False,True])
def test_later_optional_metadata_cannot_backfill_first_receipt(tmp_path,reverse):
    rows=[row(1),row(2,{'twapLookbackSeconds':60})]
    if reverse:rows.reverse()
    got=dev.first_raw_metadata(tape(tmp_path,rows))['m']
    assert got['recv_ms']==1 and got['crypto_market_config'] is None

def test_missing_later_row_cannot_erase_first_explicit_metadata(tmp_path):
    got=dev.first_raw_metadata(tape(tmp_path,[row(1,{'twapLookbackSeconds':60}),row(2)]))['m']
    assert got['crypto_market_config']=={'twapLookbackSeconds':60}

def test_conflicting_explicit_definitions_fail(tmp_path):
    with pytest.raises(ValueError,match='conflicting explicit'):
        dev.first_raw_metadata(tape(tmp_path,[row(1,{'twapLookbackSeconds':60}),row(2,{'twapLookbackSeconds':30})]))

@pytest.mark.parametrize('change',[{'clobTokenIds':['u2','d']},{'slug':'btc-updown-5m-2000'},
    {'feeSchedule':{'rate':.08,'exponent':1,'takerOnly':True}},{'resolutionSource':'other'}])
def test_mapping_fee_and_source_drift_still_fail(tmp_path,change):
    with pytest.raises(ValueError,match='definition or fee changed'):
        dev.first_raw_metadata(tape(tmp_path,[row(1),row(2,**change)]))

def test_scoped_reader_restores_frozen_reader_after_failure():
    original=old.first_raw_metadata
    with pytest.raises(RuntimeError):
        with dev._scoped_metadata_reader():
            assert old.first_raw_metadata is dev.first_raw_metadata
            raise RuntimeError('conversion failed')
    assert old.first_raw_metadata is original

def test_header_stamp_preserves_all_causal_events_and_is_idempotent(tmp_path):
    p=tmp_path/'normalized.jsonl.gz'
    events='{"type":"market","recv_ms":1}\n{"type":"decision","recv_ms":500,"features":{}}\n'
    with gzip.open(p,'wt') as f:
        f.write('{"type":"dataset","split":"development","recv_ms":0}\n'+events)
    result=dev.stamp_header({'output':str(p)})
    with gzip.open(p,'rt') as f:
        header=json.loads(f.readline())
        assert f.read()==events
    assert header['recv_ms']==0
    assert header['development_adapter_code_sha256']==old._sha(dev.Path(dev.__file__))
    first_hash=result['sha256']
    assert dev.stamp_header(result)['sha256']==first_hash

def test_header_stamp_refuses_holdout(tmp_path):
    p=tmp_path/'normalized.jsonl.gz'
    with gzip.open(p,'wt') as f:f.write('{"type":"dataset","split":"holdout"}\n')
    with pytest.raises(ValueError,match='restricted to development'):
        dev.stamp_header({'output':str(p)})
