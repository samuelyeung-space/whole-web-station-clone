"""ECO development rules: model/market error motion, not confirmed fair value."""
EXECUTION_STYLE='taker'

def candidates():
    rules=[]
    for mechanism in ('closing','exhaustion'):
        for normalization in ('plain','twap_variance'):
            for horizon in (5,15):
                for edge in (.04,.08):
                    p=f'eco_{normalization}_{horizon}'
                    rules.append({'id':f'ECO-{len(rules)+1:03d}','mechanism':mechanism,
                        'normalization':normalization,'horizon':horizon,'edge':edge,
                        'independent_mechanism_claim':False,
                        'required_features':['book_5','ask_up','ask_down','fee','r5','imbalance',
                                             p+'_e',p+'_v',p+'_a',p+'_j',p+'_dq']})
    # Matched controls are existing book_5 edge concepts, not new economic rules.
    for edge in (.04,.08):
        rules.append({'id':f'ECO-CONTROL-{int(edge*100):02d}','mechanism':'edge_control','edge':edge,
                      'required_features':['book_5','ask_up','ask_down','fee']})
    return rules

def signal(c,f):
    q=f['book_5'];up=q-f['ask_up']-f['fee']*f['ask_up']*(1-f['ask_up'])
    dn=1-q-f['ask_down']-f['fee']*f['ask_down']*(1-f['ask_down'])
    s=1 if up>=dn else -1
    if max(up,dn)<c['edge'] or not 30<=f['tau_s']<=240:return
    if c['mechanism']=='edge_control':return 'Up' if s==1 else 'Down'
    p=f"eco_{c['normalization']}_{c['horizon']}"
    if s*f[p+'_e']<=0 or s*f['r5']<=0:return
    if c['mechanism']=='closing':
        ok=s*f[p+'_v']<0 and s*f[p+'_dq']>0
    else:
        ok=s*f[p+'_v']>0 and s*f[p+'_a']<0 and s*f[p+'_j']<0 and s*f['imbalance']>0
    if ok:return 'Up' if s==1 else 'Down'
