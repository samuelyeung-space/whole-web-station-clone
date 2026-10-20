"""Low-disk, label-blind checkpoint integrity audit without extracting raw files.

All gzip quote/decision streams are opaque CRC scans, not JSON/price analysis.
Only root runtime/health and registration slugs are interpreted as metadata.
Byte validity and registration presence never imply execution/OOS admission.
"""
from pathlib import Path,PurePosixPath
import argparse,gzip,hashlib,json,re,tarfile


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def require(ok,message):
    if not ok:raise ValueError(message)


def audit_archive(path,expected_sha256,expected_bytes):
    from capture_checkpoint import allowed
    path=Path(path)
    require(type(expected_bytes) is int and 0<expected_bytes<=6*1024**3,'explicit bounded archive size required')
    require(isinstance(expected_sha256,str) and re.fullmatch('[a-f0-9]{64}',expected_sha256),'explicit SHA256 required')
    require(path.stat().st_size==expected_bytes and sha(path)==expected_sha256,'archive size/hash mismatch')
    streams=[];metadata={};registered=set()
    with tarfile.open(path,'r:') as tar:
        members=tar.getmembers()
        require(members and members[0].name=='capture-manifest.json' and members[0].size<=4*1024**2,'bounded manifest must be first')
        require(all(m.isfile() for m in members) and len({m.name for m in members})==len(members),'duplicate or nonregular member')
        manifest=json.load(tar.extractfile(members[0]));rows=manifest.get('files')
        require(manifest.get('schema')=='capture-checkpoint-v1' and isinstance(rows,list),'unsupported checkpoint')
        require(len({r['path'] for r in rows})==len(rows),'duplicate manifest paths')
        require({r['path'] for r in rows}=={m.name for m in members[1:]},'member inventory mismatch')
        require(all(allowed(r['path']) and type(r['bytes']) is int and r['bytes']>=0 for r in rows),'unsafe member path/size')
        require(sum(r['bytes'] for r in rows)<=6*1024**3,'excessive checkpoint size')
        lookup={m.name:m for m in members}
        for row in rows:
            member=lookup[row['path']]
            require(member.size==row['bytes'],'member size mismatch')
            with tar.extractfile(member) as source:
                require(hashlib.file_digest(source,'sha256').hexdigest()==row['sha256'],'member hash mismatch: '+row['path'])
            if row['path'] in ('capture-runtime.json','capture-health.json'):
                require(member.size<=4*1024**2,'excessive root metadata')
                metadata[row['path']]=json.load(tar.extractfile(member))
            if not row['path'].endswith('.jsonl.gz'):continue
            count=expanded=0;last=b'';name=PurePosixPath(row['path']).name[:-9]
            require(member.size>=18,'missing gzip header/footer')
            with tar.extractfile(member) as source:
                require(source.read(2)==b'\x1f\x8b','missing gzip header/footer')
            with tar.extractfile(member) as source,gzip.GzipFile(fileobj=source,mode='rb') as decoded:
                if name=='markets':
                    for line in decoded:
                        expanded+=len(line);count+=1;last=line[-1:]
                        event=json.loads(line)
                        slug=event.get('slug','') if isinstance(event,dict) else ''
                        if isinstance(slug,str) and re.fullmatch(r'btc-updown-5m-\d+',slug):registered.add(slug)
                else:
                    for block in iter(lambda:decoded.read(1024**2),b''):
                        expanded+=len(block);count+=block.count(b'\n');last=block[-1:]
            require(not expanded or last==b'\n','unterminated opaque JSONL stream')
            streams.append(dict(path=row['path'],records=count,expanded_bytes=expanded,
                compressed_bytes=member.size,gzip_eof_crc_verified=True,
                semantic_access='registration_slug_metadata_only' if name=='markets' else 'opaque_bytes_only'))
    require(path.stat().st_size==expected_bytes and sha(path)==expected_sha256,'archive changed during audit')
    return dict(schema='capture-archive-opaque-integrity-v1',run_id=manifest.get('run_id'),
        source_commit=manifest.get('source_commit'),archive_bytes=expected_bytes,archive_sha256=expected_sha256,
        all_member_hashes_verified=True,checkpoint_file_count=len(rows),all_gzip_eof_crc_verified=True,
        gzip_streams=streams,btc5m_registered_markets=len(registered),root_metadata=metadata,
        raw_files_extracted=False,prices_or_outcomes_semantically_read=False,
        execution_input_admitted=False,full_recorded_utc_days=None,new_financial_replays=0,
        seven_day_result=None,formal_oos_result=None,durable_saved=False,
        limits=['Registration presence is not per-market book/outcome coverage',
                'Opaque CRC does not validate quote JSON semantics or feed health',
                'Valid bytes are not observed private fills or OOS evidence'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--bytes',required=True,type=int);p.add_argument('--out',required=True)
    a=p.parse_args();out=Path(a.out)
    if out.exists():raise ValueError('refusing to overwrite prior audit')
    result=audit_archive(a.archive,a.sha256,a.bytes)
    with out.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({k:result[k] for k in ('run_id','checkpoint_file_count','btc5m_registered_markets','all_gzip_eof_crc_verified','new_financial_replays')}))


if __name__=='__main__':main()
