# Data sources, licences and exact-version access

Checked against official sources on 25 November 2026. This document accompanies
the XAI_DTI `v0.23-r01.1` release. The release manifest determines which files are
actually distributed; a source identity or download link below does not imply
that its original data archive is included.

Project-authored software is MIT licensed. Project-owned model weights,
calibration assets, frozen representations and original derived study materials
are CC BY 4.0 to the extent of the project's rights; see [RIGHTS.md](RIGHTS.md).
Third-party data retain the applicable source terms. The project licences do not
replace those terms or license the underlying journal articles and patent
documents.

## BindingDB: development inputs and MODEL_GATE labels

The development inputs were prepared from the BindingDB `202608` Articles and
Patents exports. `202608` identifies the export, not an experimental measurement
cut-off. The registered TSV members are dated 31 July 2026; the project acquired
and processed this snapshot on 10 August 2026.

BindingDB's official database paper explicitly releases data curated by
BindingDB under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) and
permits reuse and redistribution with citation and identification of changes.
It identifies US and selected WIPO patent data as BindingDB-curated data. It
separately preserves [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/)
for imported ChEMBL data. See the paper's **Data availability** and **BindingDB
is openly shared and well documented** sections:
[Liu et al., BindingDB in 2024, Nucleic Acids Research 53:D1633–D1644 (2025)](https://doi.org/10.1093/nar/gkae1075).
The official [information page](https://www.bindingdb.org/rwd/bind/info.jsp)
still links BindingDB-curated data to
[CC BY 3.0 US](https://creativecommons.org/licenses/by/3.0/us/).
Both statements permit attributed redistribution; this release relies on the
paper's explicit CC BY 4.0 data statement and records this version discrepancy.

The Articles filename alone is insufficient to establish a uniform licence.
A source-label-only inventory of the exact registered archive found:

| Exact archive | `Curation/DataSource` | Records |
| --- | --- | ---: |
| `BindingDB_BindingDB_Articles_202608_tsv.zip` | Curated from the literature by BindingDB | 93,023 |
| Same Articles archive | ChEMBL | 429 |
| Same Articles archive | Taylor Research Group, UCSD | 260 |
| `BindingDB_Patents_202608_tsv.zip` | US Patent | 1,339,453 |
| Same Patents archive | WIPO | 8,730 |

The archived preparation script excludes non-native Articles records by requiring
`Curation/DataSource == "Curated from the literature by BindingDB"` for that
partition. A licensing-provenance check joined the registered MODEL_FIT,
MODEL_SELECT and MODEL_GATE identities to their contributing record metadata.
All contributing records had provenance and only the three native categories
above occurred: literature, US Patent and WIPO. No ChEMBL or Taylor record was
found among those contributions. This checks source provenance, not scientific
correctness of an affinity or target annotation.

The mixed, unfiltered Articles archive must not be presented as entirely
CC BY 4.0. Imported ChEMBL records retain share-alike terms. No separate licence
for the 260 Taylor-labelled records was established in this audit; their inclusion
in the archive is not treated as permission to relicense them. A native-only
Articles extraction can retain original record identifiers, the source label,
the original snapshot identity and a documented selection rule. The exact
Patents archive contains only the two BindingDB-curated patent categories in
this inventory; the native-data permission supports its attributed
redistribution. This permission concerns the curated database data, not a
blanket licence to republish patent or journal full text or images.

### Separate BindingDB source-data asset

The separate BindingDB source-data asset is scoped to three objects; consult its
final manifest for the actual included filenames, sizes and checksums:

1. The exact frozen WP1 v1.1 working table,
   `bindingdb_development_coldboth.parquet`, SHA-256
   `d28f7e31c33dad1f5d2aa1be7c4f737ce766e834b25d1c2e4bee0fbb25f16507`.
   Its metadata-only source inventory contains 101,504 records, all labelled
   BindingDB: 19,795 native literature, 81,380 US Patent and 329 WIPO records.
   These are working-table records, not counts of final frozen comparison
   labels. Preserve the table bytes and the recorded preprocessing provenance.
2. The exact registered Patents ZIP identified below, with BindingDB attribution
   and the native-data licence notice.
3. A **native-only** projection of the registered Articles archive, selected by
   the exact `Curation/DataSource` rule above. It excludes the 429 ChEMBL and
   260 Taylor-labelled records. It is a transformed dataset and requires its own
   checksum, extraction rule and source-archive identity; the original Articles
   ZIP checksum does not identify this new projection.

The working table and any source projection retain BindingDB source attribution;
the project does not claim the original measurements as its own. Where frozen
inputs carry UniProt-derived sequence material, also credit UniProt, link its
[CC BY 4.0 notice](https://rest.uniprot.org/help/license), and retain the recorded
sequence provenance. Providing sequence bytes does not establish that a
canonical sequence is the experimental assay construct.

The complete all-source Assays and reaction-set bridge archives are not bundled
in this baseline source-data asset. Their exact registered identities and
independent acquisition route remain documented below; source-level assay
reconstruction is a separate access requirement.

### Frozen evaluation truth projection

`saved_rows/model_gate/frozen_labels.csv.gz` is an attributed, transformed
projection of the frozen BindingDB-derived MODEL_GATE comparison labels, not a
new experimental dataset. Its companion
`saved_rows/model_gate/frozen_labels_manifest.json` records 5,697 labels
(798 Kd and 4,899 Ki), their registered order and the source-file identity.
The original binary64 truth values are encoded using Python `float.hex()`;
recover them using `float.fromhex()` without rounding to binary32. No M1
prediction columns, literal molecular inputs or original source-record rows are
exported in that projection. The originating truth file has SHA-256
`9105443d72def4466d3572634e1a298a915622586389393eadce16f1b7abf252`.

These projected values inherit BindingDB-native attribution and reuse terms.
Credit BindingDB, cite the paper above, retain the source snapshot and manifest,
and state that the release selected frozen comparison labels and changed their
storage representation. The projection supplies the original comparison truth;
it does not reverse the study's recorded source-exclusion decisions or certify
the biological validity of every original source record.

### Exact source identities and assay linkage

| Registered source archive | Bytes | SHA-256 |
| --- | ---: | --- |
| `BindingDB_BindingDB_Articles_202608_tsv.zip` | 18,271,972 | `2529b1c572aa7b298e57355e251c9a9572b82dd35f97259d5e3866ee69bfada5` |
| `BindingDB_Patents_202608_tsv.zip` | 172,105,433 | `88d0e6664ac063489d2c161c80b6a83fd347eb87e924e47a44d01ee3de93ce13` |
| `BindingDB_rsid_eaids_202608_tsv.zip` | 7,556,983 | `dcd68218d72e6d1fbb87190b52ee8ceda21501b17cad4c6c27effd1a84eedd4b` |
| `BindingDB_Assays_202608_tsv.zip` | 10,067,687 | `e6ce48748603b669fa2d333c1759a946125d0e56d73eed8f795c6cb92e677252` |

The [official download page](https://www.bindingdb.org/rwd/bind/chemsearch/marvin/Download.jsp)
documents the separate assay linkage: a main TSV's reaction-set identifier joins
through `BindingDB_rsid_eaids.tsv` to `ENTRYID_ASSAYID`, then to
`BindingDB_Assays.tsv` by entry and assay identifiers. The registered auxiliary
archives contain identifier mappings and assay descriptions, respectively; they
have no curation-source or licence column. The official native-data grant is
relevant to native-record-bound assay material, but this audit did not establish
that every description in the complete all-source Assays archive is native
BindingDB material. A source-restricted assay/bridge projection therefore needs
an explicit record-source binding; it should not receive a blanket native-only
licence solely because of its filename.

BindingDB updates its download page monthly. At this check the page offered
`202611`; direct `202608` URLs at both `/rwd/bind/downloads/` and
`/bind/downloads/` returned HTTP 404. A download wrapper returning an HTML page
is not proof that the original ZIP is retrievable. BindingDB recommends the
[UC San Diego quarterly archive](https://library.ucsd.edu/dc/collection/bb03870458)
for immutable references. An archived snapshot must match the identities above
before being used for an exact replay. A newer monthly or quarterly export is
an independent reconstruction, not a byte-identical replacement.

## External prediction panel: Papyrus, native ChEMBL and UniProt

The registered Papyrus source is **Papyrus 2024.2**, Zenodo record **13987985**:
[official versioned record](https://zenodo.org/records/13987985) and
[record metadata](https://zenodo.org/api/records/13987985).
The record metadata identifies its data licence as **CC BY-SA 4.0**.
The relevant Papyrus++ file is
`05.7++_combined_set_without_stereochemistry.tsv.xz`, official MD5
`9a325b965fb2ab043679d974d1f79549`.
Use the versioned record and file checksum, not the latest Papyrus release.
Papyrus 2024.2 updates its ChEMBL component to release 34; Papyrus file naming
(`05.7`) and the Zenodo release version (`2024.2`) are different identifiers.

The external panel also contains a 116-group high-evidence subset and native
ChEMBL activity linkage. That subset is not wholly supplied by the Papyrus++
file. Reacquiring Papyrus alone therefore does not establish an exact replay of
all 5,000 external labels or inputs. The saved panel identities, source joins,
role assignment, endpoint rules and input encoding must also be preserved.

[ChEMBL's official terms](https://chembl.gitbook.io/chembl-interface-documentation/about)
are **CC BY-SA 3.0**. Its versioned
[release catalogue](https://chembl.gitbook.io/chembl-interface-documentation/downloads)
provides [release 34](https://doi.org/10.6019/CHEMBL.database.34), matching the
Papyrus component. That fact alone does not establish which native ChEMBL
database snapshot was used for the additional activity linkage. A complete
native-linkage replay still requires that snapshot's version/checksum and
the saved activity identifiers; a live API or current release 37 is not an
exact substitute. ChEMBL-derived redistributed data must retain its source
attribution and applicable share-alike terms rather than being labelled
project-only CC BY 4.0.

[UniProt data are CC BY 4.0](https://rest.uniprot.org/help/license).
The external methods describe a frozen UniProt canonical sequence reference.
A live accession lookup does not guarantee the same sequence bytes and does not
certify an assay construct. Exact input replay requires the frozen sequence
identity/version and the saved sequence-to-panel mapping. No complete claim is
made here that all external-panel original inputs and labels have been exposed
merely by providing the official source links.

## What can be checked with the released material

| Claim or task | Material needed and source boundary |
| --- | --- |
| Frozen head responses, source gradients and E/B/Skill on the recorded latent supports | Project-owned heads, calibration slots, frozen representations, ordered supports, reference weights and operation dictionaries. These checks do not require biochemical truth labels. Retain BindingDB attribution for the development-data provenance. |
| Original MODEL_GATE prediction-risk comparisons | Released frozen scores plus the binary64 truth projection above, exact case/endpoint/order bindings and original calibration slots. This is a frozen comparison replay, not retraining. |
| Source-exclusion prediction-risk comparison | The same frozen quantities plus the released disposition/support identities. Use the specified eligible subset; do not silently replace labels or refit calibration. |
| Path, candidate-ranking and secant panels | Their own released panel identities, frozen support/response material and method definitions. Do not substitute a different panel or infer missing truth from model outputs. |
| Rebuilding development molecules, protein inputs and labels from original databases | Exact BindingDB snapshot and the registered preprocessing/filtering/aggregation rules, input mappings and roles. The frozen latent cache permits latent-space replay but does not itself reconstruct the original molecular inputs. |
| Exact external prediction replay | Exact external frozen inputs/truth, saved panel/source mappings, original encoding and model assets. Papyrus/ChEMBL/UniProt access routes above are available, but the unresolved exact native-linkage and sequence-version bindings remain distinct requirements. |

Historical Davis, Metz, GLASS and other source registries used for screening or
decontamination do not become freely redistributable through this project's
licence. Their original payloads are not licensed by this notice. Full source
articles, publisher HTML/PDF, screenshots and assay-document images are likewise
not included under the project software or model licence. Public citations and
source locators can be provided without asserting ownership of those works.

The release manifests, source-specific notices and exact comparison scope take
precedence over any shorthand description such as “all data open”. Providing
frozen scientific material supports the specified checks; it does not attest to
an unrecorded historical software environment or establish completion of every
raw-data reconstruction route.
