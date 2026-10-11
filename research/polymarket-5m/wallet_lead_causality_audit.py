"""Synthetic prefix-invariance audit of pinned upstream Family 32; not a backtest.

Loads only AST-selected pure functions, never the upstream download/runner entrypoint.
Usage: python wallet_lead_causality_audit.py --source DIR --out NEW_JSON
"""
import argparse
import ast
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd


def functions(path, names, env):
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in nodes} != set(names):
        raise ValueError('Required pinned functions absent')
    tree = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)] + nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree), str(path), 'exec'), env)


def audit(source):
    cross = {'HEALTH_STATE': ('up_best_bid', 'up_best_ask', 'down_best_bid', 'down_best_ask', 'up_ask_size', 'down_ask_size', 'up_bid_size', 'down_bid_size')}
    functions(source / 'cross.py', ['book_health', 'changed_near'], cross)
    env = dict(np=np, pd=pd, Decimal=Decimal, cross=SimpleNamespace(**cross),
               wl=SimpleNamespace(BookSnapshot=lambda **kw: kw), BOOK_CHANGE_WINDOW_MS=2000)
    functions(source / 'wallet_lead_scan.py', ['_decimal_or_none', '_snapshot_at'], env)
    def row(t, ask=.5):
        return dict(timestamp_ms=t, market_id='synthetic-only', up_best_bid=.49,
                    up_best_ask=ask, down_best_bid=.49, down_best_ask=.51,
                    up_ask_size=100, down_ask_size=100, lifecycle_state='active', observed_halt_flag=False)
    prefix = [row(9000), row(10000)]
    unchanged = pd.DataFrame(prefix + [row(11000)])
    changed = pd.DataFrame(prefix + [row(11000, .52)])
    a, b = env['_snapshot_at'](unchanged, 10000)[0], env['_snapshot_at'](changed, 10000)[0]
    assert unchanged[unchanged.timestamp_ms <= 10000].equals(changed[changed.timestamp_ms <= 10000])
    assert a['timestamp_ms'] == b['timestamp_ms'] == 10000
    assert a['up_ask'] == b['up_ask'] == Decimal('.5')
    # The production kernel returns stale_book when fresh is false and can fill when true.
    assert a['fresh'] is False and b['fresh'] is True
    clock = {}
    functions(source / 'wallet_lead.py', ['observable_ms'], clock)
    return dict(classification='synthetic falsification; no market replay or PnL',
                source_sha256={n: hashlib.sha256((source/n).read_bytes()).hexdigest()
                               for n in ('cross.py','wallet_lead.py','wallet_lead_scan.py')},
                prefix_identical_through_ms=10000, only_changed_timestamp_ms=11000,
                snapshot_ms=10000, identical_ask='0.5',
                fresh_without_future_change=a['fresh'], fresh_with_future_change=b['fresh'],
                prefix_invariance_pass=False,
                finding='Future quote changes control stale_book rejection; invalid executable fill gate.',
                clock_example=dict(block_timestamp_s=10, assumed_observable_ms=clock['observable_ms'](10),
                                   measured_local_receive_ms=None, verdict='End-of-second bound does not establish actual receipt time'),
                required_fix='Preserve original freeze; new version needs local receipt clocks, causal feed state, all attempts and unresolved labels retained.',
                new_real_strategy_replays=0, new_qualified_strategies=0, pnl=None)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    result = audit(a.source)
    with a.out.open('x') as f:
        json.dump(result, f, indent=2, allow_nan=False)
    print(json.dumps(result, indent=2))
