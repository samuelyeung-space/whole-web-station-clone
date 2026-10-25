# Raw401217 opaque preservation and policy recheck
2026-10-25 UTC. Transport evidence only.

## Preservation
Raw run40121765309 has three original source parts. Each outer Actions artifact digest,inner ZIP digest,payload digest/length and ZIP CRC/EOF matched the source index. Their ordered payload stream is1,181,306,880bytes with SHA256:
`7d7b8b6bd5619cf5524ad9a281187b65a4b7d54a81d4988dc742f9686dbedaeb`.
All three original parts are privately recoverable before their2026-10-25T22:41Z expiry.

The existing unmodified artifact_parts.py split these original ZIPs into25standard48MiB-or-smaller ZIP chunks. Every original passed stream reassembly verification. All25saves succeeded and post-upload local hashes matched. The private checkpoint binds canonical submanifests,their trusted hashes,saved file identity/name mappings and the source publication index. Restore each saved file to its canonical parts[].name before existing artifact_parts verify/assemble. The capture.tar market content was not extracted or interpreted.

Large task-owned originals,subchunks and transfer leftovers are removed only after successful durable saving,checkpoint identity confirmation and byte verification. No previous preserved capture was downloaded again.

## Official reference check, not historical assumption replacement
Primary references checked on2026-10-25:
- https://docs.polymarket.com/changelog/predictions
- https://docs.polymarket.com/trading/fees

The dated changelog still supports150mscrypto taker waiting effective2026-09-04T14:00Z and60-second5mChainlink TWAP effective2026-08-14T00:00Z; the earlier opening/final TWAP announcement remains. The current fee page lists crypto0.07 and shares×feeRate×p×(1-p). These page-level facts do not bind a historical market's feeSchedule,itode or effective timestamp. No old frozen cost assumption changed;250ms remains pressure only, and venue wait is not added twice to measured total latency. No new per-market cost/settlement endpoint was queried.

## Intake and research boundary
Source remains c2d7b38dc4220dfb5f896b92a0474c346ef7d6f7; research parent52b3a41c54cf7d94737d88820991e8c1e844fa16. The next recorder40186073702 is observed in progress from2026-10-25T10:45:23Z. That is workflow metadata, not evidence of continuous healthy BTC5m coverage.

New financial replays0,new candidates0,new admitted full UTC days0,Rank7 PnLnull,formal new OOSnull,qualified independent mechanisms0/10. The previously registered fourUSD-Mflow variants remain signal-only; no fee/fill/cash result is attributed to them. Old freezes,reserved common window and prior log-access provenance remain unchanged. No orders,production changes,deployment,AWS access,workflow dispatch or messages.

Next preservation order:raw40139643966 before2026-10-26T02:42Z,40155498846 before06:43Z,40170142710 before10:45Z. Require complete2016-slot market/outcome/execution coverage before any seven-day ranking admission; saved byte counts never substitute for it.
