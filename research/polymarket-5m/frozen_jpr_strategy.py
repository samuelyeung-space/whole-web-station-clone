"""Dependency-free immutable strategy extraction for offline replay. No training."""
PROVENANCE = {'original_source': 'joint_probability_research.py', 'original_source_sha256': '02f155469f7387dbe8dfb947f16e34f3b03414bf16aa455d5d2c23dc95a5aa09', 'function_sha256': {'catalog': '369319cd5de553786e9ce487be5c0165a83affb44062b2d6080b3e3037822415', 'signal': '2746df3805bcda2e05890889abf51803e5309bb6be8c3a10fbdbacd11f8985f2'}, 'policy': 'exact original function text; no rule or parameter change; not a new candidate family'}

def catalog():
 out=[]
 for h in [5,15]:
  for model in ['time','trend','book','joint']:
   for mechanism in ['edge','continuation','exhaustion']:
    for edge in [.02,.04,.06,.08]:
     key=f'{model}_{h}';out.append({'id':f'JPR-{len(out)+1:03}','model':key,'mechanism':mechanism,'horizon':h,'edge':edge,'required_features':[key,'ask_up','ask_down','fee','r5',f'v{h}',f'a{h}']})
 return out

def signal(c,f):
 p=f[c['model']];up=p-f['ask_up']-f['fee']*f['ask_up']*(1-f['ask_up']);dn=1-p-f['ask_down']-f['fee']*f['ask_down']*(1-f['ask_down']);s=1 if up>=dn else -1
 if max(up,dn)<c['edge'] or not 15<=f['tau_s']<=240:return None
 v=f['v'+str(c['horizon'])];a=f['a'+str(c['horizon'])]
 if c['mechanism']=='continuation' and not(s*f['r5']>0 and s*v>0 and s*a>0):return None
 if c['mechanism']=='exhaustion' and not(s*v<0 and s*a>0):return None
 return 'Up' if s==1 else 'Down'
