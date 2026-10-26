"""Conservative adapter over absorption.TokenBook for normalized receipt ties.

A group is an observed (epoch, local receipt) tie, NOT a proven wire frame.
All rows are applied before exposing a quote. Conflicting same-level updates,
snapshot/delta mixtures and multiple snapshots invalidate gate continuity.
No cancellations, actors, aggressor flow or executable capacity are inferred.
"""
from collections import defaultdict
from absorption import TokenBook


def receipt_groups(rows):
    pending=[]; key=None; previous=None
    for row in rows:
        current=(row['connection_epoch'], row['recv_ms'])
        if previous is not None and row['recv_ms'] < previous:
            raise ValueError('local receipt regression')
        previous=row['recv_ms']
        if pending and current != key:
            yield pending
            pending=[]
        key=current; pending.append(row)
    if pending:
        yield pending


class AtomicBooks:
    def __init__(self, selected_assets=None):
        self.selected_assets=None if selected_assets is None else set(selected_assets)
        self.books={}; self.epoch=None; self.groups=0

    def apply(self, rows):
        if not rows:
            return []
        epoch=rows[0]['connection_epoch']; now=rows[0]['recv_ms']
        if any((r['connection_epoch'],r['recv_ms']) != (epoch,now) for r in rows):
            raise ValueError('not one receipt group')
        self.groups+=1
        reset=(epoch!=self.epoch or any(r['kind'] in ('clob_connection','clob_error') for r in rows))
        if reset:
            self.books.clear()
        self.epoch=epoch
        by_asset=defaultdict(list)
        for r in rows:
            asset=r.get('asset_id')
            if asset is not None and (self.selected_assets is None or asset in self.selected_assets):
                by_asset[asset].append(r)
        out=[]
        for asset, changes in by_asset.items():
            book=self.books.setdefault(asset,TokenBook())
            snapshots=[r for r in changes if r['kind']=='clob_snapshot']
            deltas=[r for r in changes if r['kind']=='clob_price_change']
            ambiguous=len(snapshots)>1 or bool(snapshots and deltas)
            invalid=any(r['kind'] not in ('clob_snapshot','clob_price_change') for r in changes)
            bids=dict(book.bids); asks=dict(book.asks)
            initialized=book.ready
            seen={}
            try:
                if snapshots:
                    snap=snapshots[-1]
                    bids=TokenBook._levels(snap['bids']); asks=TokenBook._levels(snap['asks'])
                    initialized=True
                if deltas and not initialized:
                    invalid=True
                for r in deltas:
                    side=r['side'].upper(); p=float(r['price']); q=float(r['size'])
                    TokenBook._levels([{'price':p,'size':q}])  # canonical bounds, including deletes
                    if side not in ('BUY','SELL'):
                        raise ValueError('invalid side')
                    level=(side,p)
                    if level in seen and seen[level]!=q:
                        ambiguous=True
                    seen[level]=q
                    levels=bids if side=='BUY' else asks
                    if q>0: levels[p]=q
                    else: levels.pop(p,None)
            except (KeyError,ValueError,TypeError,OverflowError):
                invalid=True
            # Commit only the complete final group, avoiding intermediate crosses.
            book.bids=bids; book.asks=asks; book.recv_ms=now
            book.ready=initialized and not invalid and book._uncrossed()
            out.append(dict(asset_id=asset,recv_ms=now,epoch=epoch,book=book,
                validated=book.ready,ambiguous_receipt=ambiguous,
                update_id=(epoch,now),receipt_group_rows=len(changes),
                physical_frame_id_proven=False,lifecycle_reset=reset))
        return out
