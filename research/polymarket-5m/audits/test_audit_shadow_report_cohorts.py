"""Counterexamples for silent membership drift and truncated report evidence."""
from copy import deepcopy
import pytest
import audit_shadow_report_cohorts as code

def pair(before,after):
    return {k:{'content':text,'blob_sha':code.git_blob_sha(text)}
        for k,text in [('before',before),('after',after)]}
def fixture():
    old='| 合计 | 415 |x|\n| GitHub 10000000001 | 405 |x|\n| GitHub 10000000002 | 10 |x|\n'
    new='| 合计 | 1037 |x|\n| GitHub 10000000002 | 10 |x|\n| GitHub 10000000003 | 559 |x|\n| GitHub 10000000004 | 468 |x|\n'
    before='2 笔：\n```\n10000000001 1.79e9 Up .4\n10000000002 1.79e9 Down .6\n```\n'
    after='2 笔：\n```\n10000000003 1.79e9 Up .4\n10000000004 1.79e9 Down .6\n```\n'
    files={code.RECORDER:pair(old,new),code.DIAGNOSE:pair(before,after)}
    for letter,name in code.STOP_FILES.items():
        stop=f'检验 {letter}（前 100 笔）：EV -1c → 没通过（录制段：10000000001, 10000000003）（已判定，不再重算）\n'
        files[name]=pair(stop,stop)
    return {'base':'a','head':'b','files':files},{'sha':'b','parents':[{'sha':'a'}],'files':[]}

def test_rising_total_does_not_hide_historical_sample_replacement():
    bundle,commit=fixture();out=code.audit(bundle,commit,['10000000004'])
    rec=out['recorder_cohort']
    assert rec['aggregate_delta']==622 and rec['aggregate_delta_explained']
    assert rec['removed']=={'10000000001':405}
    assert rec['added']=={'10000000003':559,'10000000004':468}
    assert out['historical_membership_changed']
    assert out['changed_run_provenance']['10000000003']['already_in_prior_frozen_stop_cohort']
    assert out['changed_run_provenance']['10000000004']['already_consumed_development_capture']
    assert out['all_frozen_verdict_lines_unchanged']
    assert out['financial_result'] is None and not out['new_independent_oos_evidence']

def test_changed_frozen_verdict_is_reported_instead_of_overwriting_history():
    bundle,commit=fixture();name=code.STOP_FILES['C']
    altered=bundle['files'][name]['after']['content'].replace('-1c','+9c')
    bundle['files'][name]['after']={'content':altered,'blob_sha':code.git_blob_sha(altered)}
    out=code.audit(bundle,commit)
    assert out['all_frozen_verdict_lines_unchanged'] is False
    assert out['frozen_verdicts']['C']['unchanged'] is False
    assert '-1c' in out['frozen_verdicts']['C']['before']

def test_truncated_provider_content_cannot_keep_its_old_blob_identity():
    bundle,commit=fixture();bundle['files'][code.RECORDER]['after']['content']='truncated'
    with pytest.raises(ValueError,match='blob hash mismatch'):code.audit(bundle,commit)

def test_incomplete_hunk_cannot_establish_a_complete_changed_report():
    bundle,commit=fixture();commit['files']=[{'filename':'test.md','patch':'@@ -1,2 +1,2 @@\n-a\n+b','additions':1,'deletions':1}]
    with pytest.raises(ValueError,match='incomplete'):code.audit(bundle,commit)

def test_printed_diagnostic_count_must_match_rows():
    bundle,commit=fixture();changed=bundle['files'][code.DIAGNOSE]['after']['content'].replace('2 笔','3 笔')
    bundle['files'][code.DIAGNOSE]['after']={'content':changed,'blob_sha':code.git_blob_sha(changed)}
    with pytest.raises(ValueError,match='count header'):code.audit(bundle,commit)
