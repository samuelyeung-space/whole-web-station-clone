"""Development-only inlet for USD-M raw frames before first-copy deduplication.

No socket, credential, recorder mutation, subscription, order, feature or PnL.
The caller must bind route identifiers to the actual endpoint configuration and
provide an append-only writer; writer return is not proof of disk durability.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math

VERSION = 'usdm-pre-dedup-route-frames-development-v1'


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('nonfinite JSON constant')


def _uint(value):
    return type(value) is int and value >= 0


def _decimal(value):
    if type(value) not in (str, int, float):
        raise ValueError('invalid decimal type')
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid decimal') from exc
    if not number.is_finite() or number <= 0:
        raise ValueError('nonpositive or nonfinite decimal')
    # Decimal.normalize() uses the active precision and could merge distinct
    # long inputs. Remove decimal trailing zeroes without arithmetic/rounding.
    sign, digits, exponent = number.as_tuple()
    digits = list(digits)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    return ''.join(map(str, digits)) + 'e' + str(exponent)


def _classification(raw):
    """Never invent a trade from a ticker, aggregate or control frame."""
    try:
        payload = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                             parse_constant=_constant)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        return {'kind': 'unparsed', 'reason': 'invalid_utf8_or_json'}, None
    if not isinstance(payload, Mapping):
        return {'kind': 'other', 'reason': 'nonobject'}, None
    # Combined and direct forms remain distinguishable in raw bytes.
    data = payload.get('data', payload)
    if not isinstance(data, Mapping) or data.get('e') != 'trade':
        return {'kind': 'other', 'reason': 'not_native_raw_trade'}, None
    if data.get('s') != 'BTCUSDT' or not _uint(data.get('t')):
        return {'kind': 'unclassified_trade', 'reason': 'invalid_symbol_or_trade_id'}, None
    identity = ('BTCUSDT', data['t'])
    try:
        canonical = json.dumps(data, sort_keys=True, separators=(',', ':'),
                               ensure_ascii=True, allow_nan=False).encode('ascii')
    except (ValueError, TypeError, OverflowError):
        return {'kind': 'unparsed', 'reason': 'nonfinite_or_unserializable_payload'}, None
    result = {'kind': 'native_raw_trade', 'symbol': identity[0],
              'trade_id': identity[1], 'payload_sha256': hashlib.sha256(canonical).hexdigest(),
              'economics_valid': False}
    try:
        if not _uint(data.get('T')) or not _uint(data.get('E')):
            raise ValueError('invalid source timestamps')
        if type(data.get('m')) is not bool:
            raise ValueError('missing or nonboolean native maker flag')
        economics = (data['T'], _decimal(data.get('p')), _decimal(data.get('q')), data['m'])
    except (ValueError, TypeError, OverflowError):
        result['reason'] = 'invalid_economics_retained'
        # Preserve the original fields through raw bytes; zero fields do not
        # acquire an inferred positive price/quantity or aggressor volume.
        return result, identity
    result['economics_valid'] = True
    result['economic_signature'] = list(economics)
    return result, identity


class RouteFrameObserver:
    """Retain every frame; comparisons become known only at later receipt.

    This does not feed RecorderFuturesFlowTape or repair its failure latch.
    A later valid duplicate cannot backfill a previous decision. No ID sequence,
    pair of routes, lifecycle event or validity flag certifies coverage.
    """
    def __init__(self, *, capture_id, clock_id, routes, split):
        if split != 'development':
            raise ValueError('development split required')
        if not all(isinstance(x, str) and x.strip() for x in (capture_id, clock_id)):
            raise ValueError('capture and receive-clock identities required')
        if not isinstance(routes, Mapping) or not routes or any(
                not isinstance(k, str) or not k or not isinstance(v, str) or not v
                for k, v in routes.items()):
            raise ValueError('explicit route to endpoint mapping required')
        self.capture_id, self.clock_id, self.routes = capture_id, clock_id, dict(routes)
        self.frame_index, self.last_recv = 0, -math.inf
        self.connection_sequences, self.first = {}, {}
        self.failed = False

    def record_and_forward(self, raw, *, recv_ms, route_id, conn_id, epoch,
                           sequence, writer, forward):
        """Write envelope, then pass the exact original object to the callback.

        Sink failure latches the observer and prevents the callback. Callback
        failure does not remove a successfully written envelope. Source clocks
        are not substituted for recv_ms, and no network operation is performed.
        """
        if self.failed:
            raise ValueError('observer failed closed')
        if type(raw) not in (bytes, str):
            raise ValueError('original UTF-8 text or raw bytes required')
        if type(recv_ms) not in (int, float) or not math.isfinite(recv_ms) or recv_ms < self.last_recv:
            raise ValueError('nondecreasing finite receive time required')
        if recv_ms < 0 or route_id not in self.routes or not all(
                _uint(x) for x in (conn_id, epoch, sequence)) or epoch < 1:
            raise ValueError('valid route/connection/epoch/sequence required')
        connection = (route_id, conn_id, epoch)
        prior_sequence = self.connection_sequences.get(connection)
        if prior_sequence is not None and sequence != prior_sequence + 1:
            raise ValueError('connection sequence must advance exactly once')
        raw_bytes = raw.encode('utf-8') if isinstance(raw, str) else raw
        classification, identity = _classification(raw_bytes)
        row = {'schema': VERSION, 'split': 'development', 'capture_id': self.capture_id,
               'clock_id': self.clock_id, 'frame_index': self.frame_index,
               'recv_ms': recv_ms, 'route_id': route_id, 'endpoint': self.routes[route_id],
               'conn_id': conn_id, 'connection_epoch': epoch, 'connection_sequence': sequence,
               'raw_sha256': hashlib.sha256(raw_bytes).hexdigest(), 'raw_bytes': len(raw_bytes),
               'raw_base64': base64.b64encode(raw_bytes).decode('ascii'),
               'classification': classification, 'comparison': None,
               'coverage_certified': False, 'features_admitted': 0, 'pnl_usd': None}
        if identity in self.first:
            first = self.first[identity]
            previous = first['classification']
            same_payload = previous['payload_sha256'] == classification['payload_sha256']
            both_valid = previous['economics_valid'] and classification['economics_valid']
            same_economics = both_valid and previous['economic_signature'] == classification['economic_signature']
            row['comparison'] = {'first_frame_index': first['frame_index'],
                                 'first_recv_ms': first['recv_ms'],
                                 'available_recv_ms': recv_ms,
                                 'same_payload': same_payload,
                                 'same_valid_economics': bool(same_economics),
                                 'status': 'repeat' if same_payload else
                                     ('metadata_changed' if same_economics else 'unresolved_payload_conflict'),
                                 'earlier_frame_or_decision_rewritten': False}
        # Give the sink detached data: it cannot mutate the observer's first
        # receipt and thereby rewrite the meaning of future comparisons.
        encoded = json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n'
        try:
            writer(encoded)
        except BaseException:
            self.failed = True
            raise
        if identity is not None and identity not in self.first:
            self.first[identity] = {'classification': deepcopy(classification),
                                    'frame_index': self.frame_index, 'recv_ms': recv_ms}
        self.connection_sequences[connection] = sequence
        self.last_recv = recv_ms
        self.frame_index += 1
        forward(raw, recv_ms, conn_id)
        return row
