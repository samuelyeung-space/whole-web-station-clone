# DPP: matched observed depth-pressure persistence

Research only. No orders, deployment, production/risk changes or old freeze edits.
This study registers all 24 adaptive development variants before financial replay.
It does not claim 24 independent mechanisms or any new independent mechanism.

Prior art includes `strategy_zoo.book_imbalance(depth=3)`, single Up-book
imbalance in `mine500_catalog`, complete price paths in
`mine500_path_candidates`, direct complementary queue lag and fair-price
common-mode residuals. The new feature interaction is joint-token displayed
depth persistence and matched Up-only controls, not a new market microstructure
theory. Prior 720 catalog trials and all historical searches remain in the
multiple-testing history; there is no fresh denominator of 24 alone.

At each original 1s decision, require fresh (<=250ms) original receipts and
healthy direct bids/asks for BOTH tokens. Sum shares at the first three distinct
price levels (not three fixed 1cent ticks). For each token,
`I=(bid_depth-ask_depth)/(bid_depth+ask_depth)`. Joint pressure is
`(I_Up-I_Down)/2`; the matched control uses `I_Up` but retains exactly the same
two-token health gate and price-reference path. Reference midpoint is the
average of direct Up midpoint and one minus direct Down midpoint. Neither
token's orders are reconstructed from the other token for execution.

Require four or six consecutive 1s decision samples (3s or 5s endpoints).
Signed persistence is the smallest absolute pressure on a same-sign path;
crossing zero returns zero. Missing or invalid decision samples and missing grid
points reset the path. This describes observed decision-grid persistence, NOT
complete raw-feed health or liquidity surviving every intra-second event.
Do not interpret these snapshots as aggressor flow, cancellations or maker fills.

Cross templates `flat`, `continuation`, `reversal` with lookbacks 3/5s,
thresholds .25/.50 and joint/control arms: 24 unique rule fingerprints.
Flat means the entire midpoint range <=1cent; continuation/reversal require
at least 2cent endpoint movement agreeing/opposing pressure. Tau is 30–240s.
Both price thresholds are probability distances, not a claim of universal tick.
This is a taker/settlement experiment; no new fitted probability or model.

Every candidate/stress/latency independently starts $1000 and targets100 shares
with original fixed first-signal limits, partial FAK, market fee/tick/minimum
gates, reserved cash and official outcomes. No daily reset, borrowing, top-up
or shared portfolio. Baseline500ms,600/1000ms, half-depth and extra1cent are
reported separately. These 216 related scenarios cannot be summed. The prior
post-send rejection sensitivity is not combined into a full Cartesian grid.

Only the already consumed Oct7–9 MFA source tape is permitted by the runner's
input SHA256. It contains 27.01591 observed hours,336 registered markets and332
official outcomes, zero complete UTC days. No seven-day rank or OOS admission
is possible. Old freezes and the Oct12–19 protocol are unchanged. Any new
confirmation needs a separately registered later untouched interval.

Run:

    python run_depth_pressure_study.py --tape CACHE/events.jsonl.gz \
      --expected-sha d74e1a017a54852d27e2edb227dd0f4c9af395287538afcf0f01feee4cf22af8 \
      --out NEW_DIRECTORY

The original tape is restored using the existing frozen MFA recipe, not refit.
The new directory must not exist. Preserve protocol, feature receipt, all
attempts/cash events, metrics, all scenario audits and code/input hashes.
Synthetic prefix, symmetry, missing-input, threshold and malformed-book tests
are engineering checks only, not historical PnL.
