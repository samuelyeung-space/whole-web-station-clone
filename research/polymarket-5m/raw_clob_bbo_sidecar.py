"""Preserve native top-quote observations separately from frozen strict depth.

No orders, depth synthesis, settlement labels or economic scoring. Missing
receipt clocks fail explicitly instead of silently scanning with a zero clock.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import tarfile
from collections import Counter
from collections.abc import Mapping


def envelope(row):
    if 'recv_ms' in row:
        recv = float(row['recv_ms'])
        epoch, sequence = row.get('connection_epoch'), row.get('sequence')
        messages = row.get('msg', [])
        connection_id = None
        schema = 'legacy_recv_ms_msg'
    elif 'rn' in row:
        recv = float(row['rn']) / 1e6
        epoch, sequence = row.get('e'), row.get('s')
        messages = row.get('m', [])
        connection_id = row.get('c')
        schema = 'native_rn_m'
    else:
        raise ValueError('missing explicit raw receipt clock')
    if not math.isfinite(recv) or recv < 0:
        raise ValueError('invalid raw receipt clock')
    if type(epoch) is not int or epoch < 1 or type(sequence) is not int or sequence < 0:
        raise ValueError('missing or invalid raw epoch/sequence')
    if isinstance(messages, Mapping):
        messages = [messages]
    if not isinstance(messages, list) or any(not isinstance(m, Mapping) for m in messages):
        raise ValueError('invalid raw message container')
    return dict(recv_ms=recv, connection_epoch=epoch, input_sequence=sequence,
        connection_id=connection_id, envelope_schema=schema), messages


def top_quote(message, context):
    if message.get('event_type') != 'best_bid_ask':
        return None
    source = float(message['timestamp'])
    bid, ask = float(message['best_bid']), float(message['best_ask'])
    if not (math.isfinite(source) and source >= 0 and math.isfinite(bid)
            and math.isfinite(ask) and 0 <= bid < ask <= 1):
        raise ValueError('invalid native top quote')
    if not message.get('asset_id') or not message.get('market'):
        raise ValueError('missing native quote token/market')
    return dict(context, kind='clob_native_bbo_observation',
        source_ts_ms=source, asset_id=str(message['asset_id']),
        market_id=str(message['market']), best_bid=bid, best_ask=ask,
        quote_only=True, depth_known=False)


def raw_records(raw_tar, member, max_records):
    if not 1 <= max_records <= 1000000:
        raise ValueError('max_records must be1..1000000')
    previous = -math.inf
    with tarfile.open(raw_tar) as archive:
        with gzip.GzipFile(fileobj=archive.extractfile(member)) as stream:
            for line_number, line in enumerate(stream, 1):
                if line_number > max_records:
                    break
                row = json.loads(line)
                context, messages = envelope(row)
                if context['recv_ms'] < previous:
                    raise ValueError('raw receipt clock regressed')
                previous = context['recv_ms']
                yield dict(context, raw_line=line_number), messages


def signature(recv, source, market, change):
    return (recv, source, market, str(change['asset_id']), str(change['side']),
        float(change['price']), float(change['size']),
        float(change['best_bid']) if 'best_bid' in change else None,
        float(change['best_ask']) if 'best_ask' in change else None)


def audit_window(raw_tar, member, strict, target_ms, *, lookback_ms=20, max_records=10000):
    if not math.isfinite(target_ms) or target_ms < 0 or not 0 <= lookback_ms <= 1000:
        raise ValueError('invalid diagnostic window')
    raw_changes, strict_changes = Counter(), Counter()
    raw_order, strict_order = [], []
    quotes, raw_window = [], []
    kinds = Counter()
    schemas = Counter()
    past_target = False
    for context, messages in raw_records(raw_tar, member, max_records):
        recv = context['recv_ms']
        if recv > target_ms:
            past_target = True
            break
        if recv < target_ms - lookback_ms:
            continue
        raw_window.append(dict(context, messages=messages))
        schemas[context['envelope_schema']] += 1
        for message in messages:
            kinds[str(message.get('event_type'))] += 1
            quote = top_quote(message, context)
            if quote is not None:
                quotes.append(quote)
            if message.get('event_type') == 'price_change':
                for change in message.get('price_changes', []):
                    key = signature(recv, float(message['timestamp']), str(message['market']), change)
                    raw_changes[key] += 1
                    raw_order.append(key)
    if not past_target:
        raise ValueError('bounded raw prefix does not cover target window')
    with gzip.open(strict, 'rt') as stream:
        for line in stream:
            row = json.loads(line)
            recv = row['recv_ms']
            if recv > target_ms:
                break
            if recv >= target_ms - lookback_ms and row['kind'] == 'clob_price_change':
                key = signature(recv, row['source_ts_ms'], row['market_id'], row)
                strict_changes[key] += 1
                strict_order.append(key)
    target_changes = [key for key in raw_changes if key[0] == target_ms]
    matched_quotes = []
    for key in target_changes:
        prior = [q for q in quotes if q['asset_id'] == key[3] and q['recv_ms'] < target_ms
                 and q['source_ts_ms'] == key[1] and q['best_bid'] == key[7] and q['best_ask'] == key[8]]
        if prior:
            quote = max(prior, key=lambda q: q['recv_ms'])
            matched_quotes.append(dict(asset_id=key[3], raw_line=quote['raw_line'],
                earlier_receive_ms=quote['recv_ms'], later_price_change_ms=target_ms,
                lead_ms=target_ms-quote['recv_ms'], best_bid=quote['best_bid'], best_ask=quote['best_ask']))
    payload = json.dumps(raw_window, sort_keys=True, separators=(',', ':')).encode()
    return dict(schema='raw-clob-native-bbo-window-audit-v1',
        raw_member=member, target_recv_ms=target_ms, lookback_ms=lookback_ms,
        max_input_records=max_records, raw_window_envelopes=len(raw_window),
        raw_window_sha256=hashlib.sha256(payload).hexdigest(), envelope_schemas=dict(schemas),
        message_types=dict(kinds), raw_price_change_rows=sum(raw_changes.values()),
        strict_price_change_rows=sum(strict_changes.values()),
        missing_from_strict=sum((raw_changes-strict_changes).values()),
        extra_in_strict=sum((strict_changes-raw_changes).values()),
        ordered_price_changes_equal=raw_order == strict_order,
        fields_compared=['receipt','source','market','token','side','price','size','best_bid','best_ask'],
        native_bbo_observations=quotes, native_bbo_before_price_change=matched_quotes,
        raw_context=raw_window, labels_loaded=False, economic_result=None,
        rank7_pnl_usd=None, formal_oos_result=None,
        note='Native BBO observations carry no executable quantity. Preservation of price changes does not prove the recorder received every venue frame; do not synthesize missing depth.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-tar', required=True)
    parser.add_argument('--member', required=True)
    parser.add_argument('--strict-clob', required=True)
    parser.add_argument('--target-ms', required=True, type=float)
    parser.add_argument('--max-records', type=int, default=10000)
    args = parser.parse_args()
    print(json.dumps(audit_window(args.raw_tar, args.member, args.strict_clob,
        args.target_ms, max_records=args.max_records), indent=2, allow_nan=False))
