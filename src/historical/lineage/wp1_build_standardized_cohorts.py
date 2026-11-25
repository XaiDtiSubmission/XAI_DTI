#!/usr/bin/env python3
"""Build WP1 standardized DTI cohorts and exact cold-both audit outputs.

This pipeline never reads model outputs. Raw inputs are treated as immutable and
all generated artifacts are written beneath a caller-provided output directory.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import pickle
import re
import shutil
import sys
import time
import zipfile
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rdkit import Chem, rdBase
from rdkit.Chem.MolStandardize import rdMolStandardize
from rdkit.Chem.Scaffolds import MurckoScaffold


rdBase.DisableLog("rdApp.*")
csv.field_size_limit(sys.maxsize)

PURE_NUMBER = re.compile(r"^\s*(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*$")
DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
PMID_PATTERN = re.compile(r"\d+")
SEQUENCE_PATTERN = re.compile(r"[^A-Z*]")

COMMON_COLUMNS = [
    "record_id",
    "cohort",
    "source_partition",
    "source_record_id",
    "source_database",
    "curation_source",
    "raw_compound_id",
    "raw_target_id",
    "assay_target_name",
    "endpoint",
    "raw_value",
    "raw_unit",
    "raw_relation",
    "p_affinity",
    "raw_smiles",
    "raw_inchikey",
    "standardized_smiles",
    "standardized_inchikey",
    "compound_connectivity_key",
    "murcko_scaffold",
    "fragment_count",
    "standardization_changed",
    "target_uniprot",
    "target_gene",
    "target_species",
    "target_taxon",
    "target_sequence",
    "target_sequence_key",
    "doi",
    "pmid",
    "patent",
    "pdb_id",
    "mapping_quality",
    "duplicate_group_id",
]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def clean_sequence(value: object) -> str:
    if value is None:
        return ""
    return SEQUENCE_PATTERN.sub("", str(value).upper())


def normalize_doi(value: object) -> str:
    text = str(value or "").strip().lower()
    text = DOI_PREFIX.sub("", text).strip()
    return text.rstrip(".,; ")


def normalize_pmid(value: object) -> str:
    text = str(value or "").strip()
    match = PMID_PATTERN.search(text)
    return match.group(0) if match else ""


def normalize_uniprot(value: object) -> str:
    text = str(value or "").strip().upper()
    return text.split()[0].split(";")[0] if text else ""


def positive_float(value: object) -> float | None:
    text = str(value or "").strip()
    if not PURE_NUMBER.fullmatch(text):
        return None
    number = float(text)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def split_pdb_ids(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    tokens = sorted({token for token in re.split(r"[,;\s]+", text) if token})
    return ";".join(tokens)


@lru_cache(maxsize=None)
def standardize_smiles(raw_smiles: str) -> tuple:
    text = str(raw_smiles or "").strip()
    if not text:
        return (False, "MISSING_SMILES", "", "", "", "", 0, False)
    try:
        molecule = Chem.MolFromSmiles(text)
        if molecule is None:
            return (False, "RDKIT_PARSE_FAILED", "", "", "", "", 0, False)
        fragment_count = len(Chem.GetMolFrags(molecule))
        canonical_raw = Chem.MolToSmiles(molecule, isomericSmiles=True, canonical=True)
        parent = rdMolStandardize.Cleanup(molecule)
        parent = rdMolStandardize.FragmentParent(parent)
        parent = rdMolStandardize.Uncharger().uncharge(parent)
        Chem.SanitizeMol(parent)
        canonical = Chem.MolToSmiles(parent, isomericSmiles=True, canonical=True)
        inchikey = Chem.MolToInchiKey(parent)
        if not canonical or not inchikey:
            return (False, "EMPTY_STANDARDIZED_PARENT", "", "", "", "", fragment_count, False)
        scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=parent, includeChirality=False)
        return (
            True,
            "OK",
            canonical,
            inchikey,
            inchikey.split("-")[0],
            scaffold,
            fragment_count,
            canonical != canonical_raw,
        )
    except Exception as exc:
        return (False, f"STANDARDIZATION_ERROR:{type(exc).__name__}", "", "", "", "", 0, False)


def make_record_id(*parts: object) -> str:
    return sha256_text("|".join(str(part or "") for part in parts))[:24]


def duplicate_group_id(record: dict) -> str:
    source_key = record["doi"] or record["pmid"] or record["patent"] or record["source_database"]
    return make_record_id(
        record["compound_connectivity_key"],
        record["target_sequence_key"],
        record["endpoint"],
        source_key,
    )


def complete_record(record: dict) -> dict:
    missing = [column for column in COMMON_COLUMNS if column not in record]
    if missing:
        raise RuntimeError(f"Record missing columns: {missing}")
    record["duplicate_group_id"] = duplicate_group_id(record)
    return {column: record[column] for column in COMMON_COLUMNS}


def write_dataframe_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    temporary.replace(path)


def load_davis(
    matrix_xlsx: Path, deepdta_dir: Path, counters: Counter
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    matrix = pd.read_excel(matrix_xlsx)
    compound_columns = list(matrix.columns[3:])
    numeric_matrix = matrix.iloc[:, 3:].apply(pd.to_numeric, errors="coerce")
    filled_matrix = numeric_matrix.fillna(10000.0).to_numpy(dtype=float).T

    with (deepdta_dir / "Y").open("rb") as handle:
        deepdta_y = pickle.load(handle, encoding="latin1")
    with (deepdta_dir / "ligands_iso.txt").open() as handle:
        ligands = json.load(handle)
    with (deepdta_dir / "proteins.txt").open() as handle:
        proteins = json.load(handle)

    ligand_ids = list(ligands)
    protein_names = list(proteins)
    if deepdta_y.shape != (68, 442) or len(matrix) != 442:
        raise RuntimeError(f"Unexpected Davis matrix shapes: source={matrix.shape}, Y={deepdta_y.shape}")
    if len(protein_names) != 442:
        raise RuntimeError("DeepDTA protein map is not aligned to 442 Davis rows")

    mapping = []
    used_columns: set[int] = set()
    for ligand_position, vector in enumerate(deepdta_y):
        differences = np.max(np.abs(filled_matrix - vector), axis=1)
        source_position = int(np.argmin(differences))
        if float(differences[source_position]) != 0.0 or source_position in used_columns:
            raise RuntimeError("Davis ligand mapping did not produce a unique exact matrix match")
        used_columns.add(source_position)
        mapping.append((ligand_ids[ligand_position], source_position))

    excluded_compounds = [
        compound_columns[position]
        for position in range(len(compound_columns))
        if position not in used_columns
    ]
    counters["davis_source_numeric_cells"] = int(numeric_matrix.notna().sum().sum())
    counters["davis_mapped_compounds"] = len(mapping)
    counters["davis_unmapped_compounds"] = len(excluded_compounds)

    target_rows = []
    for target_position, protein_name in enumerate(protein_names):
        sequence = clean_sequence(proteins[protein_name])
        target_rows.append(
            {
                "target_position": target_position,
                "refseq_accession": str(matrix.iloc[target_position, 0]),
                "gene_symbol": str(matrix.iloc[target_position, 1]),
                "assay_target_name": str(matrix.iloc[target_position, 2]),
                "deepdta_target_key": protein_name,
                "target_sequence_key": sha256_text(sequence),
                "sequence_length": len(sequence),
            }
        )
    target_audit = pd.DataFrame(target_rows)
    target_audit["primary_selected"] = False
    target_audit["selection_reason"] = ""
    selected_target_positions: set[int] = set()
    for _, group in target_audit.groupby("target_sequence_key", sort=False):
        exact_gene = group[
            group["assay_target_name"].str.strip() == group["gene_symbol"].str.strip()
        ]
        if len(group) == 1:
            chosen_position = int(group.iloc[0]["target_position"])
            reason = "UNIQUE_INPUT_SEQUENCE"
        elif len(exact_gene) == 1:
            chosen_position = int(exact_gene.iloc[0]["target_position"])
            reason = "PREFERRED_EXACT_GENE_SYMBOL_WITHIN_SHARED_SEQUENCE"
        else:
            target_audit.loc[group.index, "selection_reason"] = "AMBIGUOUS_SHARED_SEQUENCE_EXCLUDED"
            continue
        selected_target_positions.add(chosen_position)
        target_audit.loc[group.index, "selection_reason"] = "SHARED_SEQUENCE_NONSELECTED"
        chosen_index = group[group["target_position"] == chosen_position].index
        target_audit.loc[chosen_index, "primary_selected"] = True
        target_audit.loc[chosen_index, "selection_reason"] = reason

    counters["davis_assay_targets_total"] = len(target_audit)
    counters["davis_unique_input_sequences"] = target_audit["target_sequence_key"].nunique()
    counters["davis_primary_selected_assay_targets"] = len(selected_target_positions)
    counters["davis_assay_targets_excluded_sequence_ambiguity"] = len(target_audit) - len(selected_target_positions)

    full_records: list[dict] = []
    primary_records: list[dict] = []
    mapping_rows: list[dict] = []
    for ligand_position, (pubchem_cid, source_position) in enumerate(mapping):
        compound_name = compound_columns[source_position]
        raw_smiles = ligands[pubchem_cid]
        standardized = standardize_smiles(raw_smiles)
        mapping_rows.append(
            {
                "pubchem_cid": pubchem_cid,
                "davis_compound": compound_name,
                "source_column_position": source_position + 4,
                "matrix_exact_match": True,
                "smiles_status": standardized[1],
            }
        )
        if not standardized[0]:
            counters["davis_compounds_standardization_failed"] += 1
            continue
        values = numeric_matrix.iloc[:, source_position]
        for target_position, raw_value in values.items():
            if pd.isna(raw_value):
                continue
            sequence = clean_sequence(proteins[protein_names[target_position]])
            target_sequence_key = sha256_text(sequence) if sequence else ""
            record = {
                "record_id": make_record_id("DAVIS_FULL", target_position, compound_name, raw_value),
                "cohort": "DAVIS_MAPPED_FULL_CANDIDATE",
                "source_partition": "Davis_2011_original_measured",
                "source_record_id": f"row{target_position + 2}|{compound_name}",
                "source_database": "Davis2011",
                "curation_source": "original_supplement",
                "raw_compound_id": pubchem_cid,
                "raw_target_id": str(matrix.iloc[target_position, 0]),
                "assay_target_name": str(matrix.iloc[target_position, 2]),
                "endpoint": "Kd",
                "raw_value": float(raw_value),
                "raw_unit": "nM",
                "raw_relation": "=",
                "p_affinity": 9.0 - math.log10(float(raw_value)),
                "raw_smiles": raw_smiles,
                "raw_inchikey": "",
                "standardized_smiles": standardized[2],
                "standardized_inchikey": standardized[3],
                "compound_connectivity_key": standardized[4],
                "murcko_scaffold": standardized[5],
                "fragment_count": standardized[6],
                "standardization_changed": standardized[7],
                "target_uniprot": "",
                "target_gene": str(matrix.iloc[target_position, 1]),
                "target_species": "Homo sapiens",
                "target_taxon": "9606",
                "target_sequence": sequence,
                "target_sequence_key": target_sequence_key,
                "doi": "10.1038/nbt.1990",
                "pmid": "",
                "patent": "",
                "pdb_id": "",
                "mapping_quality": "MATRIX_EXACT+DEEPDTA_CANONICAL_SEQUENCE",
                "duplicate_group_id": "",
            }
            full_record = complete_record(record)
            full_records.append(full_record)
            if target_position in selected_target_positions:
                primary_record = dict(full_record)
                primary_record["record_id"] = make_record_id(
                    "DAVIS_PRIMARY_SEQUENCE_RESOLVED", target_position, compound_name, raw_value
                )
                primary_record["cohort"] = "DAVIS_PRIMARY_SEQUENCE_RESOLVED"
                primary_record["mapping_quality"] = "MATRIX_EXACT+SEQUENCE_RESOLVED_TARGET"
                primary_record["duplicate_group_id"] = ""
                primary_records.append(complete_record(primary_record))

    mapping_frame = pd.DataFrame(mapping_rows)
    for name in excluded_compounds:
        mapping_frame.loc[len(mapping_frame)] = {
            "pubchem_cid": "",
            "davis_compound": name,
            "source_column_position": compound_columns.index(name) + 4,
            "matrix_exact_match": False,
            "smiles_status": "NO_RELIABLE_STRUCTURE_CROSSWALK",
        }
    full_frame = pd.DataFrame(full_records, columns=COMMON_COLUMNS)
    primary_frame = pd.DataFrame(primary_records, columns=COMMON_COLUMNS)
    counters["davis_full_mapped_candidate_records"] = len(full_frame)
    counters["davis_primary_records"] = len(primary_frame)
    return primary_frame, full_frame, mapping_frame, target_audit


def load_metz(
    matrix_xlsx: Path, mapping_tsv: Path, counters: Counter
) -> tuple[pd.DataFrame, pd.DataFrame]:
    matrix = pd.read_excel(matrix_xlsx, dtype=object)
    target_columns = list(matrix.columns[14:])
    mapping = pd.read_csv(mapping_tsv, sep="\t", dtype=str, keep_default_na=False)
    mapping_by_symbol = mapping.set_index("metz_symbol").to_dict("index")

    compound_meta: list[dict] = []
    for _, row in matrix.iloc[:, :14].iterrows():
        raw_smiles = str(row.get("Canonical_Smiles") or "").strip()
        standardized = standardize_smiles(raw_smiles)
        compound_meta.append(
            {
                "raw_compound_id": str(row.get("Cmpd_ID") or ""),
                "pubchem_sid": str(row.get("PUBCHEM_SID") or ""),
                "raw_smiles": raw_smiles,
                "standardized": standardized,
            }
        )

    activity = matrix[target_columns]
    nonblank_per_compound = activity.notna().sum(axis=1).to_numpy()
    numeric_per_compound = activity.apply(pd.to_numeric, errors="coerce").notna().sum(axis=1).to_numpy()
    structured_mask = np.array([item["standardized"][0] for item in compound_meta], dtype=bool)
    counters["metz_nonblank_cells_all_compounds"] = int(nonblank_per_compound.sum())
    counters["metz_nonnumeric_nonblank_cells_all_compounds"] = int(
        nonblank_per_compound.sum() - numeric_per_compound.sum()
    )
    counters["metz_nonblank_cells_structure_available"] = int(nonblank_per_compound[structured_mask].sum())
    counters["metz_numeric_cells_structure_available"] = int(numeric_per_compound[structured_mask].sum())
    counters["metz_nonnumeric_nonblank_cells_structure_available"] = int(
        nonblank_per_compound[structured_mask].sum() - numeric_per_compound[structured_mask].sum()
    )

    records: list[dict] = []
    mapping_audit: list[dict] = []
    for target in target_columns:
        target_name = str(target)
        mapped = mapping_by_symbol.get(target_name, {})
        sequence = clean_sequence(mapped.get("sequence", ""))
        accession = normalize_uniprot(mapped.get("uniprot_accession", ""))
        valid_target = bool(sequence and accession)
        mapping_audit.append(
            {
                "metz_symbol": target_name,
                "selection_status": mapped.get("selection_status", "MISSING_FROM_MAPPING"),
                "uniprot_accession": accession,
                "uniprot_primary_gene": mapped.get("uniprot_primary_gene", ""),
                "sequence_length": len(sequence),
                "eligible_primary": valid_target,
            }
        )
        values = pd.to_numeric(matrix[target], errors="coerce")
        exact_positions = np.flatnonzero(values.notna().to_numpy())
        counters["metz_numeric_cells_all_targets"] += len(exact_positions)
        if not valid_target:
            counters["metz_numeric_cells_unmapped_target"] += len(exact_positions)
            continue
        target_sequence_key = sha256_text(sequence)
        for position in exact_positions:
            raw_value = float(values.iloc[position])
            if not math.isfinite(raw_value):
                continue
            compound = compound_meta[position]
            standardized = compound["standardized"]
            if not standardized[0]:
                counters["metz_records_compound_standardization_failed"] += 1
                continue
            record = {
                "record_id": make_record_id("METZ", compound["raw_compound_id"], target_name, raw_value),
                "cohort": "METZ_PRIMARY",
                "source_partition": "Metz_2011_original_numeric",
                "source_record_id": f"{compound['raw_compound_id']}|{target_name}",
                "source_database": "Metz2011",
                "curation_source": "original_supplement",
                "raw_compound_id": compound["raw_compound_id"],
                "raw_target_id": target_name,
                "assay_target_name": target_name,
                "endpoint": "Ki",
                "raw_value": raw_value,
                "raw_unit": "pKi",
                "raw_relation": "=",
                "p_affinity": raw_value,
                "raw_smiles": compound["raw_smiles"],
                "raw_inchikey": "",
                "standardized_smiles": standardized[2],
                "standardized_inchikey": standardized[3],
                "compound_connectivity_key": standardized[4],
                "murcko_scaffold": standardized[5],
                "fragment_count": standardized[6],
                "standardization_changed": standardized[7],
                "target_uniprot": accession,
                "target_gene": mapped.get("uniprot_primary_gene", "") or target_name,
                "target_species": "Homo sapiens",
                "target_taxon": "9606",
                "target_sequence": sequence,
                "target_sequence_key": target_sequence_key,
                "doi": "10.1038/nchembio.530",
                "pmid": "",
                "patent": "",
                "pdb_id": "",
                "mapping_quality": str(mapped.get("selection_status", "")),
                "duplicate_group_id": "",
            }
            records.append(complete_record(record))

    frame = pd.DataFrame(records, columns=COMMON_COLUMNS)
    counters["metz_primary_records"] = len(frame)
    counters["metz_compounds_total"] = len(compound_meta)
    counters["metz_compounds_standardization_failed"] = sum(
        not item["standardized"][0] for item in compound_meta
    )
    return frame, pd.DataFrame(mapping_audit)


def load_glass(
    full_tsv: Path,
    ligands_tsv: Path,
    protein_json: Path,
    counters: Counter,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    selected_rows: dict[str, list[dict]] = {"primary": [], "text_mining": [], "other": []}
    selected_inchikeys: set[str] = set()
    with full_tsv.open(encoding="utf-8", errors="replace", newline="") as handle:
        for source_row_number, row in enumerate(csv.DictReader(handle, delimiter="\t"), start=2):
            counters["glass_rows_total"] += 1
            row["_source_row_number"] = source_row_number
            endpoint = (row.get("standard_type") or "").strip()
            relation = (row.get("standard_relation") or "").strip()
            unit = (row.get("standard_units") or "").strip()
            if endpoint not in {"Kd", "Ki"}:
                continue
            counters["glass_direct_endpoint"] += 1
            if relation != "=":
                counters["glass_direct_nonexact"] += 1
                continue
            value = positive_float(row.get("standard_value"))
            if value is None or unit.lower() != "nm":
                counters["glass_direct_bad_value_or_unit"] += 1
                continue
            counters["glass_direct_exact_nm"] += 1
            source = (row.get("source_database") or "").strip()
            source_lower = source.lower()
            if "bindingdb" in source_lower or "chembl" in source_lower:
                counters["glass_direct_aggregator_lineage"] += 1
                continue
            doi = normalize_doi(row.get("doi"))
            pmid = normalize_pmid(row.get("pmid"))
            if not doi and not pmid:
                counters["glass_direct_missing_publication"] += 1
                continue
            if "iuphar" in source_lower or "pdsp" in source_lower:
                bucket = "primary"
            elif "text-mining" in source_lower or "text mining" in source_lower:
                bucket = "text_mining"
            else:
                bucket = "other"
            row["_numeric_value"] = value
            row["_normalized_doi"] = doi
            row["_normalized_pmid"] = pmid
            selected_rows[bucket].append(row)
            selected_inchikeys.add((row.get("compound_inchikey") or "").strip().upper())

    ligand_map: dict[str, dict[str, str]] = {}
    with ligands_tsv.open(encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            key = (row.get("InChIKey") or "").strip().upper()
            if key in selected_inchikeys and key not in ligand_map:
                ligand_map[key] = row
    with protein_json.open(encoding="utf-8") as handle:
        protein_map = json.load(handle)

    def build(bucket: str, cohort: str) -> pd.DataFrame:
        records: list[dict] = []
        for row in selected_rows[bucket]:
            raw_inchikey = (row.get("compound_inchikey") or "").strip().upper()
            ligand = ligand_map.get(raw_inchikey, {})
            raw_smiles = str(ligand.get("SMILES", "") or "").strip()
            standardized = standardize_smiles(raw_smiles)
            if not standardized[0]:
                counters[f"glass_{bucket}_compound_standardization_failed"] += 1
                continue
            accession = normalize_uniprot(row.get("target_uniprot_id"))
            protein = protein_map.get(accession, {})
            sequence = clean_sequence(protein.get("sequence", ""))
            taxon = str(protein.get("taxon_id", "") or "").strip()
            if not sequence:
                counters[f"glass_{bucket}_missing_sequence"] += 1
                continue
            if taxon != "9606":
                counters[f"glass_{bucket}_nonhuman"] += 1
                continue
            value = float(row["_numeric_value"])
            endpoint = str(row.get("standard_type"))
            record = {
                "record_id": make_record_id("GLASS2", row.get("_source_row_number"), row.get("pair_id"), row.get("source_database"), endpoint, value, row.get("pmid"), row.get("doi")),
                "cohort": cohort,
                "source_partition": f"GLASS2_{bucket}",
                "source_record_id": str(row.get("pair_id") or ""),
                "source_database": str(row.get("source_database") or ""),
                "curation_source": str(row.get("confidence") or ""),
                "raw_compound_id": str(row.get("uci") or ""),
                "raw_target_id": accession,
                "assay_target_name": str(protein.get("full_name", "") or accession),
                "endpoint": endpoint,
                "raw_value": value,
                "raw_unit": "nM",
                "raw_relation": "=",
                "p_affinity": 9.0 - math.log10(value),
                "raw_smiles": raw_smiles,
                "raw_inchikey": raw_inchikey,
                "standardized_smiles": standardized[2],
                "standardized_inchikey": standardized[3],
                "compound_connectivity_key": standardized[4],
                "murcko_scaffold": standardized[5],
                "fragment_count": standardized[6],
                "standardization_changed": standardized[7],
                "target_uniprot": accession,
                "target_gene": ";".join(protein.get("gene_name", []) or []),
                "target_species": str(protein.get("species", "") or ""),
                "target_taxon": taxon,
                "target_sequence": sequence,
                "target_sequence_key": sha256_text(sequence),
                "doi": str(row["_normalized_doi"]),
                "pmid": str(row["_normalized_pmid"]),
                "patent": "",
                "pdb_id": split_pdb_ids(row.get("pdb_id")),
                "mapping_quality": "GLASS2_INCHIKEY+UNIPROT_SEQUENCE",
                "duplicate_group_id": "",
            }
            records.append(complete_record(record))
        frame = pd.DataFrame(records, columns=COMMON_COLUMNS)
        counters[f"glass_{bucket}_eligible_records"] = len(frame)
        return frame

    primary = build("primary", "GLASS2_PRIMARY_IUPHAR_PDSP")
    text_mining = build("text_mining", "GLASS2_EXPLORATORY_TEXT_MINING")
    other_source_counts = Counter(
        str(row.get("source_database") or "") for row in selected_rows["other"]
    )
    other = pd.DataFrame(
        [{"source_database": key, "record_count_before_entity_filters": value} for key, value in other_source_counts.most_common()],
        columns=["source_database", "record_count_before_entity_filters"],
    )
    return primary, text_mining, other


class BatchParquetWriter:
    def __init__(self, path: Path):
        self.path = path
        self.temporary = path.with_suffix(path.suffix + ".tmp")
        self.writer: pq.ParquetWriter | None = None
        self.rows = 0

    def write(self, rows: list[dict]) -> None:
        if not rows:
            return
        frame = pd.DataFrame(rows)
        table = pa.Table.from_pandas(frame, preserve_index=False)
        if self.writer is None:
            self.writer = pq.ParquetWriter(self.temporary, table.schema, compression="zstd")
        self.writer.write_table(table)
        self.rows += len(rows)

    def close(self) -> None:
        if self.writer is None:
            raise RuntimeError(f"No rows written to {self.path}")
        self.writer.close()
        self.temporary.replace(self.path)


def iter_bindingdb_rows(zip_path: Path) -> Iterable[dict]:
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if len(names) != 1:
            raise RuntimeError(f"Expected one TSV in {zip_path}, found {names}")
        with archive.open(names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            for source_row_number, row in enumerate(csv.DictReader(text, delimiter="\t"), start=2):
                row["_source_row_number"] = source_row_number
                yield row


def build_bindingdb(
    articles_zip: Path,
    patents_zip: Path,
    external: pd.DataFrame,
    output_dir: Path,
    counters: Counter,
) -> tuple[Path, Path]:
    external_compounds = set(external["compound_connectivity_key"].dropna()) - {""}
    external_sequences = set(external["target_sequence_key"].dropna()) - {""}
    external_uniprots = set(external["target_uniprot"].dropna()) - {""}
    external_dois = set(external["doi"].dropna()) - {""}
    external_pmids = set(external["pmid"].dropna()) - {""}
    external_pdbs: set[str] = set()
    for value in external["pdb_id"].dropna():
        external_pdbs.update(token for token in str(value).split(";") if token)

    all_path = output_dir / "bindingdb_direct_exact_with_coldboth.parquet"
    keep_path = output_dir / "bindingdb_development_coldboth.parquet"
    all_writer = BatchParquetWriter(all_path)
    keep_writer = BatchParquetWriter(keep_path)
    all_batch: list[dict] = []
    keep_batch: list[dict] = []

    for partition, path in [("ARTICLES", articles_zip), ("PATENTS", patents_zip)]:
        for raw in iter_bindingdb_rows(path):
            counters[f"bindingdb_{partition.lower()}_source_rows"] += 1
            curation = str(raw.get("Curation/DataSource") or "").strip()
            if partition == "ARTICLES" and curation != "Curated from the literature by BindingDB":
                counters["bindingdb_articles_noncanonical_curation"] += 1
                continue
            if partition == "PATENTS" and not str(raw.get("Patent Number") or "").strip():
                counters["bindingdb_patents_missing_patent_number"] += 1
                continue
            endpoints = []
            for endpoint, column in [("Kd", "Kd (nM)"), ("Ki", "Ki (nM)")]:
                value = positive_float(raw.get(column))
                if value is not None:
                    endpoints.append((endpoint, value))
            if not endpoints:
                continue
            counters[f"bindingdb_{partition.lower()}_direct_source_records"] += 1

            chain_count = str(raw.get("Number of Protein Chains in Target (>1 implies a multichain complex)") or "").strip()
            if chain_count != "1":
                counters[f"bindingdb_{partition.lower()}_excluded_not_single_chain"] += len(endpoints)
                continue
            sequence = clean_sequence(raw.get("BindingDB Target Chain Sequence 1"))
            if not sequence:
                counters[f"bindingdb_{partition.lower()}_excluded_missing_sequence"] += len(endpoints)
                continue
            raw_smiles = str(raw.get("Ligand SMILES") or "").strip()
            standardized = standardize_smiles(raw_smiles)
            if not standardized[0]:
                counters[f"bindingdb_{partition.lower()}_excluded_compound_standardization"] += len(endpoints)
                continue

            target_sequence_key = sha256_text(sequence)
            target_uniprot = normalize_uniprot(
                raw.get("UniProt (SwissProt) Primary ID of Target Chain 1")
                or raw.get("UniProt (TrEMBL) Primary ID of Target Chain 1")
            )
            doi = normalize_doi(raw.get("Article DOI"))
            pmid = normalize_pmid(raw.get("PMID"))
            patent = str(raw.get("Patent Number") or "").strip().upper()
            pdb_id = split_pdb_ids(raw.get("PDB ID(s) for Ligand-Target Complex"))
            pdb_tokens = {token for token in pdb_id.split(";") if token}

            exclude_source = bool(
                (doi and doi in external_dois)
                or (pmid and pmid in external_pmids)
                or (pdb_tokens and pdb_tokens.intersection(external_pdbs))
            )
            exclude_compound = standardized[4] in external_compounds
            exclude_target_sequence = target_sequence_key in external_sequences
            exclude_target_uniprot = bool(target_uniprot and target_uniprot in external_uniprots)
            reasons = []
            if exclude_source:
                reasons.append("SOURCE")
            if exclude_compound:
                reasons.append("COMPOUND_CONNECTIVITY")
            if exclude_target_sequence:
                reasons.append("TARGET_SEQUENCE")
            if exclude_target_uniprot and not exclude_target_sequence:
                reasons.append("TARGET_UNIPROT_CONSERVATIVE")
            keep = not reasons

            for endpoint, value in endpoints:
                record = {
                    "record_id": make_record_id("BINDINGDB", partition, raw.get("_source_row_number"), raw.get("BindingDB Reactant_set_id"), endpoint, value),
                    "cohort": "BINDINGDB_DEVELOPMENT_CANDIDATE",
                    "source_partition": f"BindingDB_202608_{partition}",
                    "source_record_id": str(raw.get("BindingDB Reactant_set_id") or ""),
                    "source_database": "BindingDB",
                    "curation_source": curation,
                    "raw_compound_id": str(raw.get("BindingDB MonomerID") or ""),
                    "raw_target_id": str(raw.get("Target Name") or ""),
                    "assay_target_name": str(raw.get("Target Name") or ""),
                    "endpoint": endpoint,
                    "raw_value": value,
                    "raw_unit": "nM",
                    "raw_relation": "=",
                    "p_affinity": 9.0 - math.log10(value),
                    "raw_smiles": raw_smiles,
                    "raw_inchikey": str(raw.get("Ligand InChI Key") or "").strip().upper(),
                    "standardized_smiles": standardized[2],
                    "standardized_inchikey": standardized[3],
                    "compound_connectivity_key": standardized[4],
                    "murcko_scaffold": standardized[5],
                    "fragment_count": standardized[6],
                    "standardization_changed": standardized[7],
                    "target_uniprot": target_uniprot,
                    "target_gene": "",
                    "target_species": str(raw.get("Target Source Organism According to Curator or DataSource") or ""),
                    "target_taxon": "",
                    "target_sequence": sequence,
                    "target_sequence_key": target_sequence_key,
                    "doi": doi,
                    "pmid": pmid,
                    "patent": patent,
                    "pdb_id": pdb_id,
                    "mapping_quality": "BINDINGDB_SINGLE_CHAIN_SEQUENCE",
                    "duplicate_group_id": "",
                }
                record = complete_record(record)
                record.update(
                    {
                        "exclude_source": exclude_source,
                        "exclude_compound_connectivity": exclude_compound,
                        "exclude_target_sequence": exclude_target_sequence,
                        "exclude_target_uniprot_conservative": exclude_target_uniprot and not exclude_target_sequence,
                        "coldboth_keep": keep,
                        "coldboth_exclusion_reason": ";".join(reasons),
                    }
                )
                all_batch.append(record)
                counters["bindingdb_valid_direct_endpoint_rows"] += 1
                if keep:
                    keep_batch.append({column: record[column] for column in COMMON_COLUMNS})
                    counters["bindingdb_coldboth_keep_rows"] += 1
                else:
                    counters["bindingdb_coldboth_excluded_rows"] += 1
                    for reason in reasons:
                        counters[f"bindingdb_reason_{reason}"] += 1
                if len(all_batch) >= 5000:
                    all_writer.write(all_batch)
                    all_batch.clear()
                if len(keep_batch) >= 5000:
                    keep_writer.write(keep_batch)
                    keep_batch.clear()
    all_writer.write(all_batch)
    keep_writer.write(keep_batch)
    all_writer.close()
    keep_writer.close()
    return all_path, keep_path


def entity_sets(frame: pd.DataFrame) -> dict[str, set[str]]:
    pairs = set(zip(frame["compound_connectivity_key"], frame["target_sequence_key"]))
    return {
        "compound": set(frame["compound_connectivity_key"]) - {""},
        "target": set(frame["target_sequence_key"]) - {""},
        "uniprot": set(frame["target_uniprot"]) - {""},
        "doi": set(frame["doi"]) - {""},
        "pmid": set(frame["pmid"]) - {""},
        "pair": pairs,
        "scaffold": set(frame["murcko_scaffold"]) - {""},
    }


def summarize_cohort(name: str, frame: pd.DataFrame) -> dict:
    return {
        "cohort": name,
        "records": len(frame),
        "unique_compound_connectivity": frame["compound_connectivity_key"].nunique(),
        "unique_full_inchikey": frame["standardized_inchikey"].nunique(),
        "unique_target_sequence": frame["target_sequence_key"].nunique(),
        "unique_uniprot": frame.loc[frame["target_uniprot"] != "", "target_uniprot"].nunique(),
        "unique_pairs": frame[["compound_connectivity_key", "target_sequence_key"]].drop_duplicates().shape[0],
        "unique_doi": frame.loc[frame["doi"] != "", "doi"].nunique(),
        "unique_pmid": frame.loc[frame["pmid"] != "", "pmid"].nunique(),
        "kd_records": int((frame["endpoint"] == "Kd").sum()),
        "ki_records": int((frame["endpoint"] == "Ki").sum()),
        "p_affinity_ge_6": int((frame["p_affinity"] >= 6).sum()),
        "p_affinity_ge_7": int((frame["p_affinity"] >= 7).sum()),
        "p_affinity_ge_8": int((frame["p_affinity"] >= 8).sum()),
    }


def build_audits(
    output_dir: Path,
    audit_dir: Path,
    cohorts: dict[str, pd.DataFrame],
    binding_all_path: Path,
    binding_keep_path: Path,
    counters: Counter,
) -> None:
    binding_all = pd.read_parquet(binding_all_path)
    binding_keep = pd.read_parquet(binding_keep_path)
    all_cohorts = {**cohorts, "BINDINGDB_BEFORE_COLDBOTH": binding_all, "BINDINGDB_AFTER_COLDBOTH": binding_keep}
    summary = pd.DataFrame([summarize_cohort(name, frame) for name, frame in all_cohorts.items()])
    summary.to_csv(audit_dir / "cohort_summary.csv", index=False)

    external_names = ["DAVIS_PRIMARY", "METZ_PRIMARY", "GLASS2_PRIMARY_IUPHAR_PDSP"]
    sets = {name: entity_sets(cohorts[name]) for name in external_names}
    overlap_rows = []
    for left in external_names:
        for right in external_names:
            row = {"left": left, "right": right}
            for entity in ["compound", "target", "uniprot", "doi", "pmid", "pair", "scaffold"]:
                row[f"shared_{entity}"] = len(sets[left][entity].intersection(sets[right][entity]))
            overlap_rows.append(row)
    pd.DataFrame(overlap_rows).to_csv(audit_dir / "external_overlap_matrix.csv", index=False)

    external_union = pd.concat([cohorts[name] for name in external_names], ignore_index=True)
    external_sets = entity_sets(external_union)
    development_sets = entity_sets(binding_keep)
    assertions = {
        "compound_connectivity_overlap_after_filter": len(external_sets["compound"] & development_sets["compound"]),
        "target_sequence_overlap_after_filter": len(external_sets["target"] & development_sets["target"]),
        "uniprot_overlap_after_filter": len(external_sets["uniprot"] & development_sets["uniprot"]),
        "doi_overlap_after_filter": len(external_sets["doi"] & development_sets["doi"]),
        "pmid_overlap_after_filter": len(external_sets["pmid"] & development_sets["pmid"]),
        "exact_pair_overlap_after_filter": len(external_sets["pair"] & development_sets["pair"]),
        "shared_murcko_scaffolds_after_filter": len(external_sets["scaffold"] & development_sets["scaffold"]),
    }
    assertions["strict_exact_coldboth_pass"] = all(
        assertions[key] == 0
        for key in [
            "compound_connectivity_overlap_after_filter",
            "target_sequence_overlap_after_filter",
            "uniprot_overlap_after_filter",
            "doi_overlap_after_filter",
            "pmid_overlap_after_filter",
            "exact_pair_overlap_after_filter",
        ]
    )
    (audit_dir / "strict_coldboth_assertions.json").write_text(
        json.dumps(assertions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    exclusion_columns = [
        "exclude_source",
        "exclude_compound_connectivity",
        "exclude_target_sequence",
        "exclude_target_uniprot_conservative",
        "coldboth_keep",
    ]
    exclusion_rows = [{"criterion": column, "record_count": int(binding_all[column].sum())} for column in exclusion_columns]
    exclusion_rows.append({"criterion": "coldboth_excluded_any", "record_count": int((~binding_all["coldboth_keep"]).sum())})
    pd.DataFrame(exclusion_rows).to_csv(audit_dir / "bindingdb_exclusion_waterfall.csv", index=False)

    scaffold_rows = []
    development_scaffolds = development_sets["scaffold"]
    for name in external_names:
        frame = cohorts[name]
        has_scaffold = frame["murcko_scaffold"] != ""
        shared = frame["murcko_scaffold"].isin(development_scaffolds) & has_scaffold
        scaffold_rows.append(
            {
                "cohort": name,
                "records_with_nonempty_scaffold": int(has_scaffold.sum()),
                "records_sharing_development_scaffold": int(shared.sum()),
                "fraction_sharing_among_nonempty": float(shared.sum() / has_scaffold.sum()) if has_scaffold.sum() else None,
                "unique_external_scaffolds": frame.loc[has_scaffold, "murcko_scaffold"].nunique(),
                "unique_shared_scaffolds": len(set(frame.loc[has_scaffold, "murcko_scaffold"]) & development_scaffolds),
            }
        )
    pd.DataFrame(scaffold_rows).to_csv(audit_dir / "murcko_overlap_diagnostic.csv", index=False)

    (audit_dir / "etl_counters.json").write_text(
        json.dumps(dict(sorted(counters.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--audit-dir", required=True)
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    output_dir = (workspace / args.output_dir).resolve()
    audit_dir = (workspace / args.audit_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"Output directory is not empty: {output_dir}")
    if audit_dir.exists() and any(audit_dir.iterdir()):
        raise RuntimeError(f"Audit directory is not empty: {audit_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    inputs = {
        "bindingdb_articles": workspace / "data/raw/BindingDB/202608/BindingDB_BindingDB_Articles_202608_tsv.zip",
        "bindingdb_patents": workspace / "data/raw/BindingDB/202608/BindingDB_Patents_202608_tsv.zip",
        "davis_matrix": workspace / "data/interim/wp1/source_xlsx/supplementary_table_3.xlsx",
        "davis_deepdta": workspace / "data/raw/external/Davis/DeepDTA_derivative/data/davis",
        "metz_matrix": workspace / "data/interim/wp1/source_xlsx/kinome_pKi_with_clusters.xlsx",
        "metz_mapping": workspace / "data/raw/reference/UniProt/2026-08-10/metz_uniprot_selected.tsv",
        "glass_full": workspace / "data/raw/external/GLASS2/2026-01/glass2_full.tsv",
        "glass_ligands": workspace / "data/raw/external/GLASS2/2026-01/ligands.tsv",
        "glass_proteins": workspace / "data/raw/external/GLASS2/2026-01/protein.json",
    }
    for name, path in inputs.items():
        if not path.exists():
            raise FileNotFoundError(f"Missing {name}: {path}")

    counters: Counter = Counter()
    started = time.time()

    davis, davis_full, davis_mapping, davis_target_audit = load_davis(
        inputs["davis_matrix"], inputs["davis_deepdta"], counters
    )
    write_dataframe_atomic(davis, output_dir / "davis_primary.parquet")
    write_dataframe_atomic(davis_full, output_dir / "davis_mapped_full_candidate.parquet")
    davis_mapping.to_csv(audit_dir / "davis_compound_crosswalk.csv", index=False)
    davis_target_audit.to_csv(audit_dir / "davis_target_sequence_resolution.csv", index=False)
    print(f"DAVIS {len(davis):,} records", flush=True)

    metz, metz_mapping_audit = load_metz(inputs["metz_matrix"], inputs["metz_mapping"], counters)
    write_dataframe_atomic(metz, output_dir / "metz_primary.parquet")
    metz_mapping_audit.to_csv(audit_dir / "metz_target_mapping_audit.csv", index=False)
    print(f"METZ {len(metz):,} records", flush=True)

    glass_primary, glass_text, glass_other = load_glass(
        inputs["glass_full"], inputs["glass_ligands"], inputs["glass_proteins"], counters
    )
    write_dataframe_atomic(glass_primary, output_dir / "glass2_primary_iuphar_pdsp.parquet")
    write_dataframe_atomic(glass_text, output_dir / "glass2_exploratory_text_mining.parquet")
    glass_other.to_csv(audit_dir / "glass2_other_source_candidates.csv", index=False)
    print(f"GLASS2 primary {len(glass_primary):,}; text-mining {len(glass_text):,}", flush=True)

    external_primary = pd.concat([davis, metz, glass_primary], ignore_index=True)
    write_dataframe_atomic(external_primary, output_dir / "external_primary_union.parquet")

    binding_all_path, binding_keep_path = build_bindingdb(
        inputs["bindingdb_articles"],
        inputs["bindingdb_patents"],
        external_primary,
        output_dir,
        counters,
    )
    print(
        f"BindingDB valid {counters['bindingdb_valid_direct_endpoint_rows']:,}; "
        f"cold-both keep {counters['bindingdb_coldboth_keep_rows']:,}",
        flush=True,
    )

    cohorts = {
        "DAVIS_PRIMARY": davis,
        "METZ_PRIMARY": metz,
        "GLASS2_PRIMARY_IUPHAR_PDSP": glass_primary,
        "GLASS2_EXPLORATORY_TEXT_MINING": glass_text,
        "EXTERNAL_PRIMARY_UNION": external_primary,
    }
    build_audits(output_dir, audit_dir, cohorts, binding_all_path, binding_keep_path, counters)

    output_files = sorted(path for path in output_dir.iterdir() if path.is_file())
    audit_files = sorted(path for path in audit_dir.iterdir() if path.is_file())
    input_hashes = {}
    for name in ["bindingdb_articles", "bindingdb_patents", "davis_matrix", "metz_matrix", "metz_mapping", "glass_full", "glass_ligands", "glass_proteins"]:
        input_hashes[name] = {"path": str(inputs[name].relative_to(workspace)), "sha256": sha256_file(inputs[name])}
    manifest = {
        "pipeline": "WP1-v1.1",
        "run_date": "2026-08-10",
        "python": sys.version,
        "rdkit": getattr(sys.modules.get("rdkit"), "__version__", "2025.03.5"),
        "pandas": pd.__version__,
        "pyarrow": pa.__version__,
        "elapsed_seconds": round(time.time() - started, 3),
        "inputs": input_hashes,
        "outputs": [
            {"path": str(path.relative_to(workspace)), "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in output_files + audit_files
        ],
    }
    (audit_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "COMPLETED", "elapsed_seconds": manifest["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
