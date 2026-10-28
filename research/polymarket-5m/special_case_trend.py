"""Receipt-time 5m/15m context and research-only third/fourth-minute intents.

Extends the existing 60s Wilder ATR/Supertrend implementation in isolated module
namespaces. Existing frozen code/constants are untouched. No execution, wallet,
actor inference, exchange-OHLC equivalence or profitability claim is provided.
"""
import hashlib
import importlib.util
from pathlib import Path

DEPENDENCY_SHA256 = '3cedd882c3f3eb5ab986956de4b33713a3e890acae5deaf12e8a7421204e1ffb'


def context(bar_ms):
    if bar_ms not in (300000,900000):raise ValueError('only registered bar durations')
    path=Path(__file__).with_name('trend_pullback_context.py')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=DEPENDENCY_SHA256:
        raise ValueError('frozen dependency hash mismatch')
    spec=importlib.util.spec_from_file_location('isolated_trend_'+str(bar_ms),path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.BAR_MS=bar_ms
    return module.TrendContext()


class MultiTimeframe:
    def __init__(self):self.contexts={k:context(k) for k in (300000,900000)}
    def ingest(self,row):
        for ctx in self.contexts.values():ctx.ingest(row)
    def snapshot(self,now):
        out={"asof_ms":now}
        for k,ctx in self.contexts.items():
            r=ctx.snapshot(now)
            out[str(k)]=dict(**r,atr=ctx.atr,closed_bars_since_reset=len(ctx.closes),
                              bar_basis='received_trade_ohlc',bar_ms=k)
        return out
    def audit(self):return {str(k):v.audit() for k,v in self.contexts.items()}


class SpecialCase:
    """One fixed market opening, entry at +180s; exit/hedge intention at +240s.

    Input minute bars must be complete consecutive receipt-time UTC bars from
    original trades, with no data/lifecycle gaps. Do not supply 1s sampled OHLC.
    Entry: both closed ST directions agree; first two minutes oppose that trend
    within half opening 5m ATR; third-minute close crosses opening price in the
    trend direction. Fourth-minute flat/reversal emits separate counterfactual
    SELL_HELD or BUY_COMPLEMENT paths, never both orders in one account.
    """
    def __init__(self,start_ms,opening_context,opening_price):
        self.start=start_ms;self.opening=opening_price;self.entered=False;self.checked=False
        self.exit_checked=False;self.bars=[];self.reason=None
        a,b=opening_context['300000'],opening_context['900000']
        self.epochs=(a['ctx_epoch'],b['ctx_epoch']);self.direction=a['ctx_st_direction'];self.atr=a['atr']
        if opening_context.get('asof_ms')!=start_ms:
            self.reason='opening_context_not_stamped_at_market_open'
        if (self.reason is not None or self.direction not in (-1,1) or b['ctx_st_direction']!=self.direction
                or any(e is None for e in self.epochs) or not self.atr or self.atr<=0):
            self.reason='opening_context_unavailable_or_disagrees'
    def closed_minute(self,bar,now,current_epochs):
        if self.reason:return []
        expected=self.start+len(self.bars)*60000
        if (bar.get('start_ms')!=expected or bar.get('end_ms')!=expected+60000
                or now!=expected+60000 or bar.get('available_ms',now)>now
                or bar.get('complete') is not True or tuple(current_epochs)!=self.epochs):
            self.reason='invalid_clock_bar_or_context_epoch';return []
        o,h,l,c=[bar.get(k) for k in ('open','high','low','close')]
        if not all(type(v) in (int,float) and 0<v<float('inf') for v in (o,h,l,c)) or not l<=min(o,c)<=max(o,c)<=h:
            self.reason='invalid_bar';return []
        self.bars.append(dict(bar));s=self.direction
        if len(self.bars)==3:
            self.checked=True;first,two,three=self.bars
            calm=max(first['high'],two['high'])-min(first['low'],two['low'])<=.5*self.atr
            if s*(two['close']-self.opening)<0 and calm and s*(three['close']-two['close'])>0 and s*(three['close']-self.opening)>0:
                self.entered=True
                return [dict(kind='ENTRY_INTENT',at_ms=now,side='Up' if s==1 else 'Down',execution_pending=True)]
        if len(self.bars)==4 and self.entered and not self.exit_checked:
            self.exit_checked=True
            if s*(self.bars[3]['close']-self.bars[2]['close'])<=0:
                return [dict(kind='EXIT_OR_HEDGE_INTENT',at_ms=now,
                    counterfactual_paths=['SELL_HELD_AT_RECEIVED_BID','BUY_COMPLEMENT_AT_RECEIVED_ASK'],
                    held_quantity_required=True,shared_cash_and_inventory_required=True,execution_pending=True)]
        return []
