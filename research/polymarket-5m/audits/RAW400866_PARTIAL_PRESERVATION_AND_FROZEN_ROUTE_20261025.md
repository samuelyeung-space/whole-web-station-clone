# Raw400866 partial preservation and existing frozen execution route
As of 2026-10-25 UTC. Research only.

## New transport result
Run40086650848 has four source chunks totaling1,351,464,960 payload bytes. Three original parts (00000,00002,00003) are now privately preserved:932,034,560 payload bytes. Each received outer artifact SHA256, inner ZIP SHA256, payload SHA256 and CRC/EOF matched its index. Raw capture.tar was not unpacked.

Artifact12564170105 / original part00001 timed out on all three read attempts. Its payload419,430,400bytes remains missing. The complete source hash has NOT been verified; source reconstruction and market admission remain pending. The original artifact expires2026-10-25T14:38:35Z. This is a transient download blocker, not proof of missing recorded BTC markets.

Direct saves of original parts00000and00002 returned explicit transfer_failed twice each; no unknown finalization was retried. Their bytes were instead split into eighteen48MiB-or-smaller ZIP chunks. All eighteen saves succeeded. Local reassembly exactly reproduced both original ZIP hashes, and post-upload local hashes matched before cleanup. Part00003 was saved directly. Private recovery manifest binds ordered chunk identities, lengths, hashes and original ZIP hashes. Large temporary originals and chunk copies were removed only after confirmed saving.

## Newly published metadata
Source c6151991214d166f7c9e00a2949c64db77a88264 advanced to c2d7b38dc4220dfb5f896b92a0474c346ef7d6f7, one result commit from run40155498846. Nine forward result files changed; no strategy or execution code changed. Only metadata was used; patches/economic rows were not used for tuning. That run's record job succeeded, analyze failed at Require strict BTC replay artifact; its exact failing subcommand was not inspected. Seven artifacts are listed:four raw parts,raw index,converted part and converted index.

## Reuse the existing recovery route
Do not rebuild an engine or rewrite old freeze hashes. Research branch parent3101293ca427570cfa1c78369b5689b089cc4bbb already contains run_frozen_source_race.py, frozen_dependency_runner.py and frozen-dependencies/source-race-v1. Its snapshot manifest records the original strict_prebuild.py hash4b7f847bc67319604652955b06eea6644ebd9c545cb555fe0ece14cdc5a13e08 and source commit63424060255da4d773b19e805e7fe798251fcca1.

The source workflow still invokes mutable h_source_race.py directly. The research entry point instead selects registered dependency hashes. The exact no-data preflight, from research/polymarket-5m, is:
```bash
python run_frozen_source_race.py audit --report /absolute/output/source-race-dependency-audit.json
```
A later replay on authorized consumed data can use:
```bash
python run_frozen_source_race.py replay \
  --archive /absolute/consumed/strict-bundle \
  --out /absolute/output/source-race.json \
  --dependency-report /absolute/output/source-race-dependencies.json \
  --rows-dir /absolute/output/source-race-rows
```
These commands are a delivery plan, not new executions in this run. The existing freeze is dollar-budget FAK; it does not satisfy fixed100share Rank7 merely by becoming runnable. A changed fixed-share evaluator requires separate registration and strictly later untouched evidence. No workflow was changed or dispatched.

## Evidence status
New financial replays0; new rules0; newly admitted complete UTC days0; Rank7 PnLnull; formal new OOSnull; qualified mechanisms0/10. Old freezes and windows unchanged. Prior workflow-log exposure remains registered; no pristine OOS claim. Missing part00001 is the first next action, then remaining raw captures in expiry order. No orders, production changes, AWS access or messages.
