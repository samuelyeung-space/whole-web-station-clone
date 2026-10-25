# Raw400866 completed preservation and reusable recovery
2026-10-25 UTC. This supersedes the incomplete transport status in the prior partial-preservation report, without changing any financial result.

## Missing part recovered; full source verified
Artifact12564170105 / original part00001 downloaded successfully on the first attempt of this follow-up, after three timeouts in the previous pass. Outer artifact digest, original ZIP digest, payload length/digest and CRC/EOF matched. Original part00001 was preserved as nine48MiB-or-smaller standard artifact_parts ZIPs; all nine saves succeeded and post-upload local hashes matched.

Previously saved original parts00000and00002 were restored from their eighteen durable small ZIPs; original part00003 was restored from its directly saved ZIP. All four original ZIPs and all four payloads were verified. Streaming their payloads in index order produced exactly1,351,464,960bytes and source SHA256:
`e26742aacd7e65d75b4cf0e6d53ab24e21f6df5feec94f2821a56f90f7ac2dba`.
All4parts are now privately recoverable. The capture.tar contents were not extracted or interpreted. Market coverage and official result completeness remain unverified.

## Executable recovery adapter
New chunk_manifest_adapter.py reuses the existing unmodified artifact_parts.py assembler and verifier. It consumes the hash-bound opaque-original-zip-subchunks-v1 recovery manifest, ignores old local_path values, selects exact basenames and ordered indices, rejects symlinks/traversal/duplicate names and verifies each source ZIP. It rewraps chunk.bin into the existing canonical payload.bin transport format, then delegates payload/global hashes and atomic no-overwrite output to artifact_parts. No network, credentials, capture parsing or trading path.

Eight synthetic tests passed:roundtrip with stale local paths,missing chunk,wrong order,symlink,untrusted manifest change,wrong payload hash,traversal and overwrite. The adapter additionally restored two real419,430,520byte original ZIPs from the saved eighteen subchunks, matching both original hashes. These are transport checks, not economic observations.

From research/polymarket-5m:
```bash
python chunk_manifest_adapter.py \
  --manifest /absolute/recovery/chunk_recovery_manifest.json \
  --expected-manifest-sha256 TRUSTED_CHECKPOINT_MANIFEST_DIGEST \
  --chunks-dir /absolute/downloaded/chunks \
  --original-name ORIGINAL_ZIP_BASENAME \
  --out /absolute/output/original.zip
```
The expected digest is the recovery-manifest SHA256 retained in the private checkpoint's CHECKPOINT_MANIFEST.json, whose checkpoint ZIP identity/hash is bound in research state. Do not substitute an untrusted self-computed digest. Recovery requires downloaded chunks with their saved basenames. Allow scratch space for the downloaded chunks plus roughly two original-ZIP sizes during rewrapping and atomic assembly. The restored original ZIP is still opaque transport; recovering it alone is not authorization to consume reserved market data.

Original part00001 uses the standard artifact_parts manifest instead. Download its nine saved files and place them under canonical names from parts[].name before using artifact_parts verify/assemble with its separately retained manifest digest. The private storage manifest maps those names to durable file identities.

Code commits:b5d14e5acd9c9ce5abb4121b2bc86f234902cdf8(adapter),923173ec83619971069dfdb1328e861dd9677007(tests). Existing artifact_parts and all frozen evaluators/protocols were unchanged.

## Unchanged research status
Source head remains c2d7b38dc4220dfb5f896b92a0474c346ef7d6f7; research parent328627df9bb7aac09febe4a176a56b01449a4404. New financial replays0,new rules0,new admitted full UTC days0,Rank7 PnLnull,formal new OOSnull,qualified independent mechanisms0/10. Prior log-access provenance remains registered. No orders,production changes,AWS access,workflow dispatch or messages. All large local recovery copies were removed after successful saving and byte verification.

Next intake priority is raw40103429890 before2026-10-25T18:39Z, then40121765309,40139643966,40155498846 by expiry. No full-week admission follows from successful transport.
