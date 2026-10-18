"""DPP-v1: observed bilateral displayed-depth persistence, development only.

New interactions in existing book-pressure families, not new independent alpha.
Top-three-level imbalance already exists in strategy_zoo; the experiment adds
matched Up-only versus joint-token pressure and complete 3/5s decision paths.
This is neither aggressor flow, cancellation inference nor a maker queue model.
"""
from collections import Counter, defaultdict, deque
import hashlib
import json
import math


VERSION='dpp-v1'
LOOKBACKS=(3,5)


def number(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def fingerprint(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def candidates():
    rows=[]
    for template in ('flat','continuation','reversal'):
        for seconds in LOOKBACKS:
            for threshold in (.25,.5):
                for arm in ('joint','up_control'):
                    c=dict(id=f'DPP-{len(rows)+1:03}',version=VERSION,
                        family='existing_displayed_book_pressure',template=template,
                        seconds=seconds,threshold=threshold,arm=arm,
                        required_features=[f'dpp_{arm}_{seconds}s',f'dpp_net_{seconds}s',
                                           f'dpp_range_{seconds}s','tau_s'],
                        raw_feature_dependencies=['fresh_direct_Up_and_Down_bids_asks_top3',
                                                  'original_side_receipts','complete_1s_decision_grid'],
                        independent_mechanism_claim=False,
                        novelty='joint-token observed persistence and price-path interaction; matched Up-only control',
                        prior_art=['strategy_zoo.book_imbalance(depth=3)','mine500_catalog.book_pressure',
                                   'mine500_path_candidates','complement_queue_lag','common_mode_residual'],
                        execution=dict(style='taker',order_type='FAK',position='long_binary',
                                       legs=1,exit='official_settlement',scale_in=False))
                    c['rule_fingerprint']=fingerprint({k:c[k] for k in
                        ('version','template','seconds','threshold','arm','required_features','execution')})
                    rows.append(c)
    return rows


def signal(candidate,f):
    if not 30<=f['tau_s']<=240:return None
    seconds=candidate['seconds'];q=f'dpp_{candidate["arm"]}_{seconds}s'
    pressure=f[q];net=f[f'dpp_net_{seconds}s'];width=f[f'dpp_range_{seconds}s']
    if abs(pressure)+1e-12<candidate['threshold']:return None
    kind=candidate['template']
    if kind=='flat':
        if width>.01+1e-12:return None
    elif abs(net)+1e-12<.02:return None
    elif kind=='continuation' and pressure*net<=0:return None
    elif kind=='reversal' and pressure*net>=0:return None
    return 'Up' if pressure>0 else 'Down'


def levels(raw,bid):
    if not isinstance(raw,list) or not raw:raise ValueError('missing_depth')
    previous=None;out=[]
    for row in raw:
        if not isinstance(row,(list,tuple)) or len(row)!=2:raise ValueError('invalid_level')
        p,q=row
        if not number(p) or not 0<p<1 or not number(q) or q<=0:raise ValueError('invalid_level')
        if previous is not None and (p>=previous if bid else p<=previous):
            raise ValueError('unsorted_or_duplicate_levels')
        previous=p;out.append((p,q))
    return out


def snapshot(book,t):
    if not isinstance(book,dict) or book.get('healthy') is not True:
        raise ValueError('unhealthy_or_missing_book')
    if not number(book.get('recv_ms')) or book['recv_ms']>t:raise ValueError('future_book')
    imbalances=[];mids=[]
    for side in ('up','down'):
        receipt=book.get(side+'_recv_ms')
        if not number(receipt) or not 0<=t-receipt<=250:raise ValueError('stale_or_missing_side_receipt')
        if receipt>book['recv_ms']:raise ValueError('future_side_receipt')
        if book.get(side+'_healthy',book.get('healthy')) is not True:raise ValueError('unhealthy_side')
        bids=levels(book.get(side+'_bids'),True);asks=levels(book.get(side+'_asks'),False)
        if bids[0][0]>=asks[0][0]:raise ValueError('locked_or_crossed')
        b=sum(q for _,q in bids[:3]);a=sum(q for _,q in asks[:3])
        imbalances.append((b-a)/(b+a));mids.append((bids[0][0]+asks[0][0])/2)
    return ((imbalances[0]-imbalances[1])/2,imbalances[0],(mids[0]+1-mids[1])/2)


def persistent(values):
    if all(x>0 for x in values):return min(values)
    if all(x<0 for x in values):return max(values)
    return 0.


def augment(events,audit=None):
    """Prefix-causal features at original decisions; never rewrite books/labels.

    Persistence refers ONLY to observed 1s decision samples, not complete raw
    feed continuity or unseen intra-second liquidity. Invalid decision samples
    and missing grid points reset history. No interpolation or carry-forward.
    """
    counts=audit if audit is not None else Counter()
    books={};history=defaultdict(lambda:deque(maxlen=max(LOOKBACKS)+1))
    previous=-math.inf;header=False
    for e in events:
        now=e.get('recv_ms')
        if not number(now) or now<previous:raise ValueError('invalid receipt ordering')
        previous=now;kind=e.get('type');mid=e.get('market_id')
        if kind=='dataset':
            if header or e.get('split')!='development':raise ValueError('one development header required')
            header=True;yield e;continue
        if not header:raise ValueError('missing development header')
        if kind=='book':books[mid]=e
        if kind!='decision':yield e;continue
        counts['decisions']+=1
        if not isinstance(e.get('features'),dict) or not isinstance(e.get('feature_available_ms'),dict):
            raise ValueError('missing feature maps')
        hist=history[mid]
        out={**e,'features':dict(e['features']),'feature_available_ms':dict(e['feature_available_ms'])}
        names=[name for seconds in LOOKBACKS for name in
               (f'dpp_joint_{seconds}s',f'dpp_up_control_{seconds}s',f'dpp_net_{seconds}s',f'dpp_range_{seconds}s')]
        if any(name in e['features'] or name in e['feature_available_ms'] for name in names):
            raise ValueError('DPP feature collision')
        for name in names:out['features'][name]=None;out['feature_available_ms'][name]=None
        try:joint,up,midpoint=snapshot(books.get(mid),now)
        except ValueError as exc:
            counts['invalid|'+str(exc)]+=1;hist.clear();yield out;continue
        if hist and now-hist[-1][0]!=1000:
            counts['grid_gap_resets']+=1;hist.clear()
        hist.append((now,joint,up,midpoint));counts['valid_decision_samples']+=1
        for seconds in LOOKBACKS:
            if len(hist)<seconds+1:continue
            path=list(hist)[-(seconds+1):]
            if path[-1][0]-path[0][0]!=seconds*1000:raise AssertionError('noncomplete grid')
            p=[r[3] for r in path]
            values={f'dpp_joint_{seconds}s':persistent([r[1] for r in path]),
                    f'dpp_up_control_{seconds}s':persistent([r[2] for r in path]),
                    f'dpp_net_{seconds}s':round(p[-1]-p[0],12),
                    f'dpp_range_{seconds}s':round(max(p)-min(p),12)}
            for name,value in values.items():out['features'][name]=value;out['feature_available_ms'][name]=now
            counts[f'complete_{seconds}s_paths']+=1
        yield out
