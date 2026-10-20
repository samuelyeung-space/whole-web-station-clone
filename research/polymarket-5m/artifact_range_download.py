"""Read an already-authorized prepared artifact URL with strict HTTP ranges.

Transfer JSON arrives on stdin, never command arguments/logs. This does not
obtain credentials or bypass authorization. Failed partial files remain .pending.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import urllib.request


def download(transfer, target, *, block_bytes=8*1024**2, workers=4, opener=urllib.request.urlopen):
    size=transfer['expected_bytes'];digest=transfer['expected_sha256'];url=transfer['download_url']
    if type(size) is not int or not 0<size<=512*1024**2:
        raise ValueError('expected artifact size outside connector bound')
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
        raise ValueError('explicit expected SHA256 required')
    if type(block_bytes) is not int or block_bytes<=0 or type(workers) is not int or not 1<=workers<=8:
        raise ValueError('invalid range configuration')
    target=Path(target);pending=target.with_name(target.name+'.pending')
    if target.exists() or pending.exists():raise FileExistsError('refuse to overwrite artifact or partial')
    target.parent.mkdir(parents=True,exist_ok=True)
    with pending.open('xb') as f:f.truncate(size)
    def fetch(lo):
        hi=min(size-1,lo+block_bytes-1);expected=hi-lo+1
        req=urllib.request.Request(url,headers={'Range':f'bytes={lo}-{hi}','Accept-Encoding':'identity'})
        with opener(req,timeout=60) as res,pending.open('r+b') as out:
            if res.status!=206 or res.headers.get('Content-Range')!=f'bytes {lo}-{hi}/{size}':
                raise ValueError('server did not return the exact authorized byte range')
            length=res.headers.get('Content-Length')
            if length is not None and int(length)!=expected:raise ValueError('range length header mismatch')
            out.seek(lo);received=0;h=hashlib.sha256()
            while received<expected:
                chunk=res.read(min(1024**2,expected-received))
                if not chunk:raise ValueError('truncated byte range')
                out.write(chunk);h.update(chunk);received+=len(chunk)
            if res.read(1):raise ValueError('range body exceeds requested bytes')
        return dict(start=lo,end=hi,bytes=received,sha256=h.hexdigest())
    with ThreadPoolExecutor(max_workers=workers) as pool:
        ranges=list(pool.map(fetch,range(0,size,block_bytes)))
    with pending.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
    if actual!=digest:raise ValueError('assembled artifact SHA256 mismatch')
    pending.replace(target)
    return dict(schema='authorized-artifact-range-download-v1',bytes=size,sha256=actual,
                sha256_verified=True,range_count=len(ranges),ranges=ranges)


def main():
    import sys
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--receipt',required=True,type=Path);args=p.parse_args()
    try:receipt=download(json.load(sys.stdin),args.out)
    except Exception as error:
        # HTTP exception text can contain the signed URL. Expose type/code only.
        print(json.dumps({'status':'failed','error_type':type(error).__name__,'http_code':getattr(error,'code',None)}))
        raise SystemExit(1)
    with args.receipt.open('x') as f:json.dump(receipt,f,indent=2);f.write('\n')
    print(json.dumps({k:v for k,v in receipt.items() if k!='ranges'}))


if __name__=='__main__':main()
