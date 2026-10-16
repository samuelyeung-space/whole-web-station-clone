"""MEM: existing ECO math on frozen minimal trend scores; development only."""
EXECUTION_STYLE='taker'

def candidates():
    rules=[]
    for training in ('fixed','expanded'):
        q='mfa_trend_'+training
        for horizon in (5,15):
            p=f'mem_{training}_{horizon}'
            for mechanism in ('closing','exhaustion'):
                required=[q,'mfa_ask_up','mfa_ask_down','mfa_fee','tau_s','ep_r_5s',p+'_e',p+'_v',p+'_dq']
                if mechanism=='exhaustion':required+=['imbalance',p+'_a',p+'_j']
                rules.append({'id':f'MEM-{len(rules)+1:03d}','score_feature':q,'training':training,
                    'horizon':horizon,'mechanism':mechanism,'edge':.08,'prefix':p,
                    'required_features':required,'new_independent_mechanism':False})
    controls=[]
    for rule in rules:
        controls.append({**rule,'id':rule['id']+'-MATCHED','control':True})
    for training in ('fixed','expanded'):
        controls.append({'id':'MEM-EDGE-'+training.upper(),'score_feature':'mfa_trend_'+training,
            'training':training,'mechanism':'edge_control','control':True,'edge':.08,
            'required_features':['mfa_trend_'+training,'mfa_ask_up','mfa_ask_down','mfa_fee','tau_s'],
            'new_independent_mechanism':False})
    return rules+controls

def signal(c,f):
    if not 30<=f['tau_s']<=240:return
    q=f[c['score_feature']];up=q-f['mfa_ask_up']-f['mfa_fee']*f['mfa_ask_up']*(1-f['mfa_ask_up'])
    down=1-q-f['mfa_ask_down']-f['mfa_fee']*f['mfa_ask_down']*(1-f['mfa_ask_down'])
    if max(up,down)<c['edge']:return
    s=1 if up>=down else -1
    side='Up' if s==1 else 'Down'
    if c.get('control'):return side
    p=c['prefix']
    if s*f[p+'_e']<=0 or s*f['ep_r_5s']<=0:return
    if c['mechanism']=='closing':ok=s*f[p+'_v']<0 and s*f[p+'_dq']>0
    else:ok=s*f[p+'_v']>0 and s*f[p+'_a']<0 and s*f[p+'_j']<0 and s*f['imbalance']>0
    if ok:return side
