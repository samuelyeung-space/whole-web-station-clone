"""Restore a hash-locked development cache from original archive + saved scores.

No trained-model loading/fitting, no signals or PnL. The original helper source is
verified and imported solely for its receipt-causal execution-input projection.
Frozen headers, score bank, input hashes and equal-clock source order are bound
by a private recipe. Unknown input versions fail rather than silently retrain.
"""
import argparse,gzip,hashlib,heapq,io,json,time,zipfile
from collections import Counter
from pathlib import Path

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def enrich(event,scores,books,header):
    kind=event['type']
    if kind=='dataset':
        expected=dict(event);expected['dataset_id']+=':MFA-v1'
        expected['mfa_registration_sha256']=header['mfa_registration_sha256']
        if expected!=header or event.get('split')!='development':raise ValueError('source header identity mismatch')
        return expected
    if kind=='book':books[event['market_id']]=event
    elif kind=='decision':
        from minimal_feature_research import execution_inputs
        extra={**scores.get((event['market_id'],event['recv_ms']),{}),
               **execution_inputs(event,books.get(event['market_id']))}
        for key,value in extra.items():
            if key in event['features']:raise ValueError('frozen feature collision')
            event['features'][key]=value;event['feature_available_ms'][key]=event['recv_ms']
    return event

def validate_recipe(recipe):
    if recipe.get('schema')!='frozen-mfa-restoration-v1' or recipe.get('role')!='already_consumed_development':
        raise ValueError('explicit frozen development recipe required')
    sources=recipe['sources'];names=[s['member'] for s in sources]
    if not names or len(set(names))!=len(names):raise ValueError('duplicate/empty source members')
    if recipe['target_manifest']['split']!='development':raise ValueError('target split prohibited')
    for s in sources:
        if s['header'].get('split')!='development':raise ValueError('source split prohibited')

def restore(archive,package,recipe_path,out):
    started=time.perf_counter();recipe=json.loads(Path(recipe_path).read_text());validate_recipe(recipe)
    if sha(archive)!=recipe['archive_sha256']:raise ValueError('source archive hash mismatch')
    helper=Path(__file__).with_name('minimal_feature_research.py')
    if sha(helper)!=recipe['execution_projection_code_sha256']:raise ValueError('original projection source changed')
    target=Path(out).resolve()
    if target.exists():raise ValueError('output exists; never overwrite cached evidence')
    with zipfile.ZipFile(package) as pack:
        score_bytes=pack.read(recipe['scores_member'])
    if hashlib.sha256(score_bytes).hexdigest()!=recipe['scores_sha256']:raise ValueError('saved score bank mismatch')
    scores={}
    with gzip.GzipFile(fileobj=io.BytesIO(score_bytes)) as compressed:
        for line in io.TextIOWrapper(compressed):
            row=json.loads(line);key=(row['market_id'],row['recv_ms'])
            if key in scores:raise ValueError('duplicate saved score key')
            scores[key]=row['scores']
    target.mkdir(parents=True);counts=Counter();source_counts=[];digest=hashlib.sha256()
    try:
        with zipfile.ZipFile(archive) as raw:
            # Complete compressed-member preflight before opening any output tape.
            for spec in recipe['sources']:
                with raw.open(spec['member']) as stream:
                    if hashlib.file_digest(stream,'sha256').hexdigest()!=spec['input_sha256']:
                        raise ValueError('source member mismatch '+spec['member'])
            def body(spec):
                event_digest=hashlib.sha256();books={};local=Counter();headers=0;previous=None
                with raw.open(spec['member']) as member,gzip.GzipFile(fileobj=member) as gz:
                    for line in io.TextIOWrapper(gz):
                        event=json.loads(line)
                        if previous is not None and event['recv_ms']<previous:raise ValueError('nonmonotone source')
                        previous=event['recv_ms']
                        if event['type']=='dataset':headers+=1
                        if headers!=1:raise ValueError('one source header required')
                        event=enrich(event,scores,books,spec['header']);local[event['type']]+=1
                        event_digest.update((canonical(event)+'\n').encode())
                        if event['type']!='dataset':yield event
                if event_digest.hexdigest()!=spec['enriched_event_sha256']:raise ValueError('frozen enriched source mismatch')
                if dict(local)!=spec['counts']:raise ValueError('source record counts mismatch')
                source_counts.append({'member':spec['member'],'counts':dict(local),'enriched_event_sha256':event_digest.hexdigest()})
            with (target/'events.jsonl.gz').open('wb') as dest:
                with gzip.GzipFile(fileobj=dest,mode='wb',filename='',mtime=0,compresslevel=1) as gz:
                    def write(event):
                        blob=(canonical(event)+'\n').encode();gz.write(blob);digest.update(blob);counts[event['type']]+=1
                    write(recipe['target_manifest']['dataset_header'])
                    for event in heapq.merge(*(body(s) for s in recipe['sources']),key=lambda e:e['recv_ms']):write(event)
        manifest=recipe['target_manifest'];tape=target/'events.jsonl.gz'
        if digest.hexdigest()!=manifest['tape_event_sha256']:raise ValueError('restored logical tape mismatch')
        if sha(tape)!=manifest['tape_sha256']:raise ValueError('restored compressed tape mismatch (including compressor identity)')
        if sum(counts.values())!=manifest['record_count']:raise ValueError('restored record count mismatch')
        (target/'manifest.json').write_text(canonical(manifest)+'\n')
        receipt={'status':'passed','restored_without_original_mfa_cache':True,'model_fitting':False,
            'signals_evaluated':0,'new_economic_replays':0,'source_archive_sha256':recipe['archive_sha256'],
            'scores_sha256':recipe['scores_sha256'],'source_counts':source_counts,'counts':dict(counts),
            'restored_tape_sha256':manifest['tape_sha256'],'restored_tape_event_sha256':digest.hexdigest(),
            'elapsed_seconds':time.perf_counter()-started,'formal_oos_result':None}
        (target/'restore_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps(receipt),flush=True)
    except Exception as error:
        (target/'failed.json').write_text(json.dumps({'status':'failed_incomplete_restoration','error':str(error),'pnl':None,'formal_oos_result':None},indent=2)+'\n')
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('archive','package','recipe','out'):p.add_argument('--'+name,required=True)
    a=p.parse_args();restore(a.archive,a.package,a.recipe,a.out)
