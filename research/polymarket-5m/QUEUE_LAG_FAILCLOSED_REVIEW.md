# Complementary queue-lag execution integrity review

Research only. No production workflow, order submission, or live risk change.

Imported `complement_queue_lag.py` from the existing source branch at
`23439e508b9fd5a9ef367bec106dcaf2961c146a`. Original module SHA256:
`bab903971d48db9acafd46202276ab2b5e6a3f82be37d6fb08130b51054754a4`.
This is a correction to an existing economic mechanism, not a new mechanism.

## Reproduced issue

A nonfinite delta left stale levels and `snapshot_required=False`. A later
ordinary valid delta called `_validate()` and made the old book tradable again.
A synthetic event sequence between decision and match produced false fills at
400 and 500 ms. This does not establish occurrence in historical recordings.

## Correction

Malformed prices, sizes, sides and nonfinite clocks clear both sides, require
a snapshot and raise ValueError. A later ordinary delta cannot restore state.
Snapshot replacement validates both sides before assignment. A malformed
snapshot invalidates the entire book. The file replay must fail rather than
emit performance from corrupt input.

## Verification

Run from `research/polymarket-5m`:

    python -m unittest test_complement_queue_lag_failclosed -v

Five test methods cover malformed deltas, atomic snapshot rejection, clock
validation, valid signal/match behavior, and corruption after decision before
match. Original and corrected modules returned exactly identical complete
rows/counters for the valid synthetic fixture. No real-data backtest was run
for this revision, so new PnL is unavailable, not zero.

## Protocol boundary

The upstream frozen 5-share detector defaults are preserved for attribution.
They are not the user's 100-share comparison. That comparison still requires
100 shares per successful fill, zero fill if insufficient executable liquidity,
1000 USD shared cash, explicit release timing, and separate 500/600/1000 ms
total decision-to-match scenarios. Existing full100 tests/results are unchanged.
This corrected revision must receive a new code hash and strictly later,
untouched confirmation window before formal OOS; it cannot inherit the old
freeze. No new formal OOS window is claimed in this review.
