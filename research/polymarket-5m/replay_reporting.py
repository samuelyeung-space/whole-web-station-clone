"""Auditable reports for the offline receipt-clock replay.

``write_reports(out_dir, result, coverage_manifest, registration)`` writes one
run's reports. Candidate/latency/scenario ledgers are never added together.
Market dates use the market start in UTC, never decision or settlement dates.
No inferred marking-to-market, outcome labels, queue fills, or missing PnL.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any

DAY_MS = 86_400_000
MISSING_EXECUTION_MARKERS = (
    'missing_book', 'missing_or_invalid_fee', 'missing_asks', 'invalid_asks',
    'fee_changed_after_decision', 'invalid_min_order_size_metadata',
    'missing_required_constraints', 'invalid_constraint_timestamp',
    'invalid_up_tick_size_metadata', 'invalid_down_tick_size_metadata',
)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _date(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat() if _number(ms) else 'unknown'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def _dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def _write_csv(path, rows, preferred=()):
    names = list(dict.fromkeys(list(preferred) + [key for row in rows for key in row]))
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction='raise')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: (_json(value) if isinstance(value, (dict, list, tuple)) else value)
                             for key, value in row.items()})


def _market_metadata(manifest, registration, result):
    """Accept detailed coverage from the CLI while supporting older manifests."""
    markets = {}
    for source in (result.get('dataset') or {}, registration, manifest, manifest.get('coverage') or {}):
        for key in ('market_metadata', 'markets', 'registered_markets', 'market_rows'):
            values = source.get(key)
            if isinstance(values, dict):
                values = [dict(value, market_id=mid) if isinstance(value, dict)
                          else {'market_id': mid, 'start_ms': value} for mid, value in values.items()]
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, dict) and value.get('market_id'):
                    mid = str(value['market_id'])
                    markets[mid] = {**markets.get(mid, {}), **value}
        for key in ('market_start_ms', 'by_market_start_ms'):
            values = source.get(key)
            if isinstance(values, dict):
                for mid, value in values.items():
                    if _number(value):
                        markets[str(mid)] = {**markets.get(str(mid), {}), 'market_id': str(mid), 'start_ms': value}
    return markets


def _coverage_days(manifest, markets):
    """288 is the calendar-slot denominator, not proof of recorded markets."""
    manifest = manifest.get('coverage') or manifest
    days = {}
    for key in ('daily', 'days', 'daily_coverage'):
        values = manifest.get(key)
        if isinstance(values, dict):
            values = [dict(value, utc_date=date) if isinstance(value, dict)
                      else {'utc_date': date, 'recorded_markets': value} for date, value in values.items()]
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict):
                    date = value.get('utc_date', value.get('date_utc', value.get('date')))
                    if date:
                        days[str(date)] = {**days.get(str(date), {}), **value}
    interval = manifest.get('requested_interval_ms')
    start = manifest.get('requested_start_ms')
    end = manifest.get('requested_end_ms')
    if isinstance(interval, (list, tuple)) and len(interval) == 2:
        start, end = interval
    if _number(start) and _number(end) and start < end:
        for offset in range(int(start // DAY_MS), int((end - 1) // DAY_MS) + 1):
            days.setdefault(_date(offset * DAY_MS), {})
    by_day = defaultdict(list)
    for market in markets.values():
        by_day[_date(market.get('start_ms'))].append(market)
    for date in set(days) | set(by_day):
        rows = by_day[date]
        day = days.setdefault(date, {})
        day.setdefault('expected_calendar_slots', day.get('expected_slots', 288 if date != 'unknown' else None))
        day.setdefault('registered_markets', len(rows))
        # Preserve supplied coverage classifications. Having metadata alone does
        # not imply book coverage, a complete market, or recording health.
        if rows and any('fully_recorded' in row for row in rows):
            day.setdefault('fully_recorded_markets', sum(row.get('fully_recorded') is True for row in rows))
        day.setdefault('coverage_complete', False)
    return days


def _aggregate(rows, parent_status=None):
    statuses = Counter(row.get('status', 'unknown') for row in rows)
    filled = [row for row in rows if (row.get('filled_shares') or 0) > 1e-9]
    settled = [row for row in filled if row.get('settled') is True and _number(row.get('net_pnl_usd'))]
    quantity = sum(row.get('filled_shares', 0) or 0 for row in filled)
    realized = sum(row['net_pnl_usd'] for row in settled)
    incomplete = any(row.get('status') == 'incomplete_match_horizon' for row in rows) or len(settled) != len(filled)
    missing = any(any(marker in status for marker in MISSING_EXECUTION_MARKERS) for status in statuses)
    blocked = bool(parent_status and parent_status.startswith('blocked_'))
    if blocked:
        status, pnl = parent_status, None
    elif not rows:
        status, pnl = 'no_signal_observed', None
    elif missing:
        status, pnl = 'incomplete_missing_execution_inputs', None
    elif incomplete:
        status, pnl = 'incomplete_unresolved_or_truncated', None
    elif filled:
        status, pnl = 'simulated_complete', realized
    else:
        status, pnl = 'evaluated_no_fills', 0.0
    rejected = {key: count for key, count in statuses.items()
                if key not in ('filled_full', 'filled_partial', 'scheduled')}
    return {
        'status': status, 'first_signals': len(rows), 'attempts': len(rows),
        'submitted_orders': sum(row.get('submitted') is True for row in rows),
        'filled_orders': len(filled), 'full_fills': statuses['filled_full'],
        'partial_fills': statuses['filled_partial'], 'failed_or_unfilled_attempts': len(rows) - len(filled),
        'submitted_but_unfilled_orders': sum(row.get('submitted') is True and (row.get('filled_shares') or 0) <= 1e-9 for row in rows),
        'not_submitted_attempts': sum(row.get('submitted') is not True for row in rows),
        'no_fill_orders': sum(count for status, count in statuses.items() if status.startswith('no_fill')),
        'rejection_and_no_fill_reasons': rejected, 'status_counts': dict(statuses),
        'signal_markets': len({row['market_id'] for row in rows}),
        'filled_markets': len({row['market_id'] for row in filled}),
        'filled_shares': quantity, 'settled_positions': len(settled),
        'unresolved_positions': len(filled) - len(settled),
        'fees_usd': sum(row.get('fees_usd', 0) or 0 for row in rows),
        'extra_cost_usd': sum(row.get('extra_cost_usd', 0) or 0 for row in rows),
        'cost_usd': sum(row.get('cost_usd', 0) or 0 for row in rows),
        'realized_pnl_usd': realized if rows and not blocked else None,
        'net_pnl_usd': pnl,
        'net_pnl_per_filled_share_usd': pnl / quantity if pnl is not None and quantity else None,
    }


def _cash_rows(result, attempts):
    """Prefer actual ordered engine events; otherwise expose snapshots/deltas.

    Older engines did not record the interleaving of identical timestamps.
    Their fallback rows must not imply a reconstructed running account balance.
    """
    events = result.get('cash_events', result.get('cash_ledger'))
    if isinstance(events, list):
        return [dict(row, report_cash_basis='engine_recorded_event') for row in events], 'engine_recorded_event'
    rows = []
    for index, attempt in enumerate(attempts):
        key = {'candidate_id': attempt['candidate_id'], 'latency_ms': attempt['latency_ms'],
               'attempt_id': attempt.get('attempt_id', index), 'market_id': attempt['market_id']}
        def add(event_type, when, delta=0.0, reserve=0.0, locked=0.0, pnl=0.0, cash=None, phase=0):
            rows.append({**key, 'event_ms': when, 'event_utc_date': _date(when), 'event_type': event_type,
                         'cash_delta_usd': delta, 'reserved_delta_usd': reserve,
                         'locked_payout_delta_usd': locked, 'realized_pnl_delta_usd': pnl,
                         'engine_reported_cash_snapshot_usd': cash, 'phase': phase,
                         'report_cash_basis': 'attempt_snapshots_and_deltas_no_inferred_running_balance'})
        reservation = attempt.get('reserved_usd_at_decision', 0) or 0
        add('decision_reservation' if attempt.get('submitted') else 'decision_rejected',
            attempt.get('decision_ms'), reserve=reservation,
            cash=attempt.get('cash_before_decision'))
        if attempt.get('submitted'):
            add('match_debit_and_reservation_release', attempt.get('match_ms'),
                delta=-(attempt.get('cost_usd', 0) or 0), reserve=-reservation,
                cash=attempt.get('cash_after_match'), phase=1)
        if attempt.get('settled') is True:
            payout = attempt.get('payout_usd', 0) or 0
            settlement = attempt.get('settlement_ms')
            due = attempt.get('cash_release_due_ms', settlement)
            delayed = _number(settlement) and _number(due) and due > settlement and payout > 0
            add('settlement_recognition', settlement, delta=0 if delayed else payout,
                locked=payout if delayed else 0, pnl=attempt.get('net_pnl_usd', 0) or 0,
                cash=attempt.get('cash_after_settlement'), phase=2)
            if delayed and attempt.get('cash_released') is True:
                add('payout_cash_release', due, delta=payout, locked=-payout,
                    cash=attempt.get('cash_after_release'), phase=3)
    collisions = Counter((row['candidate_id'], row['latency_ms'], row['event_ms']) for row in rows)
    rows.sort(key=lambda row: (row['candidate_id'], row['latency_ms'],
                             row['event_ms'] if _number(row['event_ms']) else math.inf,
                             row['phase'], str(row['attempt_id'])))
    for row in rows:
        row['same_timestamp_order_unobserved'] = collisions[(row['candidate_id'], row['latency_ms'], row['event_ms'])] > 1
        row.pop('phase')
    return rows, 'attempt_snapshots_and_deltas_no_inferred_running_balance'


def write_reports(out_dir, result: dict, coverage_manifest: dict | None, registration: dict | None) -> dict[str, str]:
    """Write reports; return their absolute file paths, without mutating inputs.

    Required ``result`` fields are ``summary`` and ``attempts`` from
    joint_probability_execution.replay_events. Detailed market coverage is
    optional, but missing market start dates remain explicitly unknown.
    """
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest, registration = coverage_manifest or {}, registration or {}
    summaries = result.get('summary')
    attempts = result.get('attempts')
    if not isinstance(summaries, list) or not isinstance(attempts, list):
        raise ValueError('result must retain summary and attempts lists before write_reports')
    markets = _market_metadata(manifest, registration, result)
    coverage = _coverage_days(manifest, markets)
    groups = defaultdict(list)
    for attempt in attempts:
        if 'candidate_id' not in attempt or 'latency_ms' not in attempt or 'market_id' not in attempt:
            raise ValueError('attempt requires candidate_id, latency_ms and market_id')
        groups[(str(attempt['candidate_id']), attempt['latency_ms'])].append(attempt)
    metrics, daily, market_rows, differences = [], [], [], []
    summary_keys = [(str(row['candidate_id']), row['latency_ms']) for row in summaries]
    if len(summary_keys) != len(set(summary_keys)):
        raise ValueError('duplicate candidate/latency rows: write separate report directories for stress scenarios')
    if set(groups) - set(summary_keys):
        raise ValueError('attempts exist without their candidate/latency summary')
    for summary in summaries:
        key = str(summary['candidate_id']), summary['latency_ms']
        rows = groups[key]
        aggregate = _aggregate(rows, summary.get('status'))
        # The engine's status/PnL are authoritative, including blocked/no-signal
        # outcomes not recoverable from attempts alone. Audit discrepancies.
        for field in ('attempts', 'submitted_orders', 'filled_shares', 'fees_usd',
                      'full_fills', 'partial_fills', 'settled_positions', 'unresolved_positions'):
            if field in summary and _number(summary[field]) and abs(summary[field] - aggregate[field]) > 1e-7:
                differences.append({'candidate_id': key[0], 'latency_ms': key[1], 'field': field,
                                    'engine': summary[field], 'attempts': aggregate[field]})
        if _number(summary.get('net_pnl_usd')) and _number(aggregate['net_pnl_usd']) and abs(summary['net_pnl_usd'] - aggregate['net_pnl_usd']) > 1e-7:
            differences.append({'candidate_id': key[0], 'latency_ms': key[1], 'field': 'net_pnl_usd',
                                'engine': summary['net_pnl_usd'], 'attempts': aggregate['net_pnl_usd']})
        metrics.append({**aggregate, **summary,
                        'first_signals': aggregate['first_signals'],
                        'filled_orders': aggregate['filled_orders'],
                        'failed_or_unfilled_attempts': aggregate['failed_or_unfilled_attempts'],
                        'drawdown_basis': summary.get('drawdown_basis', 'realized_only_no_mark_to_market'),
                        'capital_scope': result.get('capital_scope', registration.get('cash_scope', 'unspecified_do_not_sum'))})
        by_day, by_market = defaultdict(list), defaultdict(list)
        for row in rows:
            start = markets.get(row['market_id'], {}).get('start_ms', row.get('market_start_ms'))
            by_day[_date(start)].append(row)
            by_market[row['market_id']].append(row)
        for date in sorted(set(coverage) | set(by_day)):
            info = coverage.get(date, {})
            aggregate_day = _aggregate(by_day[date], summary.get('status'))
            daily.append({'candidate_id': key[0], 'latency_ms': key[1], 'market_start_utc_date': date,
                          'expected_calendar_slots': info.get('expected_calendar_slots', 288 if date != 'unknown' else None),
                          'registered_markets': info.get('registered_markets'),
                          'fully_recorded_markets': info.get('fully_recorded_markets', info.get('fully_covered_markets')),
                          'recorded_markets': info.get('recorded_markets', info.get('observed_markets')),
                          'coverage_complete': info.get('coverage_complete', False),
                          'recorded_span_slots': info.get('recorded_span_slots'),
                          'complete_span_slots': info.get('complete_span_slots'),
                          'data_present_slots': info.get('data_present_slots'),
                          'missing_span_slots': info.get('missing_span_slots'),
                          'observed_seconds': info.get('observed_seconds'),
                          'decision_markets': info.get('decision_markets'),
                          'book_markets': info.get('book_markets'),
                          'outcome_markets': info.get('outcome_markets'),
                          'recording_gaps_ms': info.get('gaps_ms'),
                          'coverage_notes': info.get('notes'), **aggregate_day})
        for mid in sorted(set(markets) | set(by_market)):
            metadata = markets.get(mid, {})
            market_rows.append({'candidate_id': key[0], 'latency_ms': key[1], 'market_id': mid,
                                'market_start_ms': metadata.get('start_ms'),
                                'market_end_ms': metadata.get('end_ms'),
                                'market_start_utc_date': _date(metadata.get('start_ms')),
                                'fully_recorded': metadata.get('fully_recorded'),
                                **_aggregate(by_market[mid], summary.get('status'))})
    cash, cash_basis = _cash_rows(result, attempts)
    paths = {name: str(out / filename) for name, filename in {
        'metrics': 'metrics.csv', 'daily': 'daily.csv', 'markets': 'markets.csv',
        'cash_ledger': 'cash_ledger.csv', 'attempts': 'attempts.jsonl.gz',
        'report': 'report.md', 'audit': 'audit.json'}.items()}
    preferred = ('candidate_id', 'latency_ms', 'status', 'first_signals', 'attempts', 'submitted_orders',
                 'filled_orders', 'full_fills', 'partial_fills', 'failed_or_unfilled_attempts',
                 'filled_shares', 'fees_usd', 'extra_cost_usd', 'net_pnl_usd', 'net_pnl_per_filled_share_usd')
    _write_csv(Path(paths['metrics']), metrics, preferred)
    _write_csv(Path(paths['daily']), daily, ('candidate_id', 'latency_ms', 'market_start_utc_date', 'expected_calendar_slots'))
    _write_csv(Path(paths['markets']), market_rows, ('candidate_id', 'latency_ms', 'market_id', 'market_start_utc_date'))
    _write_csv(Path(paths['cash_ledger']), cash, ('candidate_id', 'latency_ms', 'event_sequence', 'event_ms', 'event_type'))
    # gzip mtime=0 keeps identical attempts reproducible across reruns.
    with Path(paths['attempts']).open('wb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as handle:
            for row in attempts:
                handle.write((_json(row) + '\n').encode('utf-8'))
    def fmt(value, digits=5):
        return 'null（未完成/不可计算）' if value is None else (f'{value:.{digits}f}' if isinstance(value, float) else str(value))
    scope = result.get('capital_scope', registration.get('cash_scope', '未指定；不得合并余额或收益'))
    lines = ['# 回放结果', '',
             '这是历史录制上的开发仿真。盘口深度仅给出成交容量上界，不代表真实成交。', '',
             f'资金口径：{scope}。不同延迟和压力情景是相关对照，不能相加。',
             '每天理论为 288 个五分钟时段；七天为 2016 个时段。下表覆盖数必须依据录制清单，信号数和成交数不能替代覆盖数。',
             '日归属按市场开始时间的 UTC 日期；资金回收按官方结果本地获知时间及登记的回收延迟。',
             '回撤仅为已实现收益回撤，未计算逐时盯市回撤。缺失或未结算结果保持 null；CSV 空白表示 null。', '',
             '| UTC 市场日 | 理论时段 | 注册市场 | 录制跨度触及 | 完整跨度时段 | 录制秒数 |',
             '|---|---:|---:|---:|---:|---:|']
    for date, info in sorted(coverage.items()):
        lines.append('| ' + ' | '.join([date, fmt(info.get('expected_calendar_slots')),
            fmt(info.get('registered_markets')), fmt(info.get('recorded_span_slots')),
            fmt(info.get('complete_span_slots')), fmt(info.get('observed_seconds'))]) + ' |')
    if not coverage:
        lines.append('| 未提供覆盖清单 | null | null | null | null | null |')
    lines += ['', '录制跨度只说明时间区间覆盖，不能证明完整健康的双边盘口或逐笔成交；未提供的覆盖值为 null。', '',
             '| 候选 | 总延迟 ms | 首信号/尝试 | 已发单 | 成交订单 | 全成/部分 | 失败或未成 | 成交份数 | 费用 $ | 净收益 $ | 每份净收益 $ |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in metrics:
        lines.append('| ' + ' | '.join([str(row['candidate_id']), str(row['latency_ms']), str(row['first_signals']),
            str(row['submitted_orders']), str(row['filled_orders']), f"{row['full_fills']}/{row['partial_fills']}",
            str(row['failed_or_unfilled_attempts']), fmt(row['filled_shares']), fmt(row['fees_usd']),
            fmt(row.get('net_pnl_usd')), fmt(row.get('net_pnl_per_filled_share_usd'))]) + ' |')
    lines += ['', '逐日覆盖与完整尝试漏斗见 daily.csv；逐场无信号、拒单及未结算状态见 markets.csv。',
              '所有尝试保留在 attempts.jsonl.gz；拒单/零成交原因和缺特征行数保留在 metrics.csv 与 audit.json。',
              f'现金流水来源：{cash_basis}。旧引擎同毫秒事件顺序不可恢复时明确标记，只展示已记录快照与现金变动，不虚构连续余额。',
              f'尝试账与引擎摘要核对差异数：{len(differences)}。', '',
              '正式样本外结果：null。确认合格策略：0；本报告不作收益承诺。', '']
    Path(paths['report']).write_text('\n'.join(lines), encoding='utf-8')
    audit = {key: value for key, value in result.items() if key not in ('attempts', 'cash_events', 'cash_ledger')}
    audit.update(report_schema_version='receipt_replay_reporting_v1', coverage_manifest=manifest,
                 registration=registration, reporting={
                     'attempt_row_count': len(attempts), 'summary_row_count': len(metrics),
                     'candidate_latency_ledgers_are_nonadditive': True, 'daily_clock': 'market_start_utc',
                     'cash_clock': 'engine_event_time_or_explicit_attempt_snapshots', 'cash_basis': cash_basis,
                     'no_mark_to_market': True, 'reconciliation_differences': differences,
                     'market_start_unknown_attempts': sum(_date(markets.get(row['market_id'], {}).get('start_ms', row.get('market_start_ms'))) == 'unknown' for row in attempts),
                     'files': {key: {'filename': Path(path).name,
                                     'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                                     'bytes': Path(path).stat().st_size}
                               for key, path in paths.items() if key != 'audit'}})
    _dump(Path(paths['audit']), audit)
    return paths
