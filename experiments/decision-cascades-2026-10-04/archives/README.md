# Immutable raw trace archives

These seven archives preserve every selected raw byte from completed development
runs, the explicitly aborted `dev-local-8` probe, and the completed 720-execution
`holdout-final` schedule. Archives, manifests, and validation records are tracked
on this branch. Original raw run directories remain intact locally.

Each `<group>.manifest.json` records the archive SHA-256/size, every member's
relative path/SHA-256/size, completion or explicit-abort status, the archiver's
source hash and Git commit, and credential-scan counts. Archive members restore
under `runs/`. Original absolute paths inside audit JSON are retained unchanged.

| Group | Status | Files | Raw bytes | Compressed bytes |
|---|---|---:|---:|---:|
| dev-initial-referral | Completed | 129 | 1,314,542 | 127,068 |
| dev-v2-smoke | Completed | 413 | 4,816,422 | 477,502 |
| dev-local-8 | Explicitly aborted; partial evidence preserved | 200 | 1,842,835 | 171,219 |
| dev-effort-probe | Completed | 154 | 1,424,459 | 119,095 |
| dev-low-8 | Completed | 1,481 | 15,371,541 | 1,160,815 |
| dev-verification-smoke | Completed | 372 | 3,572,164 | 280,599 |
| holdout-final | Completed, 720/720 terminal | 13,005 | 114,349,258 | 8,887,507 |
| **Total** | | **15,754** | **142,691,221** | **11,223,805** |

Both actual provider credentials were loaded through the existing bounded
password-store helper, used only for an in-memory byte scan, and never saved or
printed. Every selected file passed. This scan establishes absence of those exact
credential values, not absence of every possible unknown secret. Existing source
redaction remains intact. No inference was performed.

## Verify and restore

From the repository root, verify an archive and all its members without needing
credentials or model access:

```sh
python3 experiments/decision-cascades-2026-10-04/scripts/archive_traces.py \
  dev-low-8 --verify-only
```

After successful verification, restore to a fresh experiment directory (do not
overwrite an existing run):

```sh
mkdir -p /tmp/proceda-traces
tar -xzf experiments/decision-cascades-2026-10-04/archives/dev-low-8.tar.gz \
  -C /tmp/proceda-traces
```

The restored case directories are beneath `/tmp/proceda-traces/runs/`, preserving
the original relative layout. Pass those restored arm directories to the offline
analyzer. Hash verification is implemented by the script and rejects duplicate,
non-file, or unsafe member paths before its own temporary extraction.

## Validation performed

All seven archive hashes and all 15,754 member hashes/sizes were verified.
`dev-low-8.restoration-validation.json` records temporary safe extraction and
comparison of original versus restored planned/terminal/correct counts and the
entire offline accounting dictionaries. Both arms reproduced 24 planned,
24 terminal, and 24 correct cases. Full-rate estimated hosted costs matched:
baseline $0.120483300; candidate $0.116435250. These remain list-price estimates,
not actual invoices. The temporary extraction was removed.

`holdout-final.restoration-validation.json` independently verifies the final
archive restoration: each arm has 360 planned and terminal cases, with 350 correct
baseline cases and 346 correct candidate cases. Entire accounting dictionaries
match, including full-rate cost estimates of $1.316093 and $1.23046505.
The held-out archive SHA-256 is
`eabff1d1efeacda21b7d4f82bb7bbb75e920644572a2a6dda1424d8d6ed7b22e`.

Six isolated synthetic self-checks also passed: unfinished-group refusal,
credential-match refusal, byte-exact restore, overwrite refusal, corrupt-archive
detection, and unsafe-path refusal. The utility passed Ruff formatting/lint.

## Future packaging

The utility groups only `runs/<prefix>` and `runs/<prefix>--*`. Scheduled groups
require a completion marker, all planned terminal cases, and arm summaries.
Legacy direct-arm groups require all planned terminal cases and summaries.
Aborted groups require both an `aborted.json` marker and an explicit
`--allow-aborted <prefix>` flag. Existing archives/manifests are never replaced.

The completed holdout was packaged and its restoration analyzed with:

```sh
.venv/bin/python experiments/decision-cascades-2026-10-04/scripts/archive_traces.py \
  holdout-final --validate-sample holdout-final
```

Existing archive paths are immutable, so use `--verify-only` to inspect the saved
archive rather than repeating its creation. Packaging requires the same local
password-store setup used by the experiment credential helper;
verification does not. Preserve previous archives as immutable snapshots even
if later analysis adds files to the original run directories.
