"""Pure quote-state gates, fed by an existing validated direct TokenBook.

No book matching, trade-direction inference, cancel attribution, actor identity
or financial replay is implemented. The caller must apply an entire physical
frame to the canonical TokenBook before passing its complete current levels,
deduplicate redundant transports, and provide unambiguous local receipt order.
Displayed depth disappearing is not labelled cancellation rather than a fill.
"""
from collections import deque
from dataclasses import dataclass
import math
from statistics import median


@dataclass(frozen=True)
class Config:
    baseline_ms: float = 1000.0
    minimum_baseline_updates: int = 3
    deadline_ms: float = 30000.0
    hold_ms: float = 250.0
    maximum_event_gap_ms: float = 1000.0
    opposing_imbalance: float = .5
    neutral_imbalance: float = .1
    recovered_depth_fraction: float = .5
    collapsed_depth_fraction: float = .25
    spread_expansion: float = 2.0
    wall_fraction: float = .5
    minimum_wall_updates: int = 3
    overshoot_spreads: float = 3.0

    def __post_init__(self):
        positive=(self.baseline_ms,self.deadline_ms,self.hold_ms,self.maximum_event_gap_ms,
                  self.spread_expansion,self.overshoot_spreads)
        if any(not _finite(x) or x<=0 for x in positive):
            raise ValueError('invalid positive quote-gate parameter')
        fractions=(self.opposing_imbalance,self.neutral_imbalance,
                   self.recovered_depth_fraction,self.collapsed_depth_fraction,self.wall_fraction)
        if any(not _finite(x) or not 0<x<=1 for x in fractions):
            raise ValueError('invalid quote-gate fraction')
        if self.collapsed_depth_fraction>=self.recovered_depth_fraction:
            raise ValueError('collapse threshold must be below recovery threshold')
        if any(type(x) is not int or x<1 for x in
               (self.minimum_baseline_updates,self.minimum_wall_updates)):
            raise ValueError('invalid distinct-update count')


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _quote(bids, asks):
    """Defensive input checks only: reconstruction belongs to TokenBook."""
    def levels(raw, reverse):
        pairs = list(raw.items()) if hasattr(raw, 'items') else list(raw)
        if any(not(_finite(p) and _finite(q) and 0 < p < 1 and q >= 0) for p,q in pairs):
            raise ValueError('invalid direct quote levels')
        return sorted(((float(p),float(q)) for p,q in pairs if q > 0),reverse=reverse)[:5]
    b,a=levels(bids,True),levels(asks,False)
    if not b or not a or b[0][0] >= a[0][0]:
        raise ValueError('empty or crossed direct book')
    bd,ad=sum(q for _,q in b),sum(q for _,q in a)
    return dict(bids=b,asks=a,depth=bd+ad,imbalance=(bd-ad)/(bd+ad),
                spread=a[0][0]-b[0][0],mid=(a[0][0]+b[0][0])/2)


class QuoteGates:
    def __init__(self, config=None):
        self.config=config or Config()
        self.books={}
        self.arms={}
        self.events=[]

    def _record(self,key,status,now,**extra):
        e=dict(parentkey=key,status=status,recv_ms=now,**extra)
        self.events.append(e)
        return e

    def arm(self,parentkey,direction,initial_mid,time,epoch=None):
        if parentkey in self.arms:
            return False  # first parent signal is never replaced
        if direction not in (-1,1) or not(_finite(initial_mid) and 0 < initial_mid < 1 and _finite(time)):
            raise ValueError('invalid parent arm')
        book=self.books.get(parentkey)
        if not book or book['time'] > time or time-book['time'] > self.config.maximum_event_gap_ms:
            self.arms[parentkey]={'status':'rejected_missing_causal_baseline'}
            self._record(parentkey,'rejected_missing_causal_baseline',time)
            return False
        if epoch is not None and epoch != book['epoch']:
            self.arms[parentkey]={'status':'rejected_epoch'}
            self._record(parentkey,'rejected_epoch',time)
            return False
        rows=list(book['history'])
        rows=[r for r in rows if time-self.config.baseline_ms <= r['time'] <= time]
        if len(rows)<self.config.minimum_baseline_updates:
            self.arms[parentkey]={'status':'rejected_insufficient_baseline'}
            self._record(parentkey,'rejected_insufficient_baseline',time)
            return False
        self.arms[parentkey]=dict(status='armed',direction=direction,initial_mid=float(initial_mid),
            arm_ms=time,epoch=book['epoch'],baseline_depth=median(r['depth'] for r in rows),
            baseline_spread=median(r['spread'] for r in rows),
            pressure_seen=any(direction*r['imbalance']<=-self.config.opposing_imbalance for r in rows),
            balance_since=None,balance_fired=False,vacuum_fired=False,collapse_seen=False,
            wall_price=None,wall_since=None,wall_updates=0,worst_signed=0.0,
            overshoot_seen=False)
        self._record(parentkey,'armed',time)
        return True

    def disconnect(self,parentkey,time,reason='disconnect'):
        self.books.pop(parentkey,None)
        arm=self.arms.get(parentkey)
        if arm and arm.get('status')=='armed':
            arm['status']='cancel_'+reason
            self._record(parentkey,arm['status'],time)

    def on_book(self,parentkey,recv_ms,epoch,bids,asks,*,update_id=None,
                validated=True,ambiguous_receipt=False):
        """Accept one complete book state after an atomic, distinct frame."""
        if not(_finite(recv_ms) and _finite(epoch)):
            raise ValueError('invalid local clock/epoch')
        prior=self.books.get(parentkey)
        if (prior and validated and not ambiguous_receipt and epoch==prior['epoch']
                and update_id is not None and update_id==prior['update_id']):
            return []  # duplicate transport/frame does not count toward persistence
        if not validated or ambiguous_receipt or (prior and recv_ms<=prior['time']):
            self.disconnect(parentkey,recv_ms,'ambiguous_or_invalid_book')
            return []
        if prior and (epoch!=prior['epoch'] or recv_ms-prior['time']>self.config.maximum_event_gap_ms):
            self.disconnect(parentkey,recv_ms,'epoch_or_gap')
            prior=None
        try:
            q=_quote(bids,asks)
        except (ValueError,TypeError,OverflowError):
            self.disconnect(parentkey,recv_ms,'invalid_book')
            return []
        q['time']=recv_ms
        history=prior['history'] if prior else deque()
        self.books[parentkey]=dict(time=recv_ms,epoch=epoch,update_id=update_id,quote=q,history=history)
        out=[]
        arm=self.arms.get(parentkey)
        if arm and arm.get('status')=='armed':
            out=self._advance(parentkey,arm,q,recv_ms)
        history.append(q)
        while history and recv_ms-history[0]['time']>self.config.baseline_ms:
            history.popleft()
        return out

    def _advance(self,key,arm,q,now):
        c=self.config
        if now-arm['arm_ms']>=c.deadline_ms:
            arm['status']='timeout_unfilled'
            self._record(key,'timeout_unfilled',now)
            return []
        if now<arm['arm_ms']:
            return []
        s=arm['direction'];out=[]
        depth_ok=q['depth']>=c.recovered_depth_fraction*arm['baseline_depth']
        spread_normal=q['spread']<=arm['baseline_spread']+1e-12
        if s*q['imbalance']<=-c.opposing_imbalance:
            arm['pressure_seen']=True
        equilibrium=(arm['pressure_seen'] and abs(q['imbalance'])<=c.neutral_imbalance
                     and depth_ok and spread_normal)
        if equilibrium:
            if arm['balance_since'] is None:arm['balance_since']=now
            if not arm['balance_fired'] and now-arm['balance_since']>=c.hold_ms:
                arm['balance_fired']=True
                out.append(self._record(key,'balance_ready',now,direction=s,
                    imbalance=q['imbalance'],depth=q['depth'],spread=q['spread']))
        else:
            arm['balance_since']=None
        collapse=(q['depth']<=c.collapsed_depth_fraction*arm['baseline_depth']
                  and q['spread']+1e-12>=c.spread_expansion*arm['baseline_spread'])
        if collapse and not arm['collapse_seen']:
            arm['collapse_seen']=True
            self._record(key,'displayed_depth_collapse',now)
        signed=s*(q['mid']-arm['initial_mid'])
        previous_worst=arm['worst_signed']
        arm['worst_signed']=min(previous_worst,signed)
        if -arm['worst_signed']+1e-12>=c.overshoot_spreads*arm['baseline_spread']:
            arm['overshoot_seen']=True
        if arm['collapse_seen']:
            side=q['bids'] if s==1 else q['asks']
            price,size=max(side,key=lambda x:(x[1],s*x[0]))
            wall_ok=size+1e-12>=c.wall_fraction*sum(v for _,v in side)
            if wall_ok:
                if arm['wall_price']!=price:
                    arm.update(wall_price=price,wall_since=now,wall_updates=1)
                else:
                    arm['wall_updates']+=1
            else:
                arm.update(wall_price=None,wall_since=None,wall_updates=0)
            persistent=(wall_ok and arm['wall_updates']>=c.minimum_wall_updates
                        and now-arm['wall_since']>=c.hold_ms)
            repairing=signed>previous_worst+1e-12
            if (not arm['vacuum_fired'] and persistent and arm['overshoot_seen']
                    and depth_ok and spread_normal and repairing):
                arm['vacuum_fired']=True
                out.append(self._record(key,'vacuum_repair_ready',now,direction=s,
                    wall_price=price,wall_updates=arm['wall_updates'],
                    prior_overshoot=-arm['worst_signed'],mid=q['mid']))
        return out

    def gate(self,parentkey,time,family='balance'):
        """Only a gate stamped by an already ingested book event may be used."""
        if family not in ('balance','vacuum'):
            raise ValueError('unknown quote-gate family')
        arm=self.arms.get(parentkey);book=self.books.get(parentkey)
        if not arm or not book or time>book['time'] or time<arm.get('arm_ms',math.inf):
            return False
        if arm.get('status')!='armed':return False
        wanted='balance_ready' if family=='balance' else 'vacuum_repair_ready'
        return any(e['parentkey']==parentkey and e['status']==wanted and e['recv_ms']==time
                   for e in reversed(self.events))

    def audit(self):
        return dict(events=list(self.events),arms={str(k):dict(v) for k,v in self.arms.items()},
                    quote_observations_only=True,actor_or_cancel_attribution=False,
                    economic_pnl=None)
