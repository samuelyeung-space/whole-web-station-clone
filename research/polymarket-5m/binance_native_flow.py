"""Offline Binance spot trade bridge into the existing 15/60s flow engine.

No subscriptions, credentials, deployment, trading or backtesting. Native
payloads and local receipt timestamps must have been retained by the recorder.
The four-field legacy price archive cannot be repaired by this adapter.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import math
from collections.abc import Mapping

from historical_flow_receipts import CoverageInterval, ReceiptFlowTape

VERSION = 'binance-native-flow-v1'
REFERENCE = 'https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams'


def exact_uint(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(name + ' requires an exact integer or decimal string')
    text = str(value)
    if not text or not text.isascii() or not text.isdecimal():
        raise ValueError(name + ' must be an unsigned decimal integer')
    return int(text)


def positive_decimal(value, name):
    # Preserve the original decimal text in raw_payload. Float conversion is
    # only for the established feature engine, never IDs or source timestamps.
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('missing or invalid ' + name)
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid ' + name) from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(name + ' must be positive and finite')
    result = float(number)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(name + ' outside supported numeric range')
    return result


def native_receipt(payload, *, local_recv_ms, capture_run_id, stream_kind,
                   time_unit, symbol='BTCUSDT'):
    """Normalize exactly one explicitly routed native spot stream receipt.

    ``m=True`` means buyer is maker, hence seller is aggressor. Missing m
    stays unknown. ``M`` is an ignored native field and is NOT an alternative.
    Milliseconds must be declared; this version never guesses units.
    """
    if time_unit != 'MILLISECOND':
        raise ValueError('only explicitly declared MILLISECOND input is supported')
    if stream_kind not in ('trade', 'aggTrade'):
        raise ValueError('unsupported native stream kind')
    if not isinstance(payload, Mapping):
        raise ValueError('native payload must be an object')
    packet = deepcopy(dict(payload))
    data = packet
    if 'data' in packet or 'stream' in packet:
        if packet.get('stream') != symbol.lower() + '@' + stream_kind:
            raise ValueError('combined stream routing mismatch')
        data = packet.get('data')
        if not isinstance(data, Mapping):
            raise ValueError('missing combined native payload')
    if data.get('e') != stream_kind or data.get('s') != symbol:
        raise ValueError('event or symbol routing mismatch; do not mix trade and aggTrade')
    if isinstance(local_recv_ms, bool) or not isinstance(local_recv_ms, (int, float)) or not math.isfinite(local_recv_ms) or local_recv_ms < 0:
        raise ValueError('local receipt clock is required, in milliseconds')
    if not isinstance(capture_run_id, str) or not capture_run_id.strip():
        raise ValueError('capture_run_id is required')
    trade_id = exact_uint(data.get('t' if stream_kind == 'trade' else 'a'), 'trade ID')
    trade_time = exact_uint(data.get('T'), 'T')
    event_time = exact_uint(data.get('E'), 'E')
    # Explicit plausibility guard, not an estimate of latency or clock offset.
    if max(trade_time, event_time) > local_recv_ms + 10000:
        raise ValueError('source clock over 10 seconds ahead of receipt')
    price = positive_decimal(data.get('p'), 'p')
    quantity = positive_decimal(data.get('q'), 'q')
    maker = data.get('m')
    if maker is not None and type(maker) is not bool:
        raise ValueError('m must be a native boolean or absent')
    side = ('sell' if maker else 'buy') if maker is not None else None
    first = last = None
    if stream_kind == 'aggTrade':
        first, last = exact_uint(data.get('f'), 'f'), exact_uint(data.get('l'), 'l')
        if first > last:
            raise ValueError('aggregate first trade ID exceeds last trade ID')
    row = {'exchange':'binance','instrument':symbol,'trade_id':stream_kind+':'+str(trade_id),
           '_run_id':capture_run_id,'trade_ts_ms':trade_time,'recv_ts_ms':local_recv_ms,
           'price':price,'size':quantity,'taker_side':side}
    return {'version':VERSION,'row':row,'raw_payload':packet,'source_event_ms':event_time,
            'source_trade_ms':trade_time,'local_recv_ms':local_recv_ms,
            'time_unit':time_unit,'stream_kind':stream_kind,'raw_buyer_is_maker':maker,
            'price_decimal':str(Decimal(str(data['p']))),
            'quantity_decimal':str(Decimal(str(data['q']))),
            'side_provenance':'native_m_buyer_is_maker' if maker is not None else 'missing_native_m',
            'first_raw_trade_id':first,'last_raw_trade_id':last,
            'capture_clock':'provided recorder local receipt; not source or current-machine latency'}


class NativeFlowTape:
    """One stream and symbol per tape; reuse existing coverage and flow logic.

    Any parsing/append error permanently blocks this tape's future decisions.
    A new recording epoch must instantiate a new tape, with fresh coverage.
    This prevents callers from catching malformed rows and silently skipping
    their volume. Previously computed decisions are not rewritten.
    """
    def __init__(self, *, stream_kind, time_unit, symbol='BTCUSDT'):
        if stream_kind not in ('trade','aggTrade') or time_unit != 'MILLISECOND':
            raise ValueError('explicit supported stream and millisecond clock required')
        self.stream_kind=stream_kind; self.time_unit=time_unit; self.symbol=symbol
        self.engine=ReceiptFlowTape(exchange='binance',instrument=symbol)
        self.native_receipts=[]; self.failed_at=None; self.failure=None
        self._signatures={}; self._aggregate_ranges={}

    def add_trade(self, payload, *, local_recv_ms, capture_run_id):
        if self.failure is not None:
            raise ValueError('tape failed closed; start a new recording epoch')
        try:
            native=native_receipt(payload,local_recv_ms=local_recv_ms,capture_run_id=capture_run_id,
                stream_kind=self.stream_kind,time_unit=self.time_unit,symbol=self.symbol)
            row=native['row'];tid=row['trade_id']
            signature=(row['trade_ts_ms'],Decimal(native['price_decimal']),
                       Decimal(native['quantity_decimal']),row['taker_side'],
                       native['first_raw_trade_id'],native['last_raw_trade_id'])
            if tid in self._signatures and self._signatures[tid]!=signature:
                raise ValueError('conflicting native economic identity')
            if self.stream_kind=='aggTrade' and tid not in self._signatures:
                lo,hi=native['first_raw_trade_id'],native['last_raw_trade_id']
                if any(lo<=b and a<=hi for a,b in self._aggregate_ranges.values()):
                    raise ValueError('distinct aggregate IDs overlap raw trade ranges')
            self.engine.add_trade(row)
        except (ValueError,TypeError,OverflowError) as exc:
            # Invalid or backward receipt timestamps cannot safely locate the
            # corruption; block all future API queries, including retrospective.
            known=isinstance(local_recv_ms,(int,float)) and not isinstance(local_recv_ms,bool) and math.isfinite(local_recv_ms) and local_recv_ms>=self.engine._last_recv
            self.failed_at=local_recv_ms if known else -math.inf
            self.failure=str(exc)
            raise
        self.native_receipts.append(native);self._signatures[tid]=signature
        if self.stream_kind=='aggTrade':self._aggregate_ranges[tid]=(native['first_raw_trade_id'],native['last_raw_trade_id'])
        return native

    def add_coverage(self, interval):
        self.engine.add_coverage(interval)

    def features(self, decision_recv_ms):
        result=self.engine.features(decision_recv_ms)
        result.update(adapter_version=VERSION,stream_kind=self.stream_kind,
                      capture_clock='native Binance recorder local receipt milliseconds',
                      native_side_mapping='m=true -> SELL; m=false -> BUY; missing -> unknown')
        if self.failure is not None and decision_recv_ms>=self.failed_at:
            result['features']={k:None for k in result['features']}
            result['feature_available_ms']={k:None for k in result['feature_available_ms']}
            for audit in result['audit'].values():
                audit.update(value=None,available_ms=None,status='blocked_native_ingestion_error')
            result['ingestion_error']=self.failure
        return result
