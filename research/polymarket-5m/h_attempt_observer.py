"""Research-only observational ledger for suppressed HReplay attempts.

Uses the pinned engine unchanged on disk. Emitting observations can stop the
audit on write failure; it never authorizes a live order or an OOS claim.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Callable
import h_replay as core

EXPECTED_ENGINE_SHA256='38bf2d05604f4e514805d8753e1b4a76b4181b101b8111575a339cfce51fc393'

def verify_engine():
    actual=hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest()
    if actual!=EXPECTED_ENGINE_SHA256:
        raise ValueError('observer engine identity drift')
    return actual

class _ObservedCounter(Counter):
    def __init__(self,callback):
        super().__init__();self._callback=callback
    def __setitem__(self,key,value):
        previous=self.get(key,0)
        if value-previous!=1:
            raise ValueError('unexpected suppressed counter mutation')
        super().__setitem__(key,value)
        self._callback(key)

class ObservedHReplay(core.HReplay):
    """Add signal/unsent/evaluation observations, preserving engine records."""
    def __init__(self,config=None,*,sink:Callable[[dict],None]):
        verify_engine()
        super().__init__(config)
        self._sink=sink
        self._context=None
        self._audit_signal_sequence=0
        self._audit_event_sequence=0
        self._attempt_signal={}
        self.suppressed_unsent=_ObservedCounter(self._observe_unsent)

    def _emit(self,kind,row):
        self._audit_event_sequence+=1
        payload={'schema':'h-attempt-observer-v1','kind':kind,
            'audit_event_sequence':self._audit_event_sequence,
            'engine_sha256':EXPECTED_ENGINE_SHA256,'research_only':True,
            'economic_admission':False,**row}
        self._sink(deepcopy(payload))

    def _signal(self,market,market_id,event):
        if self._context is not None:
            raise ValueError('nested observer signal')
        self._audit_signal_sequence+=1
        signal_sequence=self._audit_signal_sequence
        self._context=(market,market_id,dict(event),signal_sequence)
        self._emit('eligible_signal',{'audit_signal_sequence':signal_sequence,
            'market_id':market_id,'signal_receive_ms':float(event['receive_ms']),
            'signal_source_ms':event.get('source_ms'),
            'signal_source':event.get('signal_source'),'direction':event['direction'],
            'fair':float(event['fair'])})
        previous=self._sequence
        try:
            super()._signal(market,market_id,event)
            for _,sequence,_,attempt in self._pending:
                if sequence>previous:
                    self._attempt_signal[id(attempt)]=signal_sequence
        finally:
            self._context=None

    def _observe_unsent(self,key):
        if self._context is None:
            raise ValueError('suppressed attempt without signal context')
        delay,reason,source=key
        market,market_id,event,signal_sequence=self._context
        receive_ms=float(event['receive_ms'])
        direction='Up' if event['direction'] in ('Up','up',1,True) else 'Down'
        fair=float(event['fair'])
        limit=core.limit_price(fair,self.config.theta,self.config.tick,self.config.fee_rate)
        asks=market.book.asks(direction) if market.book.ready else ()
        branch=market.branches[delay]
        reverse=branch.first_fill_direction is not None and direction!=branch.first_fill_direction
        target=self.config.base_shares*(self.config.reverse_multiplier if reverse else 1.0)
        signed,_=core._pilot_plan(limit,target,self.config) if limit is not None else (None,None)
        self._emit('unsent_attempt',{'audit_signal_sequence':signal_sequence,
            'market_id':market_id,'evaluation_ms':float(delay),'reason':reason,
            'signal_receive_ms':receive_ms,'signal_source_ms':event.get('source_ms'),
            'signal_source':source,'direction':direction,'fair':fair,'fixed_limit':limit,
            'decision_ask':asks[0][0] if asks else None,
            'decision_book_receive_ms':market.book.receive_ms,
            'decision_book_source_ms':market.book.source_ms,
            'book_epoch':market.book_epoch,'target_shares':target,'signed_size':signed,
            'sent':False,'filled':False,'send_ms':None,'earliest_match_ms':None,
            'filled_shares':0.0,'pnl':None,
            'clock_basis':self.config.signal_time_basis})

    def _evaluate(self,attempt,evaluation_ms):
        row=super()._evaluate(attempt,evaluation_ms)
        key=id(attempt)
        if key not in self._attempt_signal:
            raise ValueError('evaluation has no observer signal identity')
        signal_sequence=self._attempt_signal.pop(key)
        self._emit('evaluation',{'audit_signal_sequence':signal_sequence,
            'evaluation':dict(row)})
        return row
