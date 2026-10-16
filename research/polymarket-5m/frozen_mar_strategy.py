"""Dependency-free immutable strategy extraction for offline replay. No training."""
PROVENANCE = {'original_source': 'mean_regime_research.py', 'original_source_sha256': '11da7b97f53cf97dd078c1eff04844c8575836392a113b7a18fa03eec9e521c8', 'function_sha256': {'candidates': 'f542e9ad8ef2c6396249f2a8b37ccaabec587903f7242a4f4434542b2d02a077', 'signal': 'ff6fd1981b2bff6affca6fa9ebab9a573b9d865214b9d6e310472c2603804dad'}, 'policy': 'exact original function text; no rule or parameter change; not a new candidate family'}

def candidates():
 cs=[]
 for mechanism in ['trend_pullback','flat_overshoot','btc_jump_poly_lag']:
  for w in [15,60,120]:
   for slope in [False,True]:
    for smooth in [False,True]:
     for band in [.5,1.]:
      for edge in [.04,.08]:
       cs.append({'id':f'MAR-{len(cs)+1:03}','mechanism':mechanism,'window':w,'reference_slope_gate':slope,'smooth_only':smooth,'band':band,'edge':edge,'required_features':['book_5','ask_up','ask_down','fee',f'z{w}',f'slope{w}',f'refslope{w}','btc_r5','poly_eff','poly_move','imbalance']})
 return cs

def signal(c,f):
 p=f['book_5'];u=p-f['ask_up']-f['fee']*f['ask_up']*(1-f['ask_up']);d=1-p-f['ask_down']-f['fee']*f['ask_down']*(1-f['ask_down']);s=1 if u>=d else -1
 if max(u,d)<c['edge'] or not 30<=f['tau_s']<=240:return
 if c['smooth_only'] and not(f['poly_eff']>=.7 and s*f['poly_move']>0):return
 w=c['window'];z=f[f'z{w}'];sl=f[f'slope{w}'];rs=f[f'refslope{w}']
 if c['mechanism']=='trend_pullback':ok=s*sl>.5 and abs(z)<=c['band'] and s*f['btc_r5']>0
 elif c['mechanism']=='flat_overshoot':ok=abs(sl)<=.5 and s*z < -c['band'] and s*f['imbalance']>0
 else:ok=s*f['btc_r5']>=1 and s*z>=c['band'] and s*f['poly_move']<.02
 if c['reference_slope_gate']:
  ok=ok and (abs(rs)<=.5 if c['mechanism']=='flat_overshoot' else s*rs>0)
 return ('Up' if s>0 else 'Down') if ok else None
