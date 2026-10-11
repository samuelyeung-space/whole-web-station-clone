from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import reward_dust_chain_scan as chain_scan
import wallet_lead as wl
import wallet_lead_scan as scan


START = int(datetime(2026, 7, 2, tzinfo=UTC).timestamp())
TOKEN = "123"


def token() -> wl.TokenMeta:
    return wl.TokenMeta(
        day=date(2026, 7, 2),
        start_s=START,
        market="condition",
        token=TOKEN,
        side="Up",
        up_won=True,
    )


def raw_row(
    *,
    side: int = 0,
    token_id: str = TOKEN,
    taker: str = chain_scan.CTF_EXCHANGE_V2_ADDRESS,
    log_index: int = 7,
    maker_amount: str = "2500000",
    taker_amount: str = "5000000",
) -> dict:
    return {
        "timestamp": START + 70,
        "datetime": datetime.fromtimestamp(START + 70, UTC),
        "block_number": 90_000_001,
        "transaction_hash": "0x" + "ab" * 32,
        "log_index": log_index,
        "contract": chain_scan.CTF_EXCHANGE_V2_LABEL,
        "order_hash": "0x" + "cd" * 32,
        "maker": "0xleader",
        "taker": taker,
        "side": side,
        "token_id": token_id,
        "maker_amount_filled": maker_amount,
        "taker_amount_filled": taker_amount,
        "fee": "87500",
        "builder": "0x" + "00" * 32,
        "metadata": "0x" + "00" * 32,
    }


def write_parquet(path, rows):
    pq.write_table(pa.Table.from_pylist(rows), path)
    return pq.ParquetFile(path)


def test_scan_keeps_only_unique_target_aggressor_parent_buys(tmp_path):
    rows = [
        raw_row(),
        raw_row(side=1, log_index=8),
        raw_row(taker="0xmaker-child", log_index=9),
        raw_row(token_id="other", log_index=10),
    ]
    pf = write_parquet(tmp_path / "fills.parquet", rows)

    buys, stats = scan.scan_parquet(
        pf,
        {TOKEN: token()},
        capture_start_s=START,
        capture_end_s=START + 300,
    )

    assert len(buys) == 1
    assert buys[0].wallet == "0xleader"
    assert buys[0].shares == Decimal("5")
    assert buys[0].price == Decimal("0.5")
    assert stats.selected_rows == 1
    assert stats.row_groups_read == 1


def test_scan_keeps_small_first_buys_in_the_wallet_history(tmp_path):
    row = raw_row(maker_amount="400000", taker_amount="1000000")
    row["fee"] = "16800"
    parquet = write_parquet(tmp_path / "small.parquet", [row])

    buys, _stats = scan.scan_parquet(
        parquet,
        {TOKEN: token()},
        capture_start_s=START,
        capture_end_s=START + 300,
    )

    assert len(buys) == 1
    assert buys[0].shares == Decimal("1")


def test_dedupe_accepts_exact_duplicates_and_rejects_conflicts():
    buy = wl.normalise_parent_buy(raw_row(), {TOKEN: token()})
    assert buy is not None
    assert scan.dedupe_parent_buys([buy, buy]) == [buy]

    conflicting = replace(
        buy,
        price=Decimal("0.51"),
        fee=wl.charged_taker_fee(buy.shares, Decimal("0.51")),
    )
    with pytest.raises(ValueError, match="conflicting duplicate"):
        scan.dedupe_parent_buys([buy, conflicting])


def test_period_boundaries_are_fixed_before_results():
    assert scan.period_for_day(date(2026, 7, 15)) == "discovery"
    assert scan.period_for_day(date(2026, 7, 16)) == "confirmation"
    assert scan.period_for_day(date(2026, 8, 16)) == "confirmation"
    assert scan.period_for_day(date(2026, 8, 17)) == "locked"


def test_summary_counts_kills_as_zero_pnl_attempts():
    rows = pd.DataFrame(
        [
            {
                "day": "2026-08-17",
                "period": "locked",
                "variant": "base",
                "horizon_ms": 400,
                "market_id": "a",
                "filled": True,
                "filled_shares": 5.0,
                "price": 0.40,
                "fee_per_share": 0.0168,
                "won": 1.0,
                "pnl": 2.916,
            },
            {
                "day": "2026-08-17",
                "period": "locked",
                "variant": "base",
                "horizon_ms": 400,
                "market_id": "b",
                "filled": False,
                "filled_shares": 0.0,
                "price": None,
                "fee_per_share": 0.0,
                "won": 0.0,
                "pnl": 0.0,
            },
        ]
    )

    summary = scan.summarize(rows)
    row = summary.iloc[0]

    assert row["signals"] == 2
    assert row["fills"] == 1
    assert row["fill_rate"] == pytest.approx(0.5)
    assert row["net_pnl"] == pytest.approx(2.916)
    assert row["net_ev_per_attempted_share"] == pytest.approx(2.916 / 10)


def test_unfinalized_chain_source_can_never_promote():
    summary = pd.DataFrame(
        [
            {
                "period": period,
                "variant": "base",
                "horizon_ms": horizon,
                "fills": 100,
                "net_ev_per_attempted_share": 0.1,
                "day_cluster_lower_99": 0.05,
                "corrected_p": 0.001,
            }
            for period in ("confirmation", "locked")
            for horizon in scan.HORIZONS
        ]
    )

    passed, reason = scan.promotion_verdict(summary, chain_finalized=False)

    assert not passed
    assert "finality" in reason


def test_book_manifest_requires_and_preserves_every_frozen_day():
    lines = ["# file sha256 bytes"]
    for offset in range(scan.DAYS):
        day = scan.START_DAY + timedelta(days=offset)
        lines.append(
            f"market_parquet_{day}.tar.gz  {'ab' * 32}  {600_000_000 + offset}"
        )

    inventory = scan.parse_book_manifest("\n".join(lines))

    assert len(inventory) == scan.DAYS
    assert inventory[scan.START_DAY].sha256 == "ab" * 32
    assert inventory[scan.FINAL_DAY].day == scan.FINAL_DAY

    with pytest.raises(ValueError, match="book manifest misses"):
        scan.parse_book_manifest("\n".join(lines[:-1]))


def _book_frame(target_ms: int, *, live: bool) -> pd.DataFrame:
    if live:
        times = [target_ms - 1_000, target_ms, target_ms + 1_000]
        sizes = [5.0, 6.0, 7.0]
    else:
        times = [target_ms - 3_000, target_ms, target_ms + 3_000]
        sizes = [5.0, 5.0, 6.0]
    return pd.DataFrame(
        {
            "timestamp_ms": times,
            "market_id": ["condition"] * 3,
            "lifecycle_state": ["active"] * 3,
            "up_best_bid": [0.39] * 3,
            "up_best_ask": [0.40] * 3,
            "down_best_bid": [0.59] * 3,
            "down_best_ask": [0.60] * 3,
            "up_ask_size": sizes,
            "down_ask_size": [5.0] * 3,
            "up_bid_size": [5.0] * 3,
            "down_bid_size": [5.0] * 3,
            "observed_halt_flag": [False] * 3,
        }
    )


def test_snapshot_freshness_needs_real_changes_before_and_after_target():
    target_ms = (START + 70) * 1_000

    live = scan._snapshot_at(_book_frame(target_ms, live=True), target_ms)
    stale = scan._snapshot_at(_book_frame(target_ms, live=False), target_ms)

    assert live[0].fresh
    assert live[0].up_ask_size == Decimal("6.0")
    assert not stale[0].fresh


def test_empty_book_day_keeps_signals_as_no_snapshot_attempts():
    day = date(2026, 7, 16)
    market_start = int(datetime(2026, 7, 16, tzinfo=UTC).timestamp()) + 600
    meta = wl.TokenMeta(day, market_start, "condition", TOKEN, "Up", True)
    down = wl.TokenMeta(day, market_start, "condition", "456", "Down", True)
    event_row = raw_row()
    event_row.update(
        {
            "timestamp": market_start + 70,
            "maker": "0xleader",
            "maker_amount_filled": "400000",
            "taker_amount_filled": "1000000",
            "fee": "16800",
        }
    )
    event = wl.normalise_parent_buy(event_row, {TOKEN: meta, "456": down})
    history = [
        wl.WalletObservation(
            day=date(2026, 7, 2) + timedelta(days=index % 13),
            wallet="0xleader",
            market=f"prior-{index}",
            timestamp_s=START + index,
            price=Decimal("0.1"),
            shares=Decimal("1"),
            settlement=Decimal(1),
        )
        for index in range(20)
    ]

    rows, selected = scan.replay_day(day, [event], history, pd.DataFrame())

    assert len(selected) == 1
    assert len(rows) == 6
    assert {row["reason"] for row in rows} == {"no_snapshot"}
    assert not any(row["filled"] for row in rows)
