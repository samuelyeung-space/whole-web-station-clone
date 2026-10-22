"""Compare published report cohorts and frozen verdicts without profit replay.

The report is evidence about published text, not an executable fill ledger.
Never infer incremental PnL from rounded changing-cohort means.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from audit_forward_patch import changed_lines

STOP_FILES={letter:f'latency-test-{letter.lower()}.md' for letter in 'CDFGIJ'}
RECORDER='recorder-check.md'
DIAGNOSE='latency-test-d-diagnose.md'

def git_blob_sha(content):
    raw=content.encode('utf-8')
    return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()

def recorder_rows(text):
    rows={};total=None
    for line in text.splitlines():
        m=re.match(r'^\|\s*GitHub (\d{10,12})\s*\|\s*([\d,]+)\s*\|',line)
        if m:
            if m[1] in rows:raise ValueError('duplicate recorder run')
            rows[m[1]]=int(m[2].replace(',',''))
        m=re.match(r'^\|\s*合计\s*\|\s*([\d,]+)\s*\|',line)
        if m:
            if total is not None:raise ValueError('duplicate aggregate row')
            total=int(m[1].replace(',',''))
    if not rows or total is None:raise ValueError('recorder table incomplete')
    return rows,total

def diagnostic_rows(text):
    header=re.search(r'^([\d,]+) 笔：',text,re.MULTILINE)
    if not header:raise ValueError('diagnostic count header absent')
    counts=Counter();in_code=False
    for line in text.splitlines():
        if line.startswith('```'):
            in_code=not in_code;continue
        if in_code:
            m=re.match(r'^\s*(\d{10,12})\s+[\d.eE+-]+\s+(?:Up|Down)\s+',line)
            if m:counts[m[1]]+=1
    if in_code:raise ValueError('unterminated diagnostic table')
    if sum(counts.values())!=int(header[1].replace(',','')):
        raise ValueError('diagnostic rows disagree with count header')
    return dict(counts)

def changes(before,after):
    added={k:after[k] for k in sorted(after.keys()-before.keys())}
    removed={k:before[k] for k in sorted(before.keys()-after.keys())}
    modified={k:{'before':before[k],'after':after[k]} for k in sorted(before.keys()&after.keys()) if before[k]!=after[k]}
    return {'added':added,'removed':removed,'common_modified':modified,
        'row_delta':sum(after.values())-sum(before.values()),
        'changed_runs_explain_delta':sum(added.values())-sum(removed.values())+
            sum(v['after']-v['before'] for v in modified.values())}

def stop_line(text,letter):
    lines=[line for line in text.splitlines() if line.startswith('检验 '+letter+'（前 ') and '已判定，不再重算' in line]
    if len(lines)!=1:raise ValueError('expected one frozen stopping verdict: '+letter)
    return lines[0]

def audit(bundle,commit,consumed_runs=()):
    if bundle['head']!=commit['sha'] or bundle['base']!=commit['parents'][0]['sha']:
        raise ValueError('commit/report identity mismatch')
    required={RECORDER,DIAGNOSE,*STOP_FILES.values()}
    if set(bundle['files'])!=required:raise ValueError('report set incomplete')
    identities={}
    for name,pair in bundle['files'].items():
        identities[name]={}
        for version in ['before','after']:
            item=pair[version];content=item['content'];raw=content.encode()
            if git_blob_sha(content)!=item['blob_sha']:
                raise ValueError('report blob hash mismatch: '+name+'/'+version)
            identities[name][version]={'git_blob_sha':item['blob_sha'],'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    patches={}
    for item in commit['files']:
        if not item['filename'].endswith('.md'):continue
        if item['patch'] is None:raise ValueError('changed text patch absent')
        added,removed=changed_lines(item['patch'])
        if len(added)!=item['additions'] or len(removed)!=item['deletions']:
            raise ValueError('patch line count mismatch')
        patches[item['filename']]={'added_lines':len(added),'removed_lines':len(removed),'complete_hunks':True}
    def content(name,version):return bundle['files'][name][version]['content']
    verdicts={};known_stopped_runs=set()
    for letter,name in STOP_FILES.items():
        before=stop_line(content(name,'before'),letter);after=stop_line(content(name,'after'),letter)
        known_stopped_runs.update(re.findall(r'\b\d{10,12}\b',before))
        verdicts[letter]={'unchanged':before==after,'before':before,'after':after,
            'before_sha256':hashlib.sha256(before.encode()).hexdigest(),
            'after_sha256':hashlib.sha256(after.encode()).hexdigest()}
    old_rec,old_total=recorder_rows(content(RECORDER,'before'))
    new_rec,new_total=recorder_rows(content(RECORDER,'after'))
    rec=changes(old_rec,new_rec)
    rec.update(before_total=old_total,after_total=new_total,aggregate_delta=new_total-old_total,
        aggregate_delta_explained=new_total-old_total==rec['changed_runs_explain_delta'])
    diag=changes(diagnostic_rows(content(DIAGNOSE,'before')),diagnostic_rows(content(DIAGNOSE,'after')))
    diag['printed_rows_are_unique_trade_identities']=False
    consumed=set(map(str,consumed_runs))
    attribution={run:{'already_in_prior_frozen_stop_cohort':run in known_stopped_runs,
        'already_consumed_development_capture':run in consumed}
        for run in sorted(set(rec['added'])|set(rec['removed'])|set(diag['added'])|set(diag['removed']))}
    return {'schema':'published-shadow-report-cohort-audit-v1','base':bundle['base'],'head':bundle['head'],
        'verified_report_identities':identities,'complete_changed_text_hunks':patches,
        'recorder_cohort':rec,'diagnostic_cohort':diag,'changed_run_provenance':attribution,
        'frozen_verdicts':verdicts,'all_frozen_verdict_lines_unchanged':all(v['unchanged'] for v in verdicts.values()),
        'changed_binary_files':[{'filename':f['filename'],'git_blob_sha':f.get('sha'),'bytes_independently_decoded':False}
            for f in commit['files'] if not f['filename'].endswith('.md')],
        'historical_membership_changed':bool(rec['removed'] or any(run in known_stopped_runs for run in rec['added'])),
        'cohort_change_cause_verified':False,'incremental_pnl_identifiable_from_rounded_report_means':False,
        'new_raw_capture_or_fill_ledger_validated':False,'new_actual_live_fills_confirmed':0,
        'new_financial_replays':0,'new_independent_oos_evidence':False,
        'financial_result':None,'rank7_pnl_usd':None,'formal_oos_result':None,
        'qualified_independent_mechanisms':0}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('report_inputs');p.add_argument('commit_patch')
    p.add_argument('--consumed-run',action='append',default=[])
    a=p.parse_args()
    print(json.dumps(audit(json.loads(Path(a.report_inputs).read_text()),
        json.loads(Path(a.commit_patch).read_text()),a.consumed_run),indent=2,ensure_ascii=False,allow_nan=False))
