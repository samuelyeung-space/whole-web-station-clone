# Raw400708 preservation and strict source-race guard diagnosis

As of 2026-10-25 UTC. Research only; no orders, production changes, AWS access or workflow dispatch.

## New preservation
Raw recording run40070842966 was preserved before its 2026-10-25T10:37Z expiry: four independent ZIPs, 1,593,415,680 opaque payload bytes. Outer artifact digests, inner ZIP hashes, payload hashes, full ZIP CRC/EOF and concatenated source SHA256 all matched. Source digest: `6b652ad9b41d7b36d188328153a60ff84b44cb41d02b8dd7710ff34c8dd5816c`. All four were saved with durable private identities and verified again before large local cache removal. Raw capture.tar was not unpacked. This is transport evidence, not BTC5m coverage or economic evidence.

One further raw handoff, run40155498846, now has four parts and an index (expiry 2026-10-26T06:43Z); analysis is still running. It is queued, not preserved or admitted. Source head remains c6151991214d166f7c9e00a2949c64db77a88264; research parent remains 8a7a38c65c3aab1b9988dc6c32db379b71ade669.

## Exact new failure diagnosis
The failed strict step of run40139643966 is no longer an unknown group-level failure. Job121055924825 logs at 2026-10-25T03:37:13Z identify `h_source_race.py -> compare_archive -> _load_source_race_freeze` and:
`ValueError: source-race dependency drift: strict_prebuild.py`.

The frozen document itself matches its registered digest `29ad66236aa0d2927843764970e384cb5de238c405be4e050f6b790a31a1dc63`.
Its expected strict_prebuild.py digest is `4b7f847bc67319604652955b06eea6644ebd9c545cb555fe0ece14cdc5a13e08`; collector revision dad0d2feaa0120b2155cfb9c8db02d2120f99c6f and current source have `4bf41cd1179cc16792974c79ae485cfb26d36ea9a379a9a807ef66bb9d6ebbdb`.

Static call order places this failure before source-race artifact identity inspection and baseline/candidate comparison replay. This does not establish the results of other commands earlier in the grouped step, missing market data, strategy profitability or loss. The guard behaved as intended. No old hash, rule, freeze or protocol was changed to force acceptance.

## Access provenance and next executable route
Workflow job logs were accessed in this diagnostic and can contain upstream economic output. Register that exposure; do not label this interval pristine untouched OOS. No such economic output was used for parameter selection, training or a new PnL claim. Raw reserved-window tape remains unopened. The Oct24–31 common diagnostic-window registration stays fixed; continuous 2016-slot admission is still pending.

For a faithful frozen replay, restore the complete registered dependency closure in isolation before consuming tape. A changed converter requires a separately registered code identity and strictly later untouched window; simply updating the frozen hash is not a valid fix. Also retain the separately observed `complement_queue_lag.py: invalid venue BBO` exception for a future data-quality audit; no cause or repair is inferred here.

## Unchanged evidence status
New financial replays: 0. New candidate rules: 0. Newly admitted complete UTC days: 0. Rank7 PnL: null. Formal new OOS: null. Qualified independent mechanisms: 0/10. Previously consumed development simulations, including439, were not rerun.

Next raw preservation priority: run40086650848 before 2026-10-25T14:38Z, then40103429890,40121765309,40139643966 and40155498846 in expiry order. Converted30day archives remain available but do not replace native fields without schema verification.
