"""Reproduce chronology counterexamples in a pinned legacy Book implementation.

Synthetic engineering evidence only. Does not repair a freeze or run a strategy.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd


def audit(path, expected_blob):
    raw = Path(path).read_bytes()
    blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    if blob != expected_blob:
        raise ValueError('legacy source Git blob mismatch')
    tree = ast.parse(raw)
    nodes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Book']
    if len(nodes) != 1: raise ValueError('one pinned Book class required')
    scope = {'np': np}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    Book = scope['Book']
    cols = ['market_id','ts','recv','bid','ask','bid_size','ask_size']
    late = Book(pd.DataFrame([['synthetic',100.,101.,.40,.45,100.,100.]],columns=cols))
    match = 100.5
    selected = late.at('synthetic',match)
    if selected is None: raise ValueError('expected legacy chronology counterexample changed')
    future_quote = dict(source_ts=100.,receive_ts=101.,local_match_ts=match,
                        quote_selected=True,quote_not_received_by_match=True)
    # After-match disconnect, before the next physical update. Must not change
    # the outcome of an earlier 100.5 local match in a causal implementation.
    books = pd.DataFrame([['synthetic',100.,100.,.40,.45,100.,100.],
                          ['synthetic',101.,101.,.41,.46,100.,100.]],columns=cols)
    base = Book(books)
    future = Book(books,closes=[100.8])
    before = base.across_close('synthetic',100.,100.5)
    after = future.across_close('synthetic',100.,100.5)
    if before or not after: raise ValueError('expected legacy future-close counterexample changed')
    future_close = dict(local_decision_ts=100.,local_match_ts=100.5,later_close_ts=100.8,
                        rejected_without_later_close=before,rejected_with_later_close=after)
    down_ask,down_depth = base.side_ask('synthetic',100.5,'Down')
    if abs(down_ask-.60)>1e-12: raise ValueError('legacy complement convention changed')
    complement = dict(legacy_implied_down_ask=float(down_ask),implied_depth=float(down_depth),
                      actual_direct_down_book_provided=False,direct_down_fill_proven=False)
    return dict(schema='legacy-book-chronology-counterexamples-v1',source_git_blob=blob,
                source_sha256=hashlib.sha256(raw).hexdigest(),synthetic_only=True,
                witnesses=dict(unreceived_quote=future_quote,future_close_filter=future_close,
                               complementary_quote=complement),
                reproduced_witnesses=3,financial_replays=0,actual_fills=0,
                rank7_admitted=False,formal_oos_result=None,
                interpretation='existence of unsafe paths, not measured occurrence count in real recording')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source');p.add_argument('--blob',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=audit(a.source,a.blob)
    Path(a.out).write_text(json.dumps(r,indent=2,allow_nan=False)+'\n')
    print(json.dumps(r,indent=2))
