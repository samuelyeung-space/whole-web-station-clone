"""As-of receipt projections for arbitrary gate and matching times.

This is an input builder, not an execution simulator. A request can include both
direct tokens. No quote after the requested time, synthetic cross-token price,
source-time alignment or EOF extrapolation is permitted.
"""
import math
from atomic_quote_groups import AtomicBooks,receipt_groups


def project(rows,requests,max_age_ms=1000):
    requests=list(requests)
    if not math.isfinite(max_age_ms) or max_age_ms<=0:
        raise ValueError('invalid quote age limit')
    for r in requests:
        t=r.get('at_ms')
        if not isinstance(t,(int,float)) or isinstance(t,bool) or not math.isfinite(t):
            raise ValueError('invalid projection clock')
        if not r.get('asset_ids') or any(not isinstance(a,str) for a in r['asset_ids']):
            raise ValueError('explicit direct token IDs required')
    if len({r['request_id'] for r in requests})!=len(requests):
        raise ValueError('duplicate request ID')
    pending=iter(sorted(requests,key=lambda r:r['at_ms']))
    request=next(pending,None)
    books=AtomicBooks({a for r in requests for a in r['asset_ids']})
    flags={}; last_observed=None
    def snapshot(r,covered):
        t=r['at_ms']; result=dict(request_id=r['request_id'],at_ms=t,
            clock='local_receipt',physical_frame_id_proven=False,observed_horizon_covered=covered,
            source_epoch=books.epoch,tokens={})
        for asset in r['asset_ids']:
            book=books.books.get(asset); ambiguous=flags.get(asset,True)
            reason=('beyond_observed_horizon' if not covered else
                    'missing_direct_snapshot' if book is None else
                    'ambiguous_receipt_group' if ambiguous else
                    'invalid_or_stale_direct_book' if not book.valid_at(t,max_age_ms) else None)
            result['tokens'][asset]=dict(valid=reason is None,rejection_reason=reason,
                recv_ms=book.recv_ms if book else None,
                bids=sorted(book.bids.items(),reverse=True) if book and reason is None else [],
                asks=sorted(book.asks.items()) if book and reason is None else [])
        return result
    for group in receipt_groups(rows):
        now=group[0]['recv_ms']
        while request is not None and request['at_ms']<now:
            # A later receipt establishes that the requested local horizon elapsed;
            # its quote contents are not applied before producing this snapshot.
            yield snapshot(request,True)
            request=next(pending,None)
        if request is None:return
        reset=(books.epoch!=group[0]['connection_epoch'] or any(
            r['kind'] in ('clob_connection','clob_error') for r in group))
        if reset:flags.clear()
        for obs in books.apply(group):flags[obs['asset_id']]=obs['ambiguous_receipt']
        last_observed=now
    while request is not None:
        yield snapshot(request,last_observed is not None and request['at_ms']<=last_observed)
        request=next(pending,None)
