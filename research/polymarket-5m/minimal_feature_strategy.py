"""MFA-v1: development ablation of minimal model-input dependencies.

Eight parameter trials, not eight independent mechanisms. No fitting in signal.
Original JPR, ECO, Oct10/11 freezes and formal OOS remain unchanged.
"""
def candidates():
    out=[]
    for model in ('time','trend'):
        for training in ('fixed','expanded'):
            for edge in (.04,.08):
                score=f'mfa_{model}_{training}'
                out.append({'id':f'MFA-{len(out)+1:03}', 'model':model,
                    'training':training,'score_feature':score,'edge':edge,
                    'family':'existing_probability_mispricing_feature_admission_ablation',
                    'required_features':[score,'mfa_ask_up','mfa_ask_down','mfa_fee','tau_s'],
                    'execution':{'style':'taker','order_type':'FAK','position':'long_binary',
                                 'legs':1,'exit':'official_settlement','scale_in':False}})
    return out


def signal(candidate, features):
    if not 30<=features['tau_s']<=240:return None
    q=features[candidate['score_feature']]
    up=q-features['mfa_ask_up']-features['mfa_fee']*features['mfa_ask_up']*(1-features['mfa_ask_up'])
    down=1-q-features['mfa_ask_down']-features['mfa_fee']*features['mfa_ask_down']*(1-features['mfa_ask_down'])
    if max(up,down)<candidate['edge']:return None
    return 'Up' if up>=down else 'Down'
