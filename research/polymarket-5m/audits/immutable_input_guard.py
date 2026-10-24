"""Offline input integrity guard. It never changes trading rules or certifies PnL."""
import argparse,gzip,hashlib,json,pathlib,subprocess,time,sys

class InputRejected(ValueError):pass

def fingerprint(p):
 s=p.stat();return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_mode)

def preflight(root,files):
 root=pathlib.Path(root).resolve();state={}
 for item in files:
  rel=pathlib.Path(item['path']);p=(root/rel).resolve()
  if rel.is_absolute() or '..' in rel.parts or not p.is_relative_to(root):raise InputRejected('unsafe input path')
  before=fingerprint(p)
  if before[2]!=item['bytes']:raise InputRejected('input size mismatch: '+str(rel))
  with p.open('rb') as f:h=hashlib.file_digest(f,'sha256').hexdigest()
  if h!=item['sha256']:raise InputRejected('input hash mismatch: '+str(rel))
  if p.suffix=='.gz':
   try:
    with gzip.open(p,'rb') as f:
     while f.read(1<<20):pass
   except (EOFError,OSError) as e:raise InputRejected('invalid full gzip: '+str(rel)) from e
  if fingerprint(p)!=before:raise InputRejected('input changed during preflight: '+str(rel))
  state[p]=before
 return state

def changed(state):
 for p,expected in state.items():
  try:now=fingerprint(p)
  except OSError:return str(p)
  if now!=expected:return str(p)
 return None

def run(root,files,command,interval=.05):
 state=preflight(root,files);proc=subprocess.Popen(command)
 mutation=None
 try:
  while True:
   mutation=changed(state)
   if mutation:
    proc.terminate()
    try:proc.wait(timeout=5)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()
    break
   code=proc.poll()
   if code is not None:break
   time.sleep(interval)
 finally:
  if proc.poll() is None:proc.terminate();proc.wait()
 if mutation or changed(state):raise InputRejected('input changed during/after worker; result rejected')
 # Recheck bytes, hash and gzip, not just stat, after the worker has closed output.
 preflight(root,files)
 if code:raise InputRejected('worker failed: '+str(code))
 return dict(schema='immutable-input-guard-v1',worker_exit_code=0,input_integrity_passed=True,
             financial_profitability_validated=False,formal_oos_result=None)

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--manifest',required=True);p.add_argument('--receipt',required=True);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
 cmd=a.command[1:] if a.command[:1]==['--'] else a.command
 if not cmd:p.error('worker command required')
 try:result=run(a.root,json.load(open(a.manifest))['files'],cmd)
 except InputRejected as e:
  pathlib.Path(a.receipt).write_text(json.dumps(dict(status='rejected',reason=str(e),financial_result=None)));sys.exit(2)
 pathlib.Path(a.receipt).write_text(json.dumps(result,indent=2))
