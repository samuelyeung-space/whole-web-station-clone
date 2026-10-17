# Post-send rejection sensitivity

Research only. This optional execution stress does not change a frozen strategy,
old protocol, production setting, recording or order path. Existing code remains
available at its original Git commit. Default rejection is zero. Any changed
execution closure requires a separately registered later untouched window for
formal OOS; it cannot inherit an earlier freeze.

`ReplayConfig.post_send_reject_bps` adds an **uncalibrated** hypothetical
10%/30% stress in the registered MFA study, not an estimate of venue rejection.
A SHA256 threshold uses only fixed seed, market ID, decision time and side.
Candidate, latency, future prices, realized fill and outcome are excluded.
Thus identical intents share shocks across accounts/scenarios and 10% shocks
are nested in 30%. The realized rejection fraction need not equal the threshold.
Do not search seeds or pick a favorable latency/rate.

Reservation stays occupied until the existing match-time receipt proxy. The
failed first signal remains consumed; no retry, new limit, cost rescaling,
capital reset or synthetic fill occurs. Real acknowledgement delay is unknown.
Coverage, direct-book, fee and market-constraint gates take precedence so a
synthetic rejection cannot hide unavailable execution inputs. The rates are
therefore conditional on passing those match-input gates. All failures remain
in attempts and each rejection has a cash event checked by the independent
cash audit.

The study uses the eight existing MFA rules on the identical consumed Oct7–9
input. Each candidate/latency/stress independently starts $1000 and targets
100 shares with partial FAK, no borrowing/top-up/day reset. It adds 48 execution
scenarios, **zero new candidates/mechanisms**. Saved 24 base metrics are reused
only as a reference, never rerun or linearly scaled. This is not a complete
seven-day result, calibrated real execution, a ranking entry or positive OOS.
Fewer fills can improve loss-making samples; such improvements are not alpha.

Reproduce with pinned source files and the exact consumed tape:

    python post_send_failure_study.py --tape CACHE/events.jsonl.gz \
      --expected-sha d74e1a017a54852d27e2edb227dd0f4c9af395287538afcf0f01feee4cf22af8 \
      --baseline MFA/isolated/metrics.json --out NEW_DIRECTORY

`protocol.json` is created before replay; paths must be new. Registrations,
code/input hashes, every order attempt, cash event, all 48 summaries and gzip
EOF/CRC receipts must be retained. Engineering tests are not strategy evidence.
