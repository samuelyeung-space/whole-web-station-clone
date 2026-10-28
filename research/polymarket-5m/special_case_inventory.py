"""Inventory-only extension; NOT a quote matcher or financial backtest.

Import executed entry receipts, then fork separate SELL_HELD/BUY_COMPLEMENT
accounts. Exit receipts require upstream execution validation. Sales cash is
conservatively unavailable until acknowledgement. Fees reuse the frozen engine.
"""
from copy import deepcopy
import math
from mine500_replay_v2 import _fee, _rounded_order_fee


def number(x):
    if type(x) not in (int,float) or not math.isfinite(x) or x<0:raise ValueError('invalid number')
    return x


class InventoryAccount:
    def __init__(self,entry,initial_cash=1000):
        number(initial_cash);q=number(entry['filled_shares']);cost=number(entry['cost_usd'])
        self.last_ms=number(entry['match_ms']);self.side=entry['side']
        if self.side not in ('Up','Down') or cost>initial_cash or (q==0 and cost!=0):raise ValueError('invalid entry')
        if entry.get('settled') or entry.get('settlement_ms') is not None:raise ValueError('entry already settled')
        self.cash=initial_cash-cost;self.positions={'Up':0.,'Down':0.};self.positions[self.side]=q
        self.orders={};self.path=None;self.settled=False;self.payout=0.;self.release_ms=None;self.events=[]

    def clock(self,t):
        number(t)
        if t<self.last_ms:raise ValueError('local clock regressed')

    def fork(self,path):
        if self.path is not None or path not in ('SELL_HELD','BUY_COMPLEMENT'):raise ValueError('independent path required')
        a=deepcopy(self);a.path=path;return a

    def available(self):return self.cash-sum(o['cash'] for o in self.orders.values())

    def reserve(self,key,t,requested,limit,rate):
        self.clock(t);number(requested);number(limit);number(rate)
        if not 0<limit<1 or rate>1 or requested<=0 or not isinstance(key,str) or not key or key in self.orders:raise ValueError('invalid order')
        if not self.path or self.settled:raise ValueError('inactive account')
        opposite='Down' if self.side=='Up' else 'Up'
        cap=self.positions[self.side]-sum(o['remaining'] for o in self.orders.values())
        if self.path=='BUY_COMPLEMENT':cap-=self.positions[opposite]
        q=min(requested,max(0.,cap))
        if q<=1e-9:return {'status':'no_unreserved_inventory','submitted':False}
        cash=q*(limit+.25*rate) if self.path=='BUY_COMPLEMENT' else 0.
        if cash>self.available()+1e-9:return {'status':'cash_reject','submitted':False}
        self.orders[key]=dict(decision=t,remaining=q,limit=limit,rate=rate,cash=cash,matched=False,acked=False,credit=0.)
        self.last_ms=t;self.events.append(dict(event='reserve',at=t,order=key,shares=q))
        return dict(status='reserved',submitted=True,target_shares=q)

    def receipt(self,key,t,q,price,*,execution_validated):
        """One single-price FAK receipt only; price must never be multi-level VWAP.

        Multi-level direct-bid/ask matching and fee partition inputs remain a
        separate integration requirement. This API does not authorize assuming
        that a displayed quote was filled.
        """
        self.clock(t);number(q);number(price);o=self.orders[key]
        if execution_validated is not True or not 0<price<1 or o['matched'] or o['acked'] or q>o['remaining']+1e-9:raise ValueError('invalid receipt')
        if q and ((self.path=='SELL_HELD' and price<o['limit']-1e-9) or (self.path=='BUY_COMPLEMENT' and price>o['limit']+1e-9)):raise ValueError('fixed limit violated')
        fee=float(_rounded_order_fee(_fee(q,price,o['rate'])))
        if self.path=='BUY_COMPLEMENT':
            debit=q*price+fee
            if debit>o['cash']+1e-9:raise ValueError('reservation exceeded')
            self.cash-=debit;o['cash']-=debit
            self.positions['Down' if self.side=='Up' else 'Up']+=q
        else:self.positions[self.side]-=q;o['credit']=q*price-fee
        o['remaining']-=q;o['matched']=True;o['match_ms']=t;self.last_ms=t
        self.events.append(dict(event='receipt',order=key,at=t,filled=q,price=price,fee=fee));self.check()

    def acknowledge(self,key,t):
        self.clock(t);o=self.orders[key]
        if not o['matched'] or o['acked']:raise ValueError('invalid acknowledgement')
        self.cash+=o['credit'];o.update(credit=0.,cash=0.,remaining=0.,acked=True);self.last_ms=t
        self.events.append(dict(event='acknowledgement',order=key,at=t));self.check()

    def settle(self,winner,known_ms,market_end_ms,cash_release_ms):
        self.clock(known_ms);number(market_end_ms);number(cash_release_ms)
        if self.settled or winner not in self.positions or known_ms<market_end_ms or cash_release_ms<known_ms:raise ValueError('invalid official settlement')
        if any(not o['acked'] for o in self.orders.values()):raise ValueError('pending orders')
        self.payout=self.positions[winner];self.positions={'Up':0.,'Down':0.}
        self.settled=True;self.release_ms=cash_release_ms;self.last_ms=known_ms
        self.events.append(dict(event='official_settlement',at=known_ms,payout=self.payout))

    def release(self,t):
        self.clock(t)
        if not self.settled or self.release_ms is None or t<self.release_ms:raise ValueError('payout unavailable')
        self.cash+=self.payout;self.payout=0.;self.release_ms=None;self.last_ms=t
        self.events.append(dict(event='cash_release',at=t));self.check()

    def check(self):
        if self.available()<-1e-9 or min(self.positions.values())<-1e-9:raise AssertionError('negative cash or inventory')
