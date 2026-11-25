"""Check Pack 01 file identity, access subset, and saved case-to-arm arithmetic."""

import csv
import gzip
import hashlib
import json
from pathlib import Path
from statistics import fmean


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "saved_rows/pack01"
FUNCTIONS = ("h0", "h1", "h_aff", "h_1d")
FIELDS = (*FUNCTIONS, "A", "J_aff", "J_1d")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, error):
    if not condition:
        raise ValueError(error)


def identities(manifest):
    for artifact in manifest["artifacts"]:
        file = PACK / artifact["path"]
        body = file.read_bytes()
        require(len(body) == artifact["bytes"] and hashlib.sha256(body).hexdigest() == artifact["sha256"],
                f"Unexpected archive identity: {artifact['path']}")
    rows = read(PACK / "row_keys.json")["rows"]
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    require(len(rows) == 5000 and hashlib.sha256(encoded).hexdigest() == manifest["row_digest"],
            "Pack 01 ordered keys do not match its original seal")

    with gzip.open(PACK / "pure_papyrus_inputs.csv.gz", "rt", encoding="utf-8", newline="") as content:
        subset = [(r["sample_id"], r["endpoint"]) for r in csv.DictReader(content)]
    full = [tuple(row) for row in rows]
    selected = set(subset)
    scope = manifest["partial_input_access"]
    require(len(subset) == len(selected) == scope["included_rows"], "Unexpected partial input count")
    require([key for key in full if key in selected] == subset, "Partial inputs changed original panel ordering")
    require(len(full) - len(subset) == scope["excluded_rows"], "Invalid held-out count")
    return len(rows), len(subset)


def aggregate_checks():
    path = ROOT / "aggregates/AF09/external_prediction_values.csv"
    with path.open(encoding="utf-8", newline="") as source:
        table = {(r["arm"], r["seed_layer"], r["endpoint_layer"]): r for r in csv.DictReader(source)}
    require(len(table) == 32, "Unexpected saved case/endpoint/arm counts")
    checks = []
    for arm in ("G", "S"):
        seeds = ("17", "29", "43", "71", "101")
        for seed in seeds:
            for key in FIELDS:
                calculated = fmean(float(table[arm, seed, end][key]) for end in ("Kd", "Ki"))
                checks.append(abs(calculated - float(table[arm, seed, "EBMAE"][key])))
        for key in FIELDS:
            calculated = fmean(float(table[arm, seed, "EBMAE"][key]) for seed in seeds)
            checks.append(abs(calculated - float(table[arm, "MEAN5", "EBMAE"][key])))
    maximum = max(checks)
    require(maximum <= 1e-12, "Archived endpoint and arm calculations disagree")
    return len(checks), maximum


def main():
    manifest = read(PACK / "manifest.json")
    full, subset = identities(manifest)
    checks, delta = aggregate_checks()
    print(f"PASS {len(manifest['artifacts'])} Pack 01 objects; {full} ordered prediction rows; "
          f"{subset} available input rows; {checks} archived aggregate comparisons (max difference {delta:.3g})")


if __name__ == "__main__":
    main()
