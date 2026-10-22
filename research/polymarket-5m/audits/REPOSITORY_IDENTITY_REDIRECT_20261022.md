# Repository identity redirect audit — 2026-10-22

## Classification

Infrastructure identity update only. No new market data, strategy evaluation, Rank7 result, OOS result, order submission, or production change.

## Verified identity

- Legacy repository name: `yangyangnovelist-hub/whole-web-station-clone`
- Canonical repository name: `samuelyeung-space/whole-web-station-clone`
- Stable GitHub repository ID: `1213157147`
- Visibility: public
- Connected permissions reported by GitHub: pull/push/admin available
- Resolution method: GitHub repository metadata after the legacy name returned HTTP 301 Moved Permanently

The legacy and canonical names refer to the same repository ID. Future connector calls should use the canonical name because the workflow and contents wrappers do not follow this rename redirect reliably.

## Evidence state at 2026-10-22T06:14Z

- Source branch `claude/polymarket-options-strategy-1lc7i4` remains exactly at `650e76a2e792ab8bbc8992d6b38a6341648bd0fe`.
- Research branch before this audit remains exactly at `62ab470b921c129e7466bf8051e01113347dab54`.
- Recording run `39710494768`: `record` job still in progress at “Record and paper-trade (no orders)”; zero artifacts published.
- Report run `39719721129`: `report` job still in progress at “Exploratory reports on the same recordings”; commit step pending; zero artifacts published.
- Therefore no new closed-day evidence was admitted and no replay or ranking was run.
- Complete recorded UTC days remain 0; Rank7 PnL remains null; formal OOS remains null; confirmed independent mechanisms remain 0/10.

## Operational consequence

Use `samuelyeung-space/whole-web-station-clone` for subsequent GitHub connector reads and writes. Keep the legacy name as an alias in historical records; do not reinterpret the rename as a new repository or evidence reset.
