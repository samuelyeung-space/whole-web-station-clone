"""Read-only freeze/BBO diagnostics; never a strategy replay or admission gate.

Boundary 0/1 quotes are classified, not repaired or declared executable.
Raw scans do not apply token registration, connection/epoch or strategy filters.
"""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def unique_pairs(items):
    out={}
    for k,v in items:
        if k in out:raise ValueError('duplicate JSON key: '+k)
        out[k]=v
    return out


def audit_freeze(root, freeze, expected):
    root=Path(root).resolve();freeze=Path(freeze)
    if not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('trusted freeze digest required')
    data=freeze.read_bytes()
    if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('freeze fingerprint mismatch')
    doc=json.loads(data,object_pairs_hook=unique_pairs)
    deps=doc.get('dependencies_sha256')
    if not isinstance(deps,dict) or not deps:raise ValueError('explicit nonempty dependencies_sha256 required')
    results=[]
    for name,digest in sorted(deps.items()):
        p=PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or '\\' in name or not p.parts:
            raise ValueError('unsafe dependency path')
        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('invalid dependency digest')
        path=root.joinpath(*p.parts)
        if any(x.is_symlink() for x in [path,*path.parents] if x!=root):
            status,actual='symlink_refused',None
        elif not path.is_file():status,actual='missing',None
        else:
            before=path.stat();actual=sha(path);after=path.stat()
            if (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                status='changed_during_read'
            else:status='match' if actual==digest else 'drift'
        results.append(dict(path=name,expected_sha256=digest,actual_sha256=actual,status=status))
    return dict(freeze_sha256=expected,checked=len(results),matches=sum(x['status']=='match' for x in results),
                all_dependencies_match=all(x['status']=='match' for x in results),dependencies=results,
                manifest_schema=doc.get('schema'),formal_eligibility=False)


def bbo_issues(row):
    """Equivalent numeric inequalities to Family34's reported-BBO guard.

    Reasons distinguish conversion errors from values rejected after conversion.
    No synthetic opposite-side quote, clip, epsilon or sentinel replacement.
    """
    issues=[];values={}
    for field in ('best_bid','best_ask'):
        if field not in row:continue
        try:value=float(row[field])
        except (TypeError,ValueError,OverflowError):
            issues.append(field+'_conversion_error');continue
        values[field]=value
        if not math.isfinite(value):issues.append(field+'_nonfinite')
        elif not 0<value<1:
            issues.append(field+'_boundary_zero' if value==0 else field+'_boundary_one' if value==1 else field+'_outside_unit_interval')
    if set(values)=={'best_bid','best_ask'} and values['best_bid']>=values['best_ask']:
        issues.append('locked_or_crossed_reported_bbo')
    return issues


def logical_changes(row,mode):
    if not isinstance(row,dict):raise ValueError('row must be object')
    if mode=='strict':
        kind=row.get('kind')
        if kind=='clob_price_change':yield row
        elif kind=='clob_price_change_batch':
            changes=row.get('changes')
            if not isinstance(changes,list) or not changes:raise ValueError('invalid strict batch')
            for change in changes:
                if not isinstance(change,dict):raise ValueError('invalid strict change')
                yield change
    else:
        events=row.get('msg')
        if isinstance(events,dict):events=[events]
        if not isinstance(events,list):raise ValueError('raw msg must be object/list')
        for event in events:
            if not isinstance(event,dict):raise ValueError('raw event must be object')
            if event.get('event_type')!='price_change':continue
            changes=event.get('price_changes')
            if not isinstance(changes,list):raise ValueError('invalid raw price_changes')
            for change in changes:
                if not isinstance(change,dict):raise ValueError('invalid raw change')
                yield change


def audit_bbo(path,mode='raw',sample_limit=8):
    path=Path(path)
    if mode not in ('raw','strict'):raise ValueError('unsupported input mode')
    before=sha(path);counts=Counter();reasons=Counter();examples=[];error=None
    opener=gzip.open if path.suffix=='.gz' else open
    try:
        with opener(path,'rb') as f:
            for n,line in enumerate(f,1):
                counts['physical_rows']+=1
                if not line.endswith(b'\n'):raise ValueError('unterminated JSONL')
                row=json.loads(line)
                for offset,change in enumerate(logical_changes(row,mode)):
                    counts['logical_price_changes']+=1
                    if 'best_bid' not in change and 'best_ask' not in change:
                        counts['changes_without_reported_bbo']+=1;continue
                    counts['reported_bbo_rows']+=1
                    issues=bbo_issues(change)
                    if not issues:
                        counts['numeric_guard_pass']+=1;continue
                    counts['numeric_guard_reject']+=1;reasons.update(issues)
                    if len(examples)<sample_limit:
                        examples.append(dict(physical_line=n,logical_offset=offset,reasons=issues,
                            frame_sha256=hashlib.sha256(line).hexdigest()))
    except (OSError,EOFError,ValueError,TypeError) as exc:error=type(exc).__name__+': '+str(exc)
    after=sha(path)
    if before!=after:error='input bytes changed during scan'
    return dict(path=str(path),input_sha256=before,mode=mode,counts=dict(counts),reason_counts=dict(reasons),
                sample_hashed_frames=examples,stream_eof_crc_checked=error is None,error=error,
                actual_first_replay_failure_identified=False,
                eligibility_filters_applied=False,
                note='unfiltered BBO-contract diagnostics, not executable opportunities; no quotes/outcomes/PnL emitted')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True);p.add_argument('--freeze',required=True)
    p.add_argument('--expected-freeze-sha256',required=True)
    p.add_argument('--input',action='append',default=[]);p.add_argument('--mode',choices=['raw','strict'],default='raw')
    p.add_argument('--out',required=True);a=p.parse_args();dest=Path(a.out)
    if dest.exists():raise ValueError('refusing overwrite of prior diagnostic')
    freeze=audit_freeze(a.root,a.freeze,a.expected_freeze_sha256)
    inputs=[audit_bbo(x,a.mode) for x in a.input]
    blocked=not freeze['all_dependencies_match'] or any(x['error'] or x['counts'].get('numeric_guard_reject') for x in inputs)
    result=dict(schema='readonly-replay-input-preflight-v1',status='blocked' if blocked else 'diagnostic_checks_passed_not_admitted',
                freeze=freeze,inputs=inputs,new_financial_replays=0,new_strategy_attempts=0,
                pnl=None,formal_oos_result=None,execution_input_admitted=False)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('x') as f:json.dump(result,f,sort_keys=True,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps(dict(status=result['status'],dependency_matches=freeze['matches'],dependency_checked=freeze['checked'],
                         input_count=len(inputs),new_financial_replays=0)))
    return 2 if blocked else 0


if __name__=='__main__':raise SystemExit(main())
