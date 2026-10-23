"""Offline synthetic reproduction using three exact pinned recorder methods.

Never imports or runs the recorder, opens sockets or changes source files.
"""
import argparse
import ast
from collections import deque
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from futures_route_frames import RouteFrameObserver

PINNED_BLOB = '7b8b746c5a040ab6a97c2b4a68ca03680be624fc'
METHODS = {'_binance_record', '_first_binance_frame', 'on_binance_futures_trade'}


def reproduce(path):
    raw = Path(path).read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if blob != PINNED_BLOB:
        raise ValueError('recorder Git blob mismatch')
    tree = ast.parse(raw.decode('utf-8'))
    selected = [node for cls in tree.body if isinstance(cls, ast.ClassDef)
                for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in METHODS]
    if {node.name for node in selected} != METHODS or len(selected) != 3:
        raise ValueError('pinned method set mismatch')
    subset = ast.Module(body=selected, type_ignores=[])
    namespace = {'json': json}
    exec(compile(subset, str(path), 'exec'), namespace)
    recorder_type = type('PinnedMethodsOnly', (), {name: namespace[name] for name in METHODS})
    recorder = recorder_type()
    normalized = []
    recorder.rec = type('Sink', (), {'write': lambda _, stream, row: normalized.append((stream, row))})()
    recorder.binance_sources = {'futures_trade': dict(epoch=1, sequence=0,
                               active_connections={0, 1}, max_active=2)}
    recorder.binance_seen = {'futures_trade': set()}
    recorder.binance_seen_order = {'futures_trade': deque()}
    frame = dict(e='trade', s='BTCUSDT', t=7, T=999, E=1000, p='0', q='0', m=True)
    first = json.dumps(frame)
    later = json.dumps({'stream': 'btcusdt@trade', 'data': dict(frame, p='80000', q='.01')})
    observer = RouteFrameObserver(capture_id='synthetic-pinned-handler-check', clock_id='synthetic-clock',
                                 routes={'a': 'synthetic-direct', 'b': 'synthetic-combined'}, split='development')
    retained = []
    for i, text in enumerate((first, later)):
        observer.record_and_forward(text, recv_ms=1100+i*50, route_id='ab'[i], conn_id=i,
                                    epoch=1, sequence=0, writer=retained.append,
                                    forward=recorder.on_binance_futures_trade)
    envelopes = [json.loads(row) for row in retained]
    assert len(normalized) == 1 and normalized[0][1]['p'] == '0'
    assert len(envelopes) == 2 and envelopes[1]['comparison']['status'] == 'unresolved_payload_conflict'
    return {'classification': 'synthetic counterexample, not an observed route conflict',
            'recorder_blob': blob, 'selected_methods': sorted(METHODS),
            'legacy_first_copy_records': len(normalized), 'pre_dedup_envelopes': len(envelopes),
            'later_copy_available_recv_ms': envelopes[1]['comparison']['available_recv_ms'],
            'raw_envelopes': envelopes, 'coverage_certified': False,
            'features_admitted': 0, 'financial_replays': 0, 'pnl_usd': None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pinned_recorder')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    Path(args.output).write_text(json.dumps(reproduce(args.pinned_recorder), indent=2) + '\n')
