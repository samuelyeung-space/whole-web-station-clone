"""Observe every delay branch of an eligible H signal without changing HReplay.

Skipped branches are opportunities, not orders or failed fills. This layer
does not cover upstream source/feature rejection and cannot produce PnL.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
import hashlib
from pathlib import Path
import h_attempt_observer as base

EXPECTED_BASE_SHA256='5a5c6952c2d1344d05c09aaf25a7ad0543019e38c759904c9a12f33385ef1672'
VERSION='h-branch-opportunity-observer-v1'

def verify_dependencies():
    engine=base.verify_engine()
    observer=hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest()
    if observer!=EXPECTED_BASE_SHA256:
        raise ValueError('base attempt observer identity drift')
    return {'engine_sha256':engine,'base_observer_sha256':observer}

def signal_clock_ms(event,config):
    received=float(event['receive_ms'])
    source=event.get('source_ms')
    if config.signal_time_basis=='exchange_source_timestamp' and source is not None:
        return float(source)
    if config.signal_time_basis=='local_receipt_timestamp':
        return received
    return received-config.book_lag_ms

def skipped_reason(state,clock,config):
    # Exact priority in the hash-pinned core._signal. Inputs are captured
    # before that method; later book updates or fills are never consulted.
    if state['pending']: return 'pending_order'
    if state['first_fill_direction'] is None:
        if state['first_locked'] and not config.retry_after_no_send_or_kill:
            return 'first_locked'
    else:
        if not config.reentry_enabled: return 'reentry_disabled'
        last=state['last_fill_signal_ms']
        if last is not None and clock-last<config.reentry_min_ms-1e-9:
            return 'reentry_cooldown'
    return None

class BranchObservedHReplay(base.ObservedHReplay):
    def __init__(self,config=None,*,sink):
        verify_dependencies()
        self._branch_suppressed=None
        self.branch_counts=Counter()
        super().__init__(config,sink=sink)

    def _observe_unsent(self,key):
        delay=float(key[0])
        if self._branch_suppressed is None or delay in self._branch_suppressed:
            raise ValueError('unexpected suppressed branch identity')
        self._branch_suppressed.add(delay)
        super()._observe_unsent(key)

    def _signal(self,market,market_id,event):
        if self._branch_suppressed is not None:
            raise ValueError('nested branch signal')
        before={float(d):asdict(branch) for d,branch in market.branches.items()}
        sequence=self._sequence
        expected_signal=self._audit_signal_sequence+1
        clock=signal_clock_ms(event,self.config)
        self._branch_suppressed=set()
        try:
            super()._signal(market,market_id,event)
            if self._audit_signal_sequence!=expected_signal:
                raise ValueError('signal sequence changed unexpectedly')
            queued={}
            for _,seq,delay,attempt in self._pending:
                if seq>sequence:
                    if float(delay) in queued: raise ValueError('duplicate scheduled branch')
                    queued[float(delay)]=attempt
            if (set(queued)|self._branch_suppressed)-set(before):
                raise ValueError('unknown branch outcome')
            for delay,state in before.items():
                skipped=skipped_reason(state,clock,self.config)
                scheduled=queued.get(delay)
                suppressed=delay in self._branch_suppressed
                if skipped is not None:
                    if scheduled is not None or suppressed:
                        raise ValueError('predicted skip produced an attempt')
                    status='skipped'
                    reason=skipped
                else:
                    if (scheduled is not None)+suppressed!=1:
                        raise ValueError('branch requires exactly one attempt outcome')
                    status=('suppressed_unsent' if suppressed else
                            'scheduled_sent' if scheduled.sent else 'scheduled_unsent')
                    reason=None if suppressed else scheduled.decision_reason
                self._emit('branch_opportunity',{
                    'branch_observer_schema':VERSION,
                    'audit_signal_sequence':expected_signal,
                    'market_id':market_id,'evaluation_ms':delay,
                    'signal_receive_ms':float(event['receive_ms']),
                    'signal_source_ms':event.get('source_ms'),
                    'signal_clock_ms':clock,'signal_source':event.get('signal_source'),
                    'branch_status':status,'skip_reason':reason if skipped else None,
                    'gate_state_before':state,
                    'scheduled_decision_reason':reason if scheduled is not None else None,
                    'is_order_attempt':skipped is None,'is_skipped_opportunity':skipped is not None,
                    'pnl':None,'financial_result':None})
                self.branch_counts[(delay,status,skipped or '')]+=1
        finally:
            self._branch_suppressed=None
