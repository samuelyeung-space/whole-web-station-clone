"""MLQ-01 researcher formalization, not replication of Martin Luk trades.
Pure plugin: all receipt-clock state is computed by mlq_first_pullback_features.
"""
EXECUTION_STYLE='taker'
def candidates():
    return [dict(id=f'MLQ01-B{bar}-{mode.upper()}',mechanism='causal_pullback_ordinal',bar_s=bar,first_only=mode=='first',edge=.02,
       required_features=['book_5','ask_up','ask_down','fee','tau_s',f'mlq{bar}_direction',f'mlq{bar}_ordinal'])
       for bar in (1,5) for mode in ('first','all')]

def signal(c,f):
    s=f[f"mlq{c['bar_s']}_direction"]
    if s==0 or not 60<=f['tau_s']<=240:return
    if c['first_only'] and f[f"mlq{c['bar_s']}_ordinal"]!=1:return
    q=f['book_5'] if s==1 else 1-f['book_5'];p=f['ask_up'] if s==1 else f['ask_down']
    if q-p-f['fee']*p*(1-p)<c['edge']:return
    return 'Up' if s==1 else 'Down'
