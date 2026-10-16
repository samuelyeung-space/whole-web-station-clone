# Source registry for BTC5m ingestion

The cross-source registry is `DATA_SOURCE_REGISTRY.json` in the **private** repository `yangyangnovelist-hub/sgk90-shadow-archive`, branch `main` (introduced by merge `3b7539b4102ef48e7e62fb87f03ae4b54c9cec61`). Read it before diagnosing the owner's AWS recording coverage.

It distinguishes the AWS research recorder and S3 raw archive from the derived `lake` branch and from GitHub-hosted forward recordings. Do not use a GitHub Actions handoff gap or this replay registry's Oct9 watermark as evidence that the AWS server stopped recording.

Read-only inspection entrypoint: `tools/read_aws_inventory.py` in that private repository. It uses the existing AWS execution environment's default credential chain, prints no secrets, and performs no upload, deletion, restart or trading. A new private lake export removes the frozen upper date cutoff, but its actual first run (38938603020) failed before runner steps, so **no successful export or new ingestion is claimed**.

As checked on 2026-10-16 UTC, the current chat runtime's S3 listing attempts returned `NoCredentialsError`. Keep access-unavailable, raw data present, archive present, normalized, and replay-admitted as distinct states. Continue using the existing $1,000 / target 100-share partial-FAK benchmark; do not manufacture receipts/Down books or change frozen holdout identities.
