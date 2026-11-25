# XAI_DTI

Scientific materials for the v0.23 DTA manuscript, release **v0.23-r01.1**. The study separates changes in prediction risk from the accuracy of first-order approximations to frozen function responses.

This repository contains saved study tables, method dictionaries, selected scientific source, provenance records and an offline verifier. Model weights, frozen representation caches and selected numerical sidecars are distributed as assets of the matching GitHub Release. See [RIGHTS.md](RIGHTS.md) for the MIT scope for project-owned code, the CC BY 4.0 scope for project-owned models and derivatives, and third-party exclusions. Upstream material retains its own conditions; see [DATA_SOURCES.md](DATA_SOURCES.md).

## Start with the offline checks

Python 3.10 or newer is sufficient for byte and saved-arithmetic checks. No third-party Python package is needed.

```sh
python3 -B scripts/verify_release.py
```

This checks the selected scientific file manifest, correspondence between the browseable tables and ZIP attachments, 640 saved original-panel link/function rows, their 2,560 scalar comparisons, 20 original-panel summary slots and 20 accepted historical follow-up summary slots. It evaluates no model, decodes no numerical array and writes no output. A passing result establishes byte identity and arithmetic closure within those records.

After downloading the matching release assets, extract their archives **from the repository root**. Their members begin with `payload/`; retain that directory. The asset manifest and SHA256SUMS identify the exact release archives. Some archives are optional; a selective GINE core installation needs the five GINE model/cache bindings, calibration, original-panel selectors and saved reference array.

```sh
python3 -B scripts/verify_release.py --with-payload
python3 -B scripts/run_frozen_core.py
```

`--with-payload` requires every file listed in the extracted payload manifest, including optional archived heads. It hashes files without decoding them. The second command is a **preflight**: it checks the bounded core's required paths and bindings without importing NumPy or PyTorch. It can be used with the core subset of assets.

An explicit frozen-head execution entry point is available:

```sh
python3 -B scripts/run_frozen_core.py --execute --output core_replay_receipt.json
```

Install NumPy and CPU-capable PyTorch separately. The recorded historical environment used Python 3.12.7, NumPy 1.26.4 and PyTorch 2.13.0; [the historical runtime receipt](provenance/r01_original32/historical_runtime_identity_receipt.json) records its binary identities. The public adapter uses CPU float64, batch 1, one thread and the registered zero derivative at ReLU zero. It reads only the five GINE cases' original 32-source/64-link target: D, λ=1, restricted `FIT_ROW_UNIFORM`, `h1` and `q`, Kd/Ki. It uses each high-learning-rate head with its own original encoder coordinates and calibration. A new receipt path is required; historical outputs are never overwritten. Installing matching package versions does not establish the original binary realization. The public adapter was syntax-checked, but was **not model-executed during release preparation**.

The already completed local execution independently verified those 20 slots (60 aggregate scalars) and 640 link/function records. The distinct 128-source/256-link panel's same 20 target slots are supported by its accepted historical execution and static identity checks; no new function calls on that panel were made in the local core validation. These are separate evidence routes. The follow-up run's exact historical Python binary digest remains UNKNOWN.

The archived GATE prediction comparison has a separate runnable route, requiring NumPy and the saved-score/calibration release assets:

```sh
python3 -B scripts/verify_gate_saved_scores.py
```

It uses the published 5,697 GATE labels in exact binary64 hexadecimal form, the frozen role ordering, original F32 h0/high-learning-rate h1 scores, all 40 FIT-only calibration slots and AF18 source disposition. From the independent staging layout it verified **240 original/retained, case/endpoint risk and gain values with maximum absolute difference 0**. It performs saved-score arithmetic, not model inference. Source/license conditions for the projected BindingDB-native labels are stated in [DATA_SOURCES.md](DATA_SOURCES.md).

## Contents and interpretation

| Path | Contents |
| --- | --- |
| [claims/CLAIM_ASSET_MAP.md](claims/CLAIM_ASSET_MAP.md) | Scientific claims, R01–R07 evidence coverage, included artifacts and remaining dependencies |
| [claims/claim_asset_map.json](claims/claim_asset_map.json) | Machine-readable public artifact paths and coverage classes |
| `aggregates/AF02`–`AF12` | Published saved aggregates and dictionaries in browseable form |
| `aggregates/AF18` | Source adjudication, retained support and frozen-model comparisons used in v0.23 |
| `attachments/` | Matching sanitized ZIPs for AF2–12 and AF18 |
| [methods/NUMERICAL_OBJECTS.md](methods/NUMERICAL_OBJECTS.md) | F32 versus F64 objects, array columns, references and reduction rules |
| `methods/`, `provenance/` | Historical method specifications, selected runtime/run/acceptance records and current staging fingerprints |
| `saved_rows/` | Registered project-derived path/ranking rows, role-order identities and the exact GATE-label projection; manifests state the exact files |
| `src/historical/` | Scientific and plotting source snapshots for inspection; original worker entry points retain historical assumptions |
| `references/` | Verified bibliographic metadata and recorded reading scope; no literature PDFs |
| `payload/` after extraction | Model/cache/calibration bindings and selected numerical sidecars from the matching release |

The tables include the original frozen-support comparisons. AF18 adds the v0.23 source-adjudication analysis: 726 records with conflicting target associations are excluded from evaluation and four-corner support. The two concordant records retain their existing eligibility. Models, model selection and FIT-only calibration remain frozen. The retained-support results do not estimate the performance of models retrained on corrected data. Old full-support rows and current retained-support rows must be selected explicitly.

Coverage differs by claim. The release provides original production aggregates and selected row-level derived outputs beyond the bounded core. The presence of source, weights and caches does not establish a whole-paper rerun. In particular, full data construction, encoder training, HPO, source record adjudication and every archived statistic do not have a single portable, tested end-to-end runner here. The [claim map](claims/CLAIM_ASSET_MAP.md) states the relevant layer and missing prerequisites.

## Provenance and boundaries

The [scientific material manifest](provenance/scientific_material_manifest.json) records each staged file's public hash and, where applicable, its original source fingerprint. [The attachment audit](provenance/attachment_member_scan.json) records all 84 members of the selected 12 archives. Within these attachments, scientific JSON/JSONL numeric tokens were preserved verbatim while private path strings were replaced. Compressed copies and ZIPs therefore may have different byte hashes from the original local attachments. The smaller `methods/core_contract.json` is explicitly recorded as a selected-field metadata projection. Original-source fingerprints in historical receipts identify those original bytes; they do not authenticate transformed public bytes. Use the current public manifests to verify the public files.

Historical paths in receipts are provenance identifiers, not links to bundled input files. `historical_sources/` replaces host-specific path prefixes. Historical `public_release:false`, authorization, pending-access or pre-execution status fields are retained as statements at their recorded time; they are not the current release state. Method schemas also preserve their original pre-execution status labels. Current coverage is stated in this README and the claim map.

Raw third-party molecular/sequence inputs and original measurement archives are not copied into this Git tree. A separately manifested BindingDB source-data release asset has its own inclusion list, transformations and attribution in [DATA_SOURCES.md](DATA_SOURCES.md); it does not supply every historical input or archive. Literature PDFs/HTML, private environments, author-pending drafts and credentials are excluded. Frozen representations and score archives are project derivatives; their upstream provenance and any source conditions continue to apply. The registered R03 reconstruction agrees for 900 target groups and 991 archived conversions. Its actual historical producer identity remains UNKNOWN, and the audited records contain no case of multiple original records collapsing into a single evidence unit; that audit does not demonstrate all deduplication cases.

MODEL_GATE was development-exposed. Pair exclusion is not entity isolation. The Pack 01 result is external prediction evaluation, with the reported negative predictive-gain reversal retained; it is not external XAI-path validation or an unexposed confirmation. The Kd ranking reversal, failed historical runs, unresolved numerical qualifications and UNKNOWN fields remain part of the study. Query counts and local archive lookups are not total end-to-end cost comparisons.
