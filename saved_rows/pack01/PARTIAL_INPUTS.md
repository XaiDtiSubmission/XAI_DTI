# Partial frozen input-access aid: pure Papyrus batch contributions

`pure_papyrus_inputs.csv.gz` contains **4,884** rows of the original frozen
Pack01, comprising **967 Kd and 3,917 Ki** rows. This is a partial input-access
aid. It is neither a new evaluation result nor a replacement for the original
5,000-row panel. No predictions, residuals, metrics, calibration or labels were
recomputed.

Selection uses the original saved `source_layers` field. Only rows whose layer
list is exactly `["PAPYRUS2024_2_NATIVE_BOUND_BATCH"]` are retained. All **116**
groups with any `INHERITED_HIGH_EVIDENCE116` contribution are excluded
(33 Kd and 83 Ki), including the one saved group containing both contribution
layers. The other 115 excluded groups list only the inherited contribution
layer. Whether a group is covered by Papyrus and whether Papyrus contributes to
its actual frozen label are distinct fields; Papyrus coverage alone was not
used to qualify this export.

`partial_inputs_excluded_identities.json` records each excluded identity and its
original row index, endpoint and saved contribution layers. It contains no
excluded molecular input or label. Rows in the CSV preserve the relative order
of `row_keys.json`; join them to the full prediction streams by sample ID and
endpoint. Do not align a 4,884-row table positionally with a 5,000-value array.

The five CSV columns are:

| Column | Meaning |
| --- | --- |
| `sample_id` | Original frozen identity |
| `endpoint` | Original Kd or Ki endpoint |
| `smiles` | Actual frozen canonical isomeric comparison-parent SMILES, copied unchanged |
| `protein_sequence` | Actual frozen reference-target sequence, copied unchanged |
| `y_binary64_hex` | Original `modeling_summary_p` converted to the same binary64 value used by the original evaluator, encoded with Python `float.hex()` |

Decode the last column with `float.fromhex()`; do not round it to binary32 or
derive a replacement label from a current database. Sequence byte hashes were
checked against the saved `protein_sequence_sha256` metadata. The export
contains no embedded source text, assay descriptions, observation arrays or
publisher attachments. It does not certify that a reference sequence is the
assayed experimental construct.

## Sources and licence

This adapted source-data projection is made available under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
It retains these upstream credits and terms:

- **Papyrus 2024.2**, Olivier J. M. Béquignon and Linde Schoenmaker, with the
  contributors listed on the [versioned Zenodo record](https://zenodo.org/records/13987985).
  Its [official record metadata](https://zenodo.org/api/records/13987985) identifies
  CC BY-SA 4.0. The registered file is
  `05.7++_combined_set_without_stereochemistry.tsv.xz`, SHA-256
  `8004e0d1027a760f205b45264386f792e7d49658da39f77f52e660a6f19760dd`.
  Cite the [Papyrus study](https://doi.org/10.1186/s13321-022-00672-x).
- **ChEMBL**, EMBL-EBI ChEMBL team. The original handoff identifies native
  **ChEMBL37 cross-version linkage**, distinct from ChEMBL34 within Papyrus
  2024.2. See [release 37](https://doi.org/10.6019/CHEMBL.database.37) and the
  [official CC BY-SA 3.0 notice](https://chembl.gitbook.io/chembl-interface-documentation/about).
  The original ChEMBL material retains CC BY-SA 3.0; its
  [section 4(b)](https://creativecommons.org/licenses/by-sa/3.0/legalcode#s4b)
  permits an adaptation under a later version with the same licence elements.
  The adaptation here preserves the share-alike requirement under CC BY-SA 4.0.
- **UniProt Consortium**, for the frozen reference sequence material, under its
  [CC BY 4.0 terms](https://rest.uniprot.org/help/license). See
  [UniProt's citation guidance](https://www.uniprot.org/help/publications).
- **XAI_DTI project**, for the saved curation, grouping, freezing, selection and
  field projection described in the manifest. Selection and packaging changes
  are identified here; the original input strings and binary64 label values are
  unchanged.

Retain these source credits, link the applicable licences and identify changes
when adapting or redistributing this projection. The project-wide MIT and
CC BY 4.0 notices do not replace this data file's share-alike terms.

## Exact frozen provenance

The version 1.62 documentation carries the same `packages_v1_61/pack_01.zip`
consumed by the historical V0 evaluation. A byte-only check of that nested ZIP
confirmed archive SHA-256
`5293d4f8f4e58611fea8947bbffabd6e23cde08d4c1603d4014f6c54b61e92c4`,
manifest SHA-256
`2fea8514eeb17a1c55e27abd49c9a0d6728c1a36f9dc6e902cf25288b7bc25bd`,
and `samples.jsonl` SHA-256
`281d63f538452a18086bb02457045fdedae049e49331b81a8ffced9b30cb3aae`.
The latter matches the actual frozen V0 source used for this projection.
The original source-layer definitions, preparation policy and ChEMBL37 source
statement are registered in the manifest by relative logical path and checksum.

This supplies exact frozen input bytes for the eligible partial panel. Official
release links and the later documentation do not, by themselves, replay every
historical native database request. The inherited-contribution groups remain
outside this input-access aid; the full original panel's saved results and
membership remain unchanged.
