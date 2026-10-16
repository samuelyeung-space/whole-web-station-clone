"""Immutable receipt-causal replacement of historical order-constraint events.

Corrects Gamma-wide token tick assumptions using the already registered extractor.
Every non-constraint body line is copied byte-for-byte, including fees and labels.
Inputs with unsupported mixed constraint semantics are rejected, not rewritten.
Source tapes, normalizers and frozen protocols are never edited. Development only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import gzip
import hashlib
import heapq
import io
import itertools
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import zipfile

import mine500_constraint_extract as extractor

VERSION = 'receipt-constraint-overlay-v1'
EXTRACTOR_SHA256 = 'c37c9a0c155e379584443dfc509abb441934d2766eef7f5a529a51d071a57e49'
FIELDS = set(extractor.FIELDS)
DROP_FIELDS = FIELDS | {name+suffix for name in FIELDS for suffix in ('_available_ms','_effective_ms','_source')}
META_FIELDS = {'type','market_id','recv_ms','constraints_source','constraint_origin','constraint_seq',
               'constraint_reason','constraint_provenance','constraint_ambiguous_fields'}


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def encode(row):
    return json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n'


def decode(line):
    def reject(value):
        raise ValueError('nonfinite JSON: '+value)
    row=json.loads(line,parse_constant=reject)
    if not isinstance(row,dict):
        raise ValueError('non-object normalized row')
    return row


def inspect(path):
    """Read all bytes through gzip CRC, collect identity and retained-line digest."""
    counts=Counter(); body=hashlib.sha256(); markets={}; previous=-math.inf
    with gzip.open(path,'rb') as f:
        first=f.readline(); header=decode(first)
        if header.get('type')!='dataset' or not first.endswith(b'\n'):
            raise ValueError('first line must be a complete dataset header')
        previous=header.get('recv_ms',-math.inf)
        for line in f:
            if not line.endswith(b'\n'):raise ValueError('unterminated normalized event')
            row=decode(line); now=row.get('recv_ms')
            if type(now) not in (int,float) or not math.isfinite(now) or now<previous:
                raise ValueError('normalized receipt clock missing or regressed')
            previous=now; kind=row.get('type')
            if kind=='dataset':raise ValueError('multiple normalized headers')
            counts[kind]+=1
            if kind=='constraint':_strip_constraints(row)
            else:body.update(line)
            if kind=='market':
                for mid,m in extractor.market_maps([row]).items():
                    old=markets.get(mid)
                    if old and any(old[k]!=m[k] for k in m if k!='recv_ms'):
                        raise ValueError('market identity changed')
                    if old is None or m['recv_ms']<old['recv_ms']:markets[mid]=m
    if not markets:raise ValueError('no markets in normalized input')
    return {'header':header,'counts':dict(counts),'nonconstraint_sha256':body.hexdigest(),
            'markets':markets,'last_recv_ms':previous}


def unpack_constraint_sources(archive, destination):
    """Read ZIP/tar once; retain only source files needed for constraint recovery."""
    destination=Path(destination); selected={}
    def wanted(name):
        return name in ('bundle-btc/strict/manifest.json','bundle-btc/strict/clob_events.jsonl.gz') or bool(
            re.fullmatch(r'raw/\d{4}-\d{2}-\d{2}/(?:markets|clob)\.jsonl\.gz',name))
    with zipfile.ZipFile(archive) as z:
        members=[m for m in z.infolist() if m.filename=='raw.tgz']
        if len(members)!=1:raise ValueError('archive requires exactly one raw.tgz')
        with z.open(members[0]) as outer, tarfile.open(fileobj=outer,mode='r|gz') as tar:
            for member in tar:
                name=member.name.removeprefix('./')
                if not wanted(name):continue
                if not member.isfile() or name in selected or '..' in PurePosixPath(name).parts:
                    raise ValueError('ambiguous or unsafe selected source member')
                path=destination/name;path.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as src,path.open('xb') as dst:shutil.copyfileobj(src,dst,1024*1024)
                selected[name]=path
    for name in ('bundle-btc/strict/manifest.json','bundle-btc/strict/clob_events.jsonl.gz'):
        if name not in selected:raise ValueError('missing native constraint source: '+name)
    return selected


def _strip_constraints(row):
    """Only pure order-constraint rows are supported by the unchanged engine."""
    extra=set(row)-DROP_FIELDS-META_FIELDS
    if extra:
        raise ValueError("unsupported mixed constraint semantics: "+",".join(sorted(extra)))
    return None


def write_overlay(source, target, header, constraints):
    """Merge constraints before decisions, copying other body lines verbatim."""
    count=Counter(); retained_digest=hashlib.sha256(); removed=0; retained_constraint=0
    def originals():
        nonlocal removed,retained_constraint
        with gzip.open(source,'rb') as src:
            src.readline()
            for line in src:
                row=decode(line)
                if row.get('type')=='constraint':
                    removed+=1; row=_strip_constraints(row)
                    if row is None:continue
                    line=encode(row);retained_constraint+=1
                else:retained_digest.update(line)
                yield row,line
    # Preserve original ordering of all non-constraint events at equal times.
    originals_iter=iter(originals()); pending=next(originals_iter,None); ci=0
    with Path(target).open('xb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0,compresslevel=1) as dst:
        dst.write(encode(header))
        while pending is not None or ci<len(constraints):
            now=min(pending[0]['recv_ms'] if pending else math.inf,
                    constraints[ci]['recv_ms'] if ci<len(constraints) else math.inf)
            group=[]
            while pending is not None and pending[0]['recv_ms']==now:
                group.append(pending);pending=next(originals_iter,None)
            added=[]
            while ci<len(constraints) and constraints[ci]['recv_ms']==now:
                added.append((constraints[ci],encode(constraints[ci])));ci+=1
            # Native tape must already put market identity before same-time dependent events.
            late_market=False;seen_other=False
            for row,_ in group:
                if row['type']=='market' and seen_other:late_market=True
                if row['type']!='market':seen_other=True
            if late_market:raise ValueError('same-time market appears after dependent event')
            for row,line in itertools.chain((x for x in group if x[0]['type']=='market'),added,
                                           (x for x in group if x[0]['type']!='market')):
                dst.write(line);count[row['type']]+=1
    return {'counts':dict(count),'nonconstraint_sha256':retained_digest.hexdigest(),
            'removed_original_constraint_events':removed,'retained_mixed_constraint_events':retained_constraint}


def correct(normalized, archive, out_root):
    normalized,archive,out_root=map(lambda p:Path(p).resolve(),(normalized,archive,out_root))
    if '.pending.' in str(normalized):raise ValueError('refuse an in-progress source tape')
    code_hash=sha(__file__); extractor_hash=sha(extractor.__file__)
    if extractor_hash!=EXTRACTOR_SHA256:raise ValueError('registered extractor code identity differs')
    source_hash=sha(normalized);archive_hash=sha(archive);before=inspect(normalized)
    producer_path=normalized.parent/'manifest.json'
    if not producer_path.is_file():raise ValueError('completed producer manifest required')
    producer=json.loads(producer_path.read_text())
    expected_tape=producer.get('normalized_sha256',producer.get('normalization',{}).get('sha256'))
    if expected_tape!=source_hash:raise ValueError('completed producer tape hash mismatch')
    lineage=producer.get('source_identity',producer.get('identity',{}))
    expected_archive=lineage.get('inputs',{}).get('archive_sha256')
    expected_clob=lineage.get('audit_inputs',{}).get('clob_events.jsonl.gz')
    if expected_archive is None and expected_clob is None:
        raise ValueError('completed producer lacks archive or audited native CLOB identity')
    if expected_archive is not None and expected_archive!=archive_hash:
        raise ValueError('archive differs from completed producer identity')
    producer_hash=sha(producer_path)
    if before['header'].get('split')!='development':raise ValueError('overlay is development-only')
    if before['header'].get('constraint_overlay'):raise ValueError('source already has constraint overlay')
    identity={'schema':VERSION,'source_normalized_sha256':source_hash,'archive_sha256':archive_hash,
              'wrapper_sha256':code_hash,'extractor_sha256':extractor_hash,
              'producer_manifest_sha256':producer_hash}
    key=hashlib.sha256(encode(identity)).hexdigest();out_root.mkdir(parents=True,exist_ok=True);dest=out_root/key
    if dest.exists():
        manifest=json.loads((dest/'manifest.json').read_text()); tape=dest/'normalized.jsonl.gz'
        if manifest['identity']!=identity or sha(tape)!=manifest['normalized_sha256']:
            raise ValueError('completed overlay integrity mismatch')
        check=inspect(tape)
        if check['counts']!=manifest['normalization']['counts'] or check['nonconstraint_sha256']!=before['nonconstraint_sha256']:
            raise ValueError('completed overlay body mismatch')
        return {**manifest,'cache_hit':True,'normalized_path':str(tape)}
    stage=Path(tempfile.mkdtemp(prefix=key+'.pending.',dir=out_root))
    try:
        selected=unpack_constraint_sources(archive,stage/'sources')
        source_hashes={name:sha(path) for name,path in selected.items()}
        if expected_clob is not None and source_hashes['bundle-btc/strict/clob_events.jsonl.gz']!=expected_clob:
            raise ValueError('native CLOB differs from completed producer audit')
        provenance=before['header'].get('provenance',{})
        if source_hashes['bundle-btc/strict/manifest.json']!=provenance.get('parent_manifest_sha256'):
            raise ValueError('archive manifest does not match normalized parent')
        native_manifest=json.loads(selected['bundle-btc/strict/manifest.json'].read_text())
        run_id=str(native_manifest['run_id'])
        if before['header'].get('dataset_id')!='historical-spot-development:'+run_id:
            raise ValueError('archive and normalized recording identity mismatch')
        gamma_names=sorted(name for name in selected if name.endswith('/markets.jsonl.gz'))
        expected=provenance.get('raw_metadata_sha256',{})
        if not gamma_names or {name:source_hashes[name] for name in gamma_names}!=expected:
            raise ValueError('native Gamma receipts do not match normalized input')
        gamma=itertools.chain.from_iterable(extractor.read_jsonl(selected[n]) for n in gamma_names)
        clob=extractor.read_jsonl(selected['bundle-btc/strict/clob_events.jsonl.gz'])
        raw_names=sorted(n for n in selected if n.startswith('raw/') and n.endswith('/clob.jsonl.gz'))
        raw_streams=[extractor.read_jsonl(selected[n]) for n in raw_names]
        raw=heapq.merge(*raw_streams,key=lambda r:r['recv_ms']) if raw_streams else None
        constraints,audit=extractor.extract_constraints(before['markets'],gamma,clob,raw)
        if not constraints:raise ValueError('constraint extractor returned no evidence')
        if constraints[0]['recv_ms']<before['header']['recv_ms']:
            raise ValueError('constraint event before dataset header')
        amendment={'schema':VERSION,'identity':identity,'run_id':run_id,
                   'source_members_sha256':source_hashes,'nonconstraint_body_unchanged_sha256':before['nonconstraint_sha256'],
                   'replacement_policy':'Replace market-wide Gamma ticks with receipt-causal direct-token CLOB observations; reconnect invalidates ticks; preserve missing values.',
                   'scope':'development schema/code correction; no frozen OOS inheritance'}
        header=copy.deepcopy(before['header']);header['constraint_overlay']=amendment
        header.setdefault('limitations',[]).extend([audit['tick_coverage_limitation'],audit['same_time_merge_assumption']])
        (stage/'constraints.jsonl').write_bytes(b''.join(encode(c) for c in constraints))
        output=stage/'normalized.jsonl.gz';written=write_overlay(normalized,output,header,constraints);check=inspect(output)
        if written['nonconstraint_sha256']!=before['nonconstraint_sha256'] or check['nonconstraint_sha256']!=before['nonconstraint_sha256']:
            raise ValueError('non-constraint events changed during overlay')
        if check['counts']!=written['counts'] or sha(normalized)!=source_hash or sha(archive)!=archive_hash or sha(producer_path)!=producer_hash:
            raise ValueError('source/output integrity changed during overlay')
        report={'identity':identity,'status':'complete_constraint_corrected_development','run_id':run_id,
                'normalization':{'sha256':sha(output),'counts':check['counts'],'header':header},
                'normalized_sha256':sha(output),'original_counts':before['counts'],'copy_verification':written,
                'constraint_extraction_audit':audit,'constraint_events_sha256':sha(stage/'constraints.jsonl'),
                'new_backtests':0,'formal_oos_result':None,'source_normalized':str(normalized),'source_archive':str(archive)}
        shutil.rmtree(stage/'sources')
        (stage/'manifest.json').write_bytes(encode(report));stage.rename(dest)
        return {**report,'cache_hit':False,'normalized_path':str(dest/'normalized.jsonl.gz')}
    except BaseException as error:
        (stage/'failed.json').write_bytes(encode({'status':'failed','error':str(error),'identity':identity}))
        # Raw sources are reproducible; keep diagnostics and partial output only.
        shutil.rmtree(stage/'sources',ignore_errors=True)
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('normalized','archive','out-root'):p.add_argument('--'+name,required=True)
    a=p.parse_args();r=correct(a.normalized,a.archive,a.out_root)
    print(json.dumps({'status':r['status'],'run_id':r['run_id'],'normalized_path':r['normalized_path'],
                      'counts':r['normalization']['counts'],'cache_hit':r['cache_hit']},indent=2))
