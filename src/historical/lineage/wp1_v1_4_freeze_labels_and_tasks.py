#!/usr/bin/env python3
"""Freeze deduplicated affinity labels and target-endpoint ranking tasks.

This model-blind audit implements WP1-v1.4. It uses only frozen data and label
metadata. No model predictions or XAI outputs are read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow


ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = (6.0, 7.0, 8.0)
K_VALUES = (5, 10, 20, 50)
PRIMARY_THRESHOLD = 7.0
PREFERRED_PRIMARY_K = 10
FALLBACK_PRIMARY_K = 5
CONFLICT_C0_MAX = 0.5
CONFLICT_C1_MAX = 1.0
CONFLICT_C2_MAX = 2.0
ABSOLUTE_TAIL_LOW = 3.0
ABSOLUTE_TAIL_HIGH = 12.0
HARD_VALID_LOW = 0.0
HARD_VALID_HIGH = 15.0
ROBUST_Z_THRESHOLD = 5.0

INPUT_FILES = {
    "BINDINGDB_DEVELOPMENT": (
        "data/processed/wp1/v1_1/bindingdb_development_coldboth.parquet",
        "DEVELOPMENT",
        "H0_EXACT",
    ),
    "DAVIS_H0": (
        "data/processed/wp1/v1_1/davis_primary.parquet",
        "KINASE",
        "H0_EXACT",
    ),
    "METZ_H0": (
        "data/processed/wp1/v1_1/metz_primary.parquet",
        "KINASE",
        "H0_EXACT",
    ),
    "GLASS2_H0": (
        "data/processed/wp1/v1_1/glass2_primary_iuphar_pdsp.parquet",
        "GPCR",
        "H0_EXACT",
    ),
    "DAVIS_H1": (
        "data/processed/wp1/v1_2/davis_h1_primary_hard.parquet",
        "KINASE",
        "H1_PRIMARY_HARD",
    ),
    "METZ_H1": (
        "data/processed/wp1/v1_2/metz_h1_primary_hard.parquet",
        "KINASE",
        "H1_PRIMARY_HARD",
    ),
    "GLASS2_H3": (
        "data/processed/wp1/v1_2/glass2_h3_moderate.parquet",
        "GPCR",
        "H3_MODERATE",
    ),
}

GROUP_KEYS = [
    "analysis_cohort",
    "compound_connectivity_key",
    "target_sequence_key",
    "endpoint",
]


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_doi(value: object) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(".,; ")


def source_reference(row: pd.Series) -> tuple[str, str]:
    doi = normalize_doi(row.get("doi", ""))
    if doi:
        return "DOI", doi
    pmid = clean_text(row.get("pmid", ""))
    if pmid:
        return "PMID", pmid
    return "SOURCE_RECORD", clean_text(row.get("source_record_id", ""))


def validate_source_affinity_scales(raw: pd.DataFrame) -> dict[str, bool]:
    units = raw["raw_unit"].fillna("").astype(str).str.strip().str.lower()
    values = pd.to_numeric(raw["raw_value"], errors="coerce")
    p_affinity = raw["p_affinity"].astype(float)
    nm = units.eq("nm")
    pki = units.eq("pki")
    nm_expected = 9.0 - np.log10(values[nm])
    return {
        "only_supported_source_affinity_scales": bool((nm | pki).all()),
        "nm_values_are_positive": bool((values[nm] > 0).all()),
        "nm_conversion_matches_paffinity": bool(
            np.allclose(nm_expected, p_affinity[nm], rtol=0.0, atol=1e-6)
        ),
        "pki_rows_are_ki_endpoint": bool((raw.loc[pki, "endpoint"] == "Ki").all()),
        "pki_value_matches_paffinity": bool(
            np.allclose(values[pki], p_affinity[pki], rtol=0.0, atol=1e-6)
        ),
    }


def join_unique(values: pd.Series) -> str:
    return ";".join(sorted({clean_text(value) for value in values if clean_text(value)}))


def preferred_representation(values: pd.Series) -> str:
    cleaned = values.dropna().astype(str)
    cleaned = cleaned[cleaned != ""]
    if cleaned.empty:
        return ""
    counts = cleaned.value_counts()
    maximum = counts.max()
    return sorted(counts[counts == maximum].index)[0]


def conflict_level(span: float) -> str:
    if span <= CONFLICT_C0_MAX:
        return "C0"
    if span <= CONFLICT_C1_MAX:
        return "C1"
    if span <= CONFLICT_C2_MAX:
        return "C2"
    return "C3"


def load_inputs() -> tuple[dict[str, pd.DataFrame], dict[str, Path]]:
    frames: dict[str, pd.DataFrame] = {}
    paths: dict[str, Path] = {}
    required = {
        "record_id",
        "source_record_id",
        "source_database",
        "compound_connectivity_key",
        "target_sequence_key",
        "endpoint",
        "p_affinity",
        "raw_relation",
        "raw_unit",
        "standardized_smiles",
        "murcko_scaffold",
        "target_sequence",
        "target_uniprot",
        "target_gene",
        "doi",
        "pmid",
    }
    for cohort, (relative_path, domain, hard_level) in INPUT_FILES.items():
        path = ROOT / relative_path
        if not path.exists():
            raise FileNotFoundError(path)
        frame = pd.read_parquet(path)
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{cohort} missing columns: {sorted(missing)}")
        frame = frame.copy()
        frame["analysis_cohort"] = cohort
        frame["analysis_domain"] = domain
        frame["hard_level"] = hard_level
        frames[cohort] = frame
        paths[cohort] = path
    return frames, paths


def make_evidence_units(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    references = work.apply(source_reference, axis=1, result_type="expand")
    references.columns = ["source_reference_type", "source_reference_id"]
    work = pd.concat([work, references], axis=1)
    work["p_affinity_rounded_6"] = work["p_affinity"].astype(float).round(6)
    evidence_columns = GROUP_KEYS + [
        "p_affinity_rounded_6",
        "source_database",
        "source_reference_type",
        "source_reference_id",
    ]
    work["evidence_unit_id"] = work[evidence_columns].astype(str).agg("|".join, axis=1).map(
        sha256_text
    )
    work = work.sort_values(["evidence_unit_id", "record_id"]).reset_index(drop=True)
    counts = work.groupby("evidence_unit_id").size().rename("raw_record_count")
    record_ids = work.groupby("evidence_unit_id")["record_id"].agg(join_unique).rename(
        "raw_record_ids"
    )
    units = work.drop_duplicates("evidence_unit_id", keep="first").copy()
    units = units.merge(counts, on="evidence_unit_id", validate="one_to_one")
    units = units.merge(record_ids, on="evidence_unit_id", validate="one_to_one")
    units["p_affinity"] = units["p_affinity_rounded_6"]
    return units.drop(columns=["p_affinity_rounded_6"])


def make_group_labels(evidence: pd.DataFrame) -> pd.DataFrame:
    evidence = evidence.copy()
    evidence["independent_document_id"] = np.where(
        evidence["source_reference_type"].isin(["DOI", "PMID"]),
        evidence["source_reference_id"],
        "",
    )
    statistical = (
        evidence.groupby(GROUP_KEYS, as_index=False)
        .agg(
            evidence_units=("evidence_unit_id", "nunique"),
            raw_records=("raw_record_count", "sum"),
            source_references=("source_reference_id", "nunique"),
            independent_documents=(
                "independent_document_id",
                lambda values: int(values[values != ""].nunique()),
            ),
            p_affinity_min=("p_affinity", "min"),
            p_affinity_q1=("p_affinity", lambda values: float(values.quantile(0.25))),
            p_affinity_median=("p_affinity", "median"),
            p_affinity_q3=("p_affinity", lambda values: float(values.quantile(0.75))),
            p_affinity_max=("p_affinity", "max"),
            p_affinity_mean=("p_affinity", "mean"),
            p_affinity_standard_deviation=("p_affinity", "std"),
            source_databases=("source_database", join_unique),
            source_reference_ids=("source_reference_id", join_unique),
            record_ids=("raw_record_ids", join_unique),
        )
    )
    representations = (
        evidence.groupby(GROUP_KEYS, as_index=False)
        .agg(
            standardized_smiles=("standardized_smiles", preferred_representation),
            standardized_smiles_count=("standardized_smiles", "nunique"),
            murcko_scaffold=("murcko_scaffold", preferred_representation),
            target_sequence=("target_sequence", preferred_representation),
            target_sequence_count=("target_sequence", "nunique"),
            target_uniprot=("target_uniprot", join_unique),
            target_gene=("target_gene", join_unique),
            analysis_domain=("analysis_domain", preferred_representation),
            hard_level=("hard_level", preferred_representation),
        )
    )
    groups = statistical.merge(representations, on=GROUP_KEYS, validate="one_to_one")
    groups["label_group_id"] = groups[GROUP_KEYS].astype(str).agg("|".join, axis=1).map(
        sha256_text
    )
    groups["p_affinity_iqr"] = groups["p_affinity_q3"] - groups["p_affinity_q1"]
    groups["p_affinity_span"] = groups["p_affinity_max"] - groups["p_affinity_min"]
    groups["conflict_level"] = groups["p_affinity_span"].map(conflict_level)
    groups["hard_invalid_affinity"] = (
        (groups["p_affinity_min"] < HARD_VALID_LOW)
        | (groups["p_affinity_max"] > HARD_VALID_HIGH)
        | ~np.isfinite(groups["p_affinity_median"])
    )
    groups["absolute_tail_flag"] = (
        (groups["p_affinity_median"] < ABSOLUTE_TAIL_LOW)
        | (groups["p_affinity_median"] > ABSOLUTE_TAIL_HIGH)
    )
    groups["primary_continuous_keep"] = (
        groups["conflict_level"].isin(["C0", "C1"])
        & ~groups["hard_invalid_affinity"]
    )

    robust_parts = []
    for _, subset in groups.groupby(["analysis_cohort", "endpoint"], sort=False):
        subset = subset.copy()
        center = float(subset["p_affinity_median"].median())
        mad = float((subset["p_affinity_median"] - center).abs().median())
        subset["cohort_endpoint_median"] = center
        subset["cohort_endpoint_mad"] = mad
        if mad == 0.0 or not math.isfinite(mad):
            subset["robust_modified_z"] = np.nan
        else:
            subset["robust_modified_z"] = (
                0.6745 * (subset["p_affinity_median"] - center) / mad
            )
        robust_parts.append(subset)
    groups = pd.concat(robust_parts, ignore_index=True)
    groups["robust_distribution_tail_flag"] = (
        groups["robust_modified_z"].abs() > ROBUST_Z_THRESHOLD
    ).fillna(False)

    for threshold in THRESHOLDS:
        slug = str(int(threshold))
        groups[f"crosses_paffinity_{slug}"] = (
            (groups["p_affinity_min"] < threshold)
            & (groups["p_affinity_max"] >= threshold)
        )
        groups[f"binary_label_paffinity_{slug}"] = (
            groups["p_affinity_median"] >= threshold
        )
        groups[f"binary_keep_paffinity_{slug}"] = (
            groups["primary_continuous_keep"]
            & ~groups[f"crosses_paffinity_{slug}"]
        )
    return groups.sort_values(GROUP_KEYS).reset_index(drop=True)


def make_task_grid(groups: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    detail_rows: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []
    for cohort, cohort_groups in groups.groupby("analysis_cohort", sort=True):
        domain = cohort_groups["analysis_domain"].iloc[0]
        hard_level = cohort_groups["hard_level"].iloc[0]
        for threshold in THRESHOLDS:
            slug = str(int(threshold))
            eligible = cohort_groups[cohort_groups[f"binary_keep_paffinity_{slug}"]].copy()
            for candidate_k in K_VALUES:
                current_detail = []
                for (target_key, endpoint), task in eligible.groupby(
                    ["target_sequence_key", "endpoint"], sort=True
                ):
                    candidate_count = int(task["compound_connectivity_key"].nunique())
                    positives = int(task[f"binary_label_paffinity_{slug}"].sum())
                    negatives = int(len(task) - positives)
                    evaluable = bool(
                        candidate_count >= candidate_k and positives > 0 and negatives > 0
                    )
                    row = {
                        "analysis_cohort": cohort,
                        "analysis_domain": domain,
                        "hard_level": hard_level,
                        "target_sequence_key": target_key,
                        "target_uniprot": join_unique(task["target_uniprot"]),
                        "target_gene": join_unique(task["target_gene"]),
                        "endpoint": endpoint,
                        "diagnostic_paffinity_threshold": threshold,
                        "candidate_k": candidate_k,
                        "candidate_count": candidate_count,
                        "positive_count": positives,
                        "negative_count": negatives,
                        "maximum_attainable_recall_at_k": (
                            min(candidate_k, positives) / positives if positives else np.nan
                        ),
                        "random_expected_recall_at_k": (
                            min(candidate_k, candidate_count) / candidate_count
                            if candidate_count
                            else np.nan
                        ),
                        "evaluable_recall_at_k": evaluable,
                    }
                    current_detail.append(row)
                    detail_rows.append(row)
                detail_frame = pd.DataFrame(current_detail)
                if detail_frame.empty:
                    tasks_total = tasks_with_k = tasks_both = tasks_evaluable = 0
                    candidate_groups = positive_groups = negative_groups = 0
                else:
                    tasks_total = len(detail_frame)
                    tasks_with_k = int((detail_frame["candidate_count"] >= candidate_k).sum())
                    tasks_both = int(
                        ((detail_frame["positive_count"] > 0) & (detail_frame["negative_count"] > 0)).sum()
                    )
                    tasks_evaluable = int(detail_frame["evaluable_recall_at_k"].sum())
                    candidate_groups = int(len(eligible))
                    positive_groups = int(eligible[f"binary_label_paffinity_{slug}"].sum())
                    negative_groups = candidate_groups - positive_groups
                summary_rows.append(
                    {
                        "analysis_cohort": cohort,
                        "analysis_domain": domain,
                        "hard_level": hard_level,
                        "diagnostic_paffinity_threshold": threshold,
                        "candidate_k": candidate_k,
                        "eligible_candidate_groups": candidate_groups,
                        "positive_groups": positive_groups,
                        "negative_groups": negative_groups,
                        "target_endpoint_tasks_total": tasks_total,
                        "tasks_with_at_least_k_candidates": tasks_with_k,
                        "tasks_with_both_classes": tasks_both,
                        "tasks_evaluable_recall_at_k": tasks_evaluable,
                        "threshold_is_primary": threshold == PRIMARY_THRESHOLD,
                        "k_is_preferred_primary": candidate_k == PREFERRED_PRIMARY_K,
                    }
                )
    return pd.DataFrame(detail_rows), pd.DataFrame(summary_rows)


def make_conflict_summary(groups: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (cohort, endpoint), subset in groups.groupby(
        ["analysis_cohort", "endpoint"], sort=True
    ):
        row: dict[str, object] = {
            "analysis_cohort": cohort,
            "endpoint": endpoint,
            "label_groups": len(subset),
            "groups_with_multiple_evidence_units": int((subset["evidence_units"] > 1).sum()),
        }
        for level in ("C0", "C1", "C2", "C3"):
            row[f"groups_{level.lower()}"] = int((subset["conflict_level"] == level).sum())
        row["primary_continuous_keep"] = int(subset["primary_continuous_keep"].sum())
        row["hard_invalid_affinity"] = int(subset["hard_invalid_affinity"].sum())
        row["absolute_tail_flag"] = int(subset["absolute_tail_flag"].sum())
        row["robust_distribution_tail_flag"] = int(
            subset["robust_distribution_tail_flag"].sum()
        )
        for threshold in THRESHOLDS:
            slug = str(int(threshold))
            row[f"groups_crossing_paffinity_{slug}"] = int(
                subset[f"crosses_paffinity_{slug}"].sum()
            )
            row[f"binary_keep_paffinity_{slug}"] = int(
                subset[f"binary_keep_paffinity_{slug}"].sum()
            )
        rows.append(row)
    return pd.DataFrame(rows)


def make_evidence_summary(raw: pd.DataFrame, evidence: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cohort in sorted(raw["analysis_cohort"].unique()):
        raw_subset = raw[raw["analysis_cohort"] == cohort]
        evidence_subset = evidence[evidence["analysis_cohort"] == cohort]
        rows.append(
            {
                "analysis_cohort": cohort,
                "raw_records": len(raw_subset),
                "evidence_units": len(evidence_subset),
                "collapsed_duplicate_records": len(raw_subset) - len(evidence_subset),
                "evidence_units_with_raw_duplicates": int(
                    (evidence_subset["raw_record_count"] > 1).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def select_primary_k(task_summary: pd.DataFrame) -> tuple[int | None, str, dict[str, int]]:
    counts: dict[str, int] = {}
    for candidate_k in (PREFERRED_PRIMARY_K, FALLBACK_PRIMARY_K):
        subset = task_summary[
            (task_summary["diagnostic_paffinity_threshold"] == PRIMARY_THRESHOLD)
            & (task_summary["candidate_k"] == candidate_k)
            & (task_summary["analysis_cohort"].isin(["DAVIS_H1", "METZ_H1"]))
        ]
        by_cohort = dict(
            zip(subset["analysis_cohort"], subset["tasks_evaluable_recall_at_k"])
        )
        davis = int(by_cohort.get("DAVIS_H1", 0))
        metz = int(by_cohort.get("METZ_H1", 0))
        combined = davis + metz
        counts[f"davis_h1_p7_k{candidate_k}"] = davis
        counts[f"metz_h1_p7_k{candidate_k}"] = metz
        counts[f"kinase_h1_p7_k{candidate_k}"] = combined
        if combined >= 20 and davis >= 5 and metz >= 5:
            decision = "GO_LABEL_FREEZE" if candidate_k == PREFERRED_PRIMARY_K else "CONDITIONAL_K5"
            return candidate_k, decision, counts
    return None, "NO_GO_MODEL_TRAINING", counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", default=str(ROOT / "data/processed/wp1/v1_4")
    )
    parser.add_argument("--audit-dir", default=str(ROOT / "audit/wp1/v1_4"))
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    audit_dir = Path(args.audit_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    frames, paths = load_inputs()
    raw = pd.concat(frames.values(), ignore_index=True)
    evidence = pd.concat(
        [make_evidence_units(frame) for frame in frames.values()], ignore_index=True
    )
    groups = make_group_labels(evidence)
    task_detail, task_summary = make_task_grid(groups)
    conflict_summary = make_conflict_summary(groups)
    evidence_summary = make_evidence_summary(raw, evidence)
    selected_k, gate_decision, gate_counts = select_primary_k(task_summary)

    relation_values = sorted({clean_text(value) for value in raw["raw_relation"].dropna()})
    unit_values = sorted({clean_text(value).lower() for value in raw["raw_unit"].dropna()})
    endpoint_values = sorted({clean_text(value) for value in raw["endpoint"].dropna()})
    scale_assertions = validate_source_affinity_scales(raw)
    assertions = {
        "model_information_absent": True,
        "all_p_affinity_finite": bool(np.isfinite(raw["p_affinity"].astype(float)).all()),
        "only_exact_relations": bool(set(relation_values) <= {"", "="}),
        **scale_assertions,
        "only_kd_ki_endpoints": bool(set(endpoint_values) <= {"Kd", "Ki"}),
        "evidence_units_not_more_than_raw_records": bool(len(evidence) <= len(raw)),
        "group_keys_unique": bool(~groups.duplicated(GROUP_KEYS).any()),
        "all_group_target_sequences_unique": bool((groups["target_sequence_count"] == 1).all()),
        "primary_continuous_excludes_c2_c3": bool(
            (~groups["primary_continuous_keep"] | groups["conflict_level"].isin(["C0", "C1"])).all()
        ),
        "primary_binary_excludes_crossing_p7": bool(
            (~groups["binary_keep_paffinity_7"] | ~groups["crosses_paffinity_7"]).all()
        ),
        "task_rows_unique": bool(
            not task_detail.duplicated(
                [
                    "analysis_cohort",
                    "target_sequence_key",
                    "endpoint",
                    "diagnostic_paffinity_threshold",
                    "candidate_k",
                ]
            ).any()
        ),
        "preferred_or_fallback_gate_resolved": bool(selected_k in {5, 10}),
    }
    assertions["all_assertions_pass"] = bool(all(assertions.values()))

    evidence.to_parquet(output_dir / "affinity_evidence_units.parquet", index=False)
    groups.to_parquet(output_dir / "affinity_group_labels.parquet", index=False)
    task_detail.to_parquet(output_dir / "target_endpoint_task_grid.parquet", index=False)
    evidence_summary.to_csv(audit_dir / "evidence_deduplication_summary.csv", index=False)
    conflict_summary.to_csv(audit_dir / "label_conflict_and_tail_summary.csv", index=False)
    task_summary.to_csv(audit_dir / "target_endpoint_ranking_feasibility_grid.csv", index=False)
    task_detail[
        (task_detail["diagnostic_paffinity_threshold"] == PRIMARY_THRESHOLD)
        & (task_detail["candidate_k"] == (selected_k or FALLBACK_PRIMARY_K))
    ].to_csv(audit_dir / "primary_target_endpoint_tasks.csv", index=False)
    groups[groups["conflict_level"].isin(["C2", "C3"])].sort_values(
        "p_affinity_span", ascending=False
    ).to_csv(audit_dir / "severe_label_conflicts.csv", index=False)
    groups[
        groups["absolute_tail_flag"] | groups["robust_distribution_tail_flag"]
    ].sort_values(["analysis_cohort", "endpoint", "p_affinity_median"]).to_csv(
        audit_dir / "affinity_tail_flags.csv", index=False
    )

    frozen_config = {
        "rule_version": "WP1-v1.4",
        "primary_continuous_metric": "MAE",
        "continuous_secondary_metrics": ["RMSE", "Spearman", "Concordance Index"],
        "primary_affinity_threshold": PRIMARY_THRESHOLD,
        "primary_affinity_interpretation": "Kd/Ki <= 100 nM",
        "sensitivity_affinity_thresholds": [6.0, 8.0],
        "primary_k": selected_k,
        "sensitivity_k": [value for value in (5, 20) if value != selected_k],
        "descriptive_only_k": 50,
        "ranking_task_unit": "cohort x target_sequence_key x endpoint",
        "primary_ranking_metric": f"macro Delta Recall@{selected_k}" if selected_k else None,
        "conflict_cutoffs": {
            "C0_max_inclusive": CONFLICT_C0_MAX,
            "C1_max_inclusive": CONFLICT_C1_MAX,
            "C2_max_inclusive": CONFLICT_C2_MAX,
            "C3": "greater than C2_max_inclusive",
        },
        "gate_decision": gate_decision,
        "gate_counts": gate_counts,
        "model_information_used": False,
        "external_predictions_used": False,
    }
    (audit_dir / "frozen_label_task_config.json").write_text(
        json.dumps(frozen_config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    manifest = {
        "rule_version": "WP1-v1.4",
        "gate_decision": gate_decision,
        "selected_primary_k": selected_k,
        "started_unix": started,
        "completed_unix": time.time(),
        "elapsed_seconds": time.time() - started,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "pandas_version": pd.__version__,
        "numpy_version": np.__version__,
        "pyarrow_version": pyarrow.__version__,
        "input_sha256": {
            str(path.relative_to(ROOT)): sha256_file(path) for path in paths.values()
        },
        "input_counts": {cohort: len(frame) for cohort, frame in frames.items()},
        "output_counts": {
            "raw_rows_across_analysis_views": len(raw),
            "evidence_units_across_analysis_views": len(evidence),
            "label_groups_across_analysis_views": len(groups),
            "task_grid_rows": len(task_detail),
        },
        "observed_values": {
            "raw_relation": relation_values,
            "raw_unit": unit_values,
            "endpoint": endpoint_values,
        },
        "assertions": assertions,
        "information_use": {
            "model_information_used": False,
            "external_predictions_used": False,
            "xai_outputs_used": False,
        },
    }
    (audit_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if not assertions["all_assertions_pass"]:
        raise AssertionError(json.dumps(assertions, indent=2))

    output_paths = [
        output_dir / "affinity_evidence_units.parquet",
        output_dir / "affinity_group_labels.parquet",
        output_dir / "target_endpoint_task_grid.parquet",
        audit_dir / "evidence_deduplication_summary.csv",
        audit_dir / "label_conflict_and_tail_summary.csv",
        audit_dir / "target_endpoint_ranking_feasibility_grid.csv",
        audit_dir / "primary_target_endpoint_tasks.csv",
        audit_dir / "severe_label_conflicts.csv",
        audit_dir / "affinity_tail_flags.csv",
        audit_dir / "frozen_label_task_config.json",
        audit_dir / "run_manifest.json",
    ]
    checksum_lines = [
        f"{sha256_file(path)}  {path.relative_to(ROOT)}" for path in output_paths
    ]
    (audit_dir / "checksums.sha256").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )

    print(evidence_summary.to_string(index=False), flush=True)
    print(conflict_summary.to_string(index=False), flush=True)
    print(
        task_summary[
            (task_summary["diagnostic_paffinity_threshold"] == PRIMARY_THRESHOLD)
            & (task_summary["candidate_k"].isin([5, 10, 20]))
        ].to_string(index=False),
        flush=True,
    )
    print(json.dumps(frozen_config, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
