# Pack01: frozen prediction streams and aligned identities

This archive exposes the existing outputs of
`iv20261101_frozen_pack01_v05_r01`, contract
`IV-V0V2-v0.5-PACK01-FROZEN-SGC-SUPPLEMENTARY`.
The exact source package is `packages_v1_61/pack_01.zip`, consumed through the
version 1.62 interface. Its registered SHA-256 is
`5293d4f8f4e58611fea8947bbffabd6e23cde08d4c1603d4014f6c54b61e92c4`.

Twenty original NumPy files provide h0 and h1 predictions for the ten frozen
case/seed combinations. Each contains 5,000 binary32 values, in the order of
`row_keys.json`: 1,000 Kd followed by 4,000 Ki. Their original full-file SHA-256
and canonical prediction-payload identities are recorded in `manifest.json`.
They were copied without decoding or reevaluating predictions.

`row_keys.json` exports only the existing sample ID and endpoint, preserving the
exact prediction order. Its ordered key-list digest matches the original
prediction seal:
`8a66deafb47bf716c355f1eda4ff839182763cae2f8a504a3cff5b0b678fe994`.
For this digest, serialize the `rows` array as UTF-8 JSON with no extra
whitespace (`ensure_ascii=False`, `sort_keys=True`, separators `(',', ':')`).

`source_groups.json` is the original group-identity archive, copied exactly.
For each named grouping dimension it maps SHA-256 digests of original group
keys to sample IDs and separately records IDs with missing metadata. Dimension
names such as `smiles` identify the grouping rule; the original SMILES,
sequences, accessions, study/assay identifiers and raw values are not present in
those hashed group keys. Hashes and sample IDs are stable linkage metadata,
not a claim of privacy protection against dictionary matching.

The existing case-level endpoint MAEs, balanced MAEs and arm contrasts are
available in the external-prediction aggregate materials (original report
SHA-256 `00627ee595e31d99320a1e5dd8e88f15b728f1856c7a284ef338bca4dc9b24ce`).
These allow checking case-to-arm aggregation. Checking row-to-MAE calculations
additionally requires the exact frozen binary64 truth values and the original
calibration slots. No truth, per-row residuals, source observations, molecular
input strings, sequence strings or representation caches are included here.
No new errors or statistics were calculated to create this archive.

Project-generated prediction outputs and grouping/identity material are
available under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) to the
extent of the project's rights. Credit the XAI_DTI project and retain this
run/version and the manifest. Underlying Papyrus, ChEMBL and UniProt data retain
their source licences and attribution requirements; see
[DATA_SOURCES.md](../../DATA_SOURCES.md). This archive does not redistribute their
original input or observation rows.

The original panel has 5,000 members. Recorded later role assignments and
historical-exposure qualifications are preserved in the study; releasing these
outputs does not redefine membership or certify a blind, globally unexposed or
cold-both evaluation.
