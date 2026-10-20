# Optional order-acknowledgement cash stress

`paired_leg_inventory.py` can now distinguish send, match (`arrival_ms`) and
acknowledgement receipt (`ack_ms`) for account reservations. This is an explicit
conservative account-policy stress, **not** evidence of a venue collateral rule,
a measured latency distribution, or actual fill probability.

Enable both `reserve_cash_at_send=True` and `reserve_until_ack=True`. Every intent
must have an integer `send_ms` and `ack_ms >= arrival_ms`. Missing, boolean,
fractional, string or regressed acknowledgement clocks fail closed. An absent
acknowledgement is not imputed from match time. A pre-fixed hypothetical delay
may be supplied only when labelled as a stress, never as observed execution.

At send, the existing worst-case limit-price/fee/cost cash is reserved. At match,
the actual debit is paid, and any unused reservation remains unavailable until
the acknowledgement. This includes price rejection, zero displayed liquidity,
full-or-zero rejection and partial FAK. An order rejected locally for inadequate
send-time cash produces no venue acknowledgement or fill. Settlement and payout
spendability remain separate from acknowledgement: an acknowledgement can occur
after settlement without reopening exposure or changing the settlement clock.

At equal timestamps the conservative order is settlement, cash release, sends,
arrivals, acknowledgements; order identifiers break ties within one kind. A send
cannot reuse a residual released by an acknowledgement at that same timestamp.
This convention is deliberately not an assertion about unknown sub-millisecond
venue event order.
Restricting cash can skip either winning or losing orders; this scenario is not
a mathematical lower bound on PnL and must not be selected by its profitability.

The opt-in result is schema 8, adds acknowledgement counts and peak pending
reservation cash, and audits consumed/retained/released reservations per event.
Default callers retain the schema-7 complete JSON surface. No intent selection,
strategy parameter, frozen protocol, production code path, deployment or actual
order is changed. Existing snapshots still provide only fill/capacity upper
bounds; private acknowledgements, queues and actual fills are not manufactured.

## Verification and adoption

Run the 13 new standard-library tests:

```
cd research/polymarket-5m
python3 -B -m unittest test_order_ack_reservations -v
```

They include 100 seeded random ten-order cash/reservation conservation paths.
Small-cash boundary fixtures are not the $1,000 ranking configuration.
Separately, 1,000 synthetic configurations compare the complete legacy-default
JSON against the parent module; this is default-output parity, not financial
validation. The existing pytest-shaped regression functions can also be run as
direct assertions using a small compatibility harness when pytest is absent;
that must not be described as a pytest run.

All inputs above are synthetic engineering fixtures: zero new candidates,
historical replays, OOS observations, financial PnL or ranking admissions.

This option is not bound into any current rank7 protocol. Before formal use,
freeze the exact implementation/input/scenario hashes and a strictly later
untouched window. Do not alter or inherit the Oct10/11 freezes or Oct12-19
protocol, and do not treat a previously exposed interval as untouched. Each
strategy/scenario must independently start from $1,000 with target 100 shares,
without daily reset, top-up or borrowing. Shared portfolios remain separate.
No historical profitability claim can be made until all required clock,
market-fee, quote, official settlement and continuous coverage inputs exist.
