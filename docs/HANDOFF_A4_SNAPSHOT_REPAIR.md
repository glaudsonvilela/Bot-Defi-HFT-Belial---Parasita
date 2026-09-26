# Bot Belial - A4 Git snapshot repair

UTC: 20260926T010202Z

## Root cause

The A4 FIX1 package manifest in Git and the original SSD release are identical:

```text
MANIFEST_SHA256=6e5e2331af0b075baed4777c7920be65b53b1e6687129cdaee36561766142885
```

The Git snapshot was incomplete because the repository preparation flow excluded
`*.log` files while those validation logs are legitimate members of
`MANIFEST_SHA256.json`.

The verifier error used a symmetric difference and therefore the earlier
`PACKAGE_MEMBERS_CHANGED` message looked ambiguous. The complete diagnosis
proved these files were **missing**, not extra.

## What was NOT the cause

Hash comparison proved the following Git and SSD files were identical before
this repair:

- `UNIVERSE_A2.json`
- `a4/catalog.py`
- `a4/spot_prepare.py`
- `a4/spot_validation.py`
- `a4/sensor_worker.py`
- `a4/venue_worker.py`
- `engine_a4/src/main.rs`
- `engine_a4/src/raydium_protocols.rs`

Therefore this package-member failure was not caused by the pool expansion,
universe selection, strategy thresholds, sizing, or native engine code.

## Repair

Restored exactly **26** missing manifest members from:

```text
/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter/releases/belial-a4-90m-v1-fix1
```

Each file was copied only after its SHA256 matched the unchanged package
manifest.

No manifest entry was changed.

## Safety

- PAPER / SHADOW only.
- orders=false.
- strategy changed=false.
- pools changed=false.
- engine changed=false.
- tests changed=false.
- manifest changed=false.

## Official verification

```text
A4_PACKAGE_HASHES=PASS
```

## Repair installer

```text
Belial Negro/Raydium Hunter/installers/BELIAL_A4_REPARAR_SNAPSHOT_GIT.sh
SHA256=a48a1a3ec505783955420f778eb1075cdbebb0c4d557229a835ec72b89a9e686
```

After this checkpoint is committed, rerun
`BELIAL_A4_90M_FIX4_ATUALIZAR.sh`.
