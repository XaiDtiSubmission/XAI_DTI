"""Verify the archived MODEL_GATE risk values using the published frozen inputs."""

import csv
import gzip
import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
AF18 = ROOT / "aggregates/AF18"
GATE = ROOT / "saved_rows/model_gate"
PAYLOAD = ROOT / "payload"
ENDPOINTS = ("Kd", "Ki")


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as data:
        return list(csv.DictReader(data))


def study_inputs():
    with gzip.open(GATE / "frozen_labels.csv.gz", "rt", encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    with gzip.open(GATE / "identity_order.jsonl.gz", "rt", encoding="utf-8") as source:
        registered = [json.loads(line) for line in source]
    fields = ("label_group_id", "pair_group_id", "endpoint")
    if len(rows) != 5697 or len(registered) != len(rows):
        raise ValueError("Unexpected MODEL_GATE identity count")
    for index, (row, registry) in enumerate(zip(rows, registered)):
        if (int(row["score_index0"]) != index or registry["score_index0"] != index
                or any(row[field] != registry[field] for field in fields)):
            raise ValueError(f"Different label and saved score identities at index {index}")
    removed = {row["label_group_id"] for row in read_csv(AF18 / "record_disposition.csv")
               if row["role"] == "MODEL_GATE" and row["group_excluded_under_this_disposition"] == "True"}
    if len(removed) != 77:
        raise ValueError("Expected 77 gated groups withdrawn by source adjudication")
    truth = np.array([float.fromhex(r["y_true_pAffinity_binary64_hex"]) for r in rows], dtype=np.float64)
    if not np.isfinite(truth).all():
        raise ValueError("Nonfinite frozen labels")
    endpoint = np.array([row["endpoint"] for row in rows])
    keep = np.array([row["label_group_id"] not in removed for row in rows], dtype=bool)
    return truth, endpoint, keep


def score(case, name, member, size):
    with np.load(PAYLOAD / "saved_scores" / case / name, allow_pickle=False) as archive:
        values = archive[member]
    if values.dtype != np.dtype("float32") or values.shape != (size,):
        raise ValueError(f"Unexpected stored score shape or precision for {case}/{name}")
    return values.astype(np.float64)


def apply(values, parameters):
    alpha, beta, *hinges = parameters["coefficients"]
    fitted = alpha * values + beta
    for slope, knot in zip(hinges, parameters["knots"]):
        fitted += slope * np.maximum(values - knot, 0.0)
    return fitted


def risk_terms(truth, h0, h1, affine, piecewise):
    mae = {f"MAE_{key}": float(np.abs(values - truth).mean())
           for key, values in (("h0", h0), ("h_aff", affine), ("h_1d", piecewise), ("h1", h1))}
    return {**mae, "J_aff": mae["MAE_h_aff"] - mae["MAE_h1"], "J_1d": mae["MAE_h_1d"] - mae["MAE_h1"]}


def main():
    truth, endpoint, keep = study_inputs()
    slots = json.loads((PAYLOAD / "calibration/calibrators_40_slots.json").read_text(encoding="utf-8"))["slots"]
    parameters = {(x["case_id"], x["endpoint"], x["family"]): x for x in slots}
    if len(parameters) != 40:
        raise ValueError("Unexpected number of calibration slots")
    expected = {(r["case_id"], r["endpoint_layer"]): r for r in read_csv(AF18 / "gate_by_fit_endpoint.csv")
                if r["endpoint_layer"] in ENDPOINTS}
    cases = sorted({case for case, _ in expected})
    if len(cases) != 10 or len(expected) != 20:
        raise ValueError("Unexpected saved comparison coverage")
    maximum = 0.0
    checks = 0
    for case in cases:
        h0 = score(case, "h0_predictions.npz", "MODEL_GATE_old", len(truth))
        h1 = score(case, "h1_high_lr_predictions.npz", "MODEL_GATE_new", len(truth))
        for end in ENDPOINTS:
            original = endpoint == end
            affine = apply(h0[original], parameters[case, end, "affine"])
            piecewise = apply(h0[original], parameters[case, end, "piecewise"])
            expected_row = expected[case, end]
            for state, mask in (("original", np.ones(original.sum(), dtype=bool)), ("retained", keep[original])):
                if mask.sum() != int(expected_row[state + "_groups"]):
                    raise ValueError(f"Unexpected {state} denominator for {case}/{end}")
                values = risk_terms(truth[original][mask], h0[original][mask], h1[original][mask],
                                    affine[mask], piecewise[mask])
                for name, actual in values.items():
                    saved = float(expected_row[f"{state}_{name}"])
                    delta = abs(actual - saved)
                    if not np.isfinite(delta) or delta > 1e-12:
                        raise ValueError(f"Saved value did not reproduce: {case}/{end}/{state}/{name}")
                    maximum = max(maximum, delta)
                    checks += 1
    print(f"PASS {checks} archived GATE metrics; max absolute difference {maximum:.3g}")


if __name__ == "__main__":
    main()
