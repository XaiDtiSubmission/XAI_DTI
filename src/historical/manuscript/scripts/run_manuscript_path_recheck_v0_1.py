"""Run the authorized, finite six-span manuscript path recheck."""

import argparse
import collections
import contextlib
import datetime
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "planning/manuscript_path_recheck_v0_1.json"
CONTRACT = ROOT / "docs/新路径面板复核核心执行合同_v0.1.md"
HANDOFF = ROOT / "inherited/manuscript_handoff_v0_1"
PRIOR_REL = (
    "snapshots/bottleneck/protocol/smiles_gine_bottleneck_fr40_path_span_v0_2/contract.json"
)
RUN_DIR = ROOT / "output/manuscript_path_recheck_v0_1/run_001"
PYTHON = ROOT / "research/mechanism_v0_2/runtime_v0_1/bin/python"
EPS = ("Kd", "Ki")
OPS = ("D", "P", "DP")
FUN = ("q", "h0", "h_aff", "h1")
HEADS = ("h0", "h1")
POLICIES = ("FIT_ROW_UNIFORM", "FIT_PROTEIN_INPUT_UNIFORM")
SPANS = (1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2, 1.0)
SEEDS = (17, 29, 43, 71, 101)
GIB = 2**30
HASH_DOMAIN = "DTI-MANUSCRIPT-FR-PATH-RECHECK-v0.1"
AUTHORITY = "按照执行合同和修改方案，开始执行第三项的追加实验"
TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[-+0-9.eE]+|true|false|null|[{}\[\],:]')
STORAGE = re.compile(r"[^/]+/data/[0-9]+")


class Stop(RuntimeError):
    """A required identity, qualification, resource or recorded cap failed."""


def require(ok, reason):
    if not ok:
        raise Stop(reason)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def encoded(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_descriptor(path):
    with path.open("rb") as f:
        size = os.fstat(f.fileno()).st_size
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    return {"path": str(path), "bytes": size, "sha256": digest}


def load_control(path):
    return json.loads(path.read_bytes())


def order_digest(key):
    return hashlib.sha256(encoded(key)).digest()


SPACE = re.compile(r"\s*")


def json_end(text, i):
    """Skip one JSON value lexically, without decoding excluded numeric fields."""
    stack = []
    while True:
        i = SPACE.match(text, i).end()
        match = TOKEN.match(text, i)
        require(match is not None, "JSON_LEXICAL_TOKEN")
        token = match.group()
        i = match.end()
        if token in ("{", "["):
            stack.append("}" if token == "{" else "]")
        elif token in ("}", "]"):
            require(stack and stack.pop() == token, "JSON_LEXICAL_CONTAINER")
            if not stack:
                return i
        elif not stack:
            require(token not in (",", ":"), "JSON_VALUE_EXPECTED")
            return i


def json_members(text, start=0, object_mode=True):
    """Yield key and value spans for a previously sealed JSON object or array."""
    opening, closing = "{}", "[]"
    delimiters = opening if object_mode else closing
    decoder = json.JSONDecoder()
    i = start
    require(text[i] == delimiters[0], "JSON_MEMBER_OPEN")
    i += 1
    seen = set()
    while True:
        while text[i].isspace():
            i += 1
        if text[i] == delimiters[1]:
            return
        key = None
        if object_mode:
            key, i = decoder.raw_decode(text, i)
            require(isinstance(key, str) and key not in seen, "JSON_DUPLICATE_KEY")
            seen.add(key)
            while text[i].isspace():
                i += 1
            require(text[i] == ":", "JSON_MEMBER_COLON")
            i += 1
            while text[i].isspace():
                i += 1
        end = json_end(text, i)
        yield key, (i, end)
        i = end
        while text[i].isspace():
            i += 1
        if text[i] == delimiters[1]:
            return
        require(text[i] == ",", "JSON_MEMBER_COMMA")
        i += 1


def project(line, wanted):
    return {k: json.loads(line[a:b]) for k, (a, b) in json_members(line) if k in wanted}


def npy_view(np, raw, shape, dtype):
    f = io.BytesIO(raw)
    require(np.lib.format.read_magic(f) == (1, 0), "NPY_VERSION")
    found_shape, fortran, found_type = np.lib.format.read_array_header_1_0(f)
    dtype = np.dtype(dtype)
    require(found_shape == shape and not fortran and found_type == dtype, "NPY_SHAPE_OR_DTYPE")
    require(len(raw) == f.tell() + math.prod(shape) * dtype.itemsize, "NPY_SIZE")
    return np.ndarray(shape, dtype=dtype, buffer=raw, offset=f.tell())


def clock_guard(binding):
    require(time.time() < binding["wall_deadline"] and time.monotonic() < binding["monotonic_deadline"],
            "7200_SECOND_HARD_STOP")


def resources(psutil, pids):
    total = 0
    for pid in set(pids):
        try:
            total += psutil.Process(pid).memory_info().rss
        except psutil.NoSuchProcess:
            continue
    return {
        "rss_bytes": total,
        "available_bytes": psutil.virtual_memory().available,
        "free_disk_bytes": psutil.disk_usage(str(ROOT)).free,
        "mps_driver_bytes": 0,
        "mps_basis": "CPU-only process, no device initialization or allocation",
    }


def check_resources(v):
    require(v["rss_bytes"] <= 36 * GIB, "RSS_LIMIT")
    require(v["available_bytes"] >= 4 * GIB, "AVAILABLE_MEMORY_LIMIT")
    require(v["free_disk_bytes"] >= 20 * GIB, "DISK_LIMIT")


class Artifacts:
    def __init__(self, binding):
        self.binding = binding
        self.hashes = {}

    def emit(self, name, obj):
        self.write(name, encoded(obj) + b"\n")

    def write(self, name, raw):
        clock_guard(self.binding)
        path = RUN_DIR / name
        require(path.name == name and not path.exists(), "IMMUTABLE_OUTPUT_NAME")
        used = sum(f.stat().st_size for f in RUN_DIR.iterdir() if f.is_file())
        require(len(list(RUN_DIR.iterdir())) < 39 and used + len(raw) <= 127 * 2**20,
                "OUTPUT_CAP_RESERVED_FOR_RECEIPTS")
        with path.open("xb") as f:
            require(f.write(raw) == len(raw), "SHORT_OUTPUT_WRITE")
            f.flush()
            os.fsync(f.fileno())
        self.hashes[name] = {"bytes": len(raw), "sha256": sha(raw)}


class Ledger:
    def __init__(self, binding, plan):
        self.binding = binding
        self.plan = plan
        self.source_list = {d["id"]: d for d in plan["future_input_descriptors_reference_only"]}
        self.issued_sources = set()
        self.source_records = []
        self.charged = collections.Counter()
        self.returned = collections.Counter()
        self.unknown = None
        self.sequence = 0
        self.journal = (RUN_DIR / "dispatch_ledger.jsonl").open("xb")
        limits = plan["proposed_caps_pending_release"]
        m, p, b = limits["main"], limits["pilot_included_in_main"], limits["input"]
        self.caps = {
            "source_files": b["descriptor_count"], "I_logical_bytes": b["identity_plus_purpose_max_bytes"],
            "checkpoint_decode": b["checkpoint_decodes_max"], "head_construct": b["checkpoint_decodes_max"],
            "checkpoint_storages": b["checkpoint_storages"], "checkpoint_storage_bytes": b["checkpoint_storage_bytes"],
            "head_parameters": b["head_parameters"], "affine_slots": b["affine_slots"], "affine_coefficients": b["affine_coefficient_values"],
            "forward": m["native_head_forward_including_source"], "input_AD": m["input_AD_single_endpoint"],
            "dot": m["direction_dot_1024"], "cache_rows": m["cache_rows_unique_upper_bound"],
            "cache_coords": m["cache_coordinates_upper_bound"], "carry_scalars": m["historical_A_r_scalar_reads"],
            "span_views": m["function_operation_span_views_upper_bound"],
            "weighted_views": m["weighted_reference_views_upper_bound"],
            "summary_rows": m["summary_rows_max"], "paired_rows": m["paired_h1_rows_max"],
            "paired_fields": m["paired_DeltaE_DeltaB_DeltaSkill_fields_max"],
            "chain_groups": p["auxiliary_chain_groups"], "chain_matvec": p["auxiliary_matvec"],
            "chain_mask": p["auxiliary_mask_products"], "chain_coords": p["auxiliary_coordinate_compares"],
        }
        self.panel_committed = False
        self.max_panel = m["source_rows"]

    def set_actual_size(self, n):
        require(isinstance(n, int) and 0 < n <= self.max_panel, "INVALID_LOCKED_PANEL_SIZE")
        for key in ("forward", "input_AD", "dot", "cache_rows", "cache_coords", "carry_scalars",
                    "span_views", "weighted_views"):
            self.caps[key] = self.caps[key] * n // self.max_panel

    def event(self, name, **data):
        raw = encoded({"event": name, "at_utc": utc(), **data}) + b"\n"
        require(self.journal.tell() + len(raw) <= 8 * 2**20, "DISPATCH_CONTROL_LOG_CAP")
        require(self.journal.write(raw) == len(raw), "SHORT_JOURNAL_WRITE")
        self.journal.flush()
        os.fsync(self.journal.fileno())

    @contextlib.contextmanager
    def block(self, name, **reservation):
        clock_guard(self.binding)
        require(self.unknown is None, "PENDING_DISPATCH")
        for key, n in reservation.items():
            require(0 <= n <= self.caps[key] - self.charged[key], "BUDGET_" + key)
        self.sequence += 1
        self.unknown = {"sequence": self.sequence, "block": name, "reserved": reservation}
        self.event("RESERVED_BEFORE_DISPATCH", **self.unknown)
        self.charged.update(reservation)
        used = collections.Counter()
        try:
            yield used
            for key, n in used.items():
                require(0 <= n <= reservation[key], "RETURN_EXCEEDS_RESERVATION_" + key)
            self.event("RETURN_COMMITTED", sequence=self.sequence, block=name, returned=dict(used))
            self.returned.update(used)
            self.unknown = None
        except BaseException:
            self.unknown["observed_returns_in_uncommitted_block"] = dict(used)
            raise

    def input(self, source_id):
        row = self.source_list[source_id]
        require(source_id not in self.issued_sources, "SOURCE_REOPEN")
        metadata = set(self.plan["panel"]["metadata_files"])
        require(self.panel_committed or source_id in metadata, "PAYLOAD_BEFORE_PANEL_SEAL")
        desc = Path(row["path"])
        self.issued_sources.add(source_id)
        with self.block("INPUT_" + source_id, source_files=1, I_logical_bytes=2 * row["bytes"]) as used:
            with desc.open("rb") as f:
                st = os.fstat(f.fileno())
                require(stat.S_ISREG(st.st_mode) and st.st_size == row["bytes"], "INPUT_FILE_SIZE_OR_TYPE")
                raw = f.read(row["bytes"])
                after = os.fstat(f.fileno())
            require((st.st_size, st.st_mtime_ns, st.st_ctime_ns) ==
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "INPUT_CHANGED_DURING_READ")
            require(len(raw) == row["bytes"] and sha(raw) == row["sha256"], "INPUT_SEAL_MISMATCH_" + source_id)
            used.update(source_files=1, I_logical_bytes=2 * row["bytes"])
            self.source_records.append({"id": source_id, "bytes": len(raw), "sha256": row["sha256"],
                                        "purpose": row["kind"], "whole_file_read_calls": 1})
        return raw


def lock_panel(np, plan, cases, ledger, files):
    key_fields = {"source_id", "label_group_id", "pair_group_id", "endpoint", "case_ids",
                  "v3_3_role", "n_reference_rows", "n_donor_proteins", "smiles_sha256", "target_sequence_key"}
    panel = [project(line, key_fields) for line in ledger.input("panel").decode().splitlines()]
    raw = ledger.input("indices")
    idx = npy_view(np, raw, (322659, 14), "<i8")
    old = [project(line, {"label_group_id", "pair_group_id", "endpoint"})
           for line in ledger.input("original_path_source_panel_exclusion").decode().splitlines()]
    require(len(old) == 32 and collections.Counter(x["endpoint"] for x in old) == {"Kd": 16, "Ki": 16},
            "ORIGINAL_PANEL_IDENTITY")
    require(len({(x["label_group_id"], x["pair_group_id"], x["endpoint"]) for x in old}) == 32,
            "ORIGINAL_PANEL_UNIQUENESS")
    excluded = {x["pair_group_id"] for x in old}
    require(collections.Counter(p["endpoint"] for p in panel) == {"Kd": 206, "Ki": 2036},
            "OPERATION_PANEL_COUNTS")
    all_keys = {(p["label_group_id"], p["pair_group_id"], p["endpoint"]) for p in panel}
    require(len(all_keys) == len(panel) and len({p["source_id"] for p in panel}) == len(panel)
            and len({(p["pair_group_id"], p["endpoint"]) for p in panel}) == len(panel),
            "SOURCE_PAIR_ENDPOINT_UNIQUENESS")
    require(all((x["label_group_id"], x["pair_group_id"], x["endpoint"]) in all_keys for x in old),
            "ORIGINAL_PANEL_INPUT_JOIN")
    case_ids = {c["case_id"] for c in cases}
    require(all(set(p["case_ids"]) == case_ids for p in panel), "ALL_CASE_SUPPORT")
    require(bool(np.all((idx[:, 0] >= 0) & (idx[:, 0] < 5697) &
                       (idx[:, 1] >= 0) & (idx[:, 1] < 46140) &
                       (idx[:, 2] >= 0) & (idx[:, 2] < 2) & (idx[:, 13] == 1))),
            "LINK_ID_OR_SUPPORT")
    require(bool(np.all((idx[:, 3:7] >= 0) & (idx[:, 3:7] < 19748)) and
                 np.all(idx[:, 3:4] != idx[:, 4:7]) and np.all(idx[:, 11:13] > 0)),
            "LINK_CORNER_KEYS_OR_DENOMINATORS")
    cache_roles = ((idx[:, 7:11] >= 0) & (idx[:, 7:11] < 46140)) | (
        (idx[:, 7:11] >= 51265) & (idx[:, 7:11] < 56962))
    require(bool(np.all(cache_roles) and np.all(idx[:, 7] >= 51265) and np.all(idx[:, 10] < 46140)),
            "LINK_CACHE_ROLE")
    groups = collections.defaultdict(list)
    for i, sid in enumerate(idx[:, 0]):
        groups[int(sid)].append(i)
    require(set(groups) == {p["source_id"] for p in panel}, "SOURCE_INDEX_JOIN")
    eligible = {ep: [] for ep in EPS}
    before = collections.Counter()
    for p in panel:
        ep = p["endpoint"]
        rr = groups[p["source_id"]]
        rows = idx[rr]
        require(len(rr) == p["n_reference_rows"] and len(set(map(int, rows[:, 1]))) == len(rr)
                and bool(np.all(rows[:, 2] == EPS.index(ep)) and np.all(rows[:, 11] == len(rr))
                         and np.all(rows[:, 7] == rows[0, 7])), "SOURCE_DONOR_ENDPOINT_OR_WEIGHT_JOIN")
        if len(rr) >= 2:
            before[ep] += 1
            if p["pair_group_id"] not in excluded:
                key = [HASH_DOMAIN, "source", ep, str(p["label_group_id"]), str(p["pair_group_id"])]
                eligible[ep].append((order_digest(key), encoded(key), p, rr))
    for ep in EPS:
        prior = plan["panel"]["development_by_endpoint"][ep]
        require(before[ep] == prior["N_eligible_with_two_distinct_donor_rows"]
                and len(eligible[ep]) >= prior["lower_bound_after_exclusion"], "CAPACITY_RECORD_CONFLICT")
    n = min(64, *(len(x) for x in eligible.values()))
    require(n >= 32, "PANEL_NOT_READY")
    selected = []
    composition = {}
    for ep in EPS:
        donors = collections.Counter()
        different = 0
        start = len(selected)
        for digest, _, p, rr in sorted(eligible[ep], key=lambda x: (x[0], x[1]))[:n]:
            key = [HASH_DOMAIN, "donor", ep, str(p["label_group_id"]), str(p["pair_group_id"])]
            ranked = sorted(rr, key=lambda r: (order_digest(key + [str(int(idx[r, 1]))]), int(idx[r, 1])))[:2]
            pair = idx[ranked]
            inverse = [1.0 / int(d) for d in pair[:, 12]]
            sensitive = [x / (inverse[0] + inverse[1]) for x in inverse]
            require(math.isclose(sum(sensitive), 1.0, rel_tol=0, abs_tol=2e-15), "RESTRICTED_WEIGHT")
            different += int(pair[0, 12] != pair[1, 12])
            donors.update(map(int, pair[:, 1]))
            selected.append({**p, "rank_digest": digest.hex(), "source_index": len(selected),
                             "links": [{"operation_row": int(r), "donor_FIT_index0": int(idx[r, 1]),
                                        "cache_rows_ABCD": [int(x) for x in idx[r, 7:11]],
                                        "old_denominators": [int(x) for x in idx[r, 11:13]],
                                        "reference_weights": [0.5, sensitive[j]]}
                                       for j, r in enumerate(ranked)]})
        counts = {}
        for field in ("smiles_sha256", "target_sequence_key"):
            c = collections.Counter(x[field] for x in selected[start:])
            counts[field] = {"unique": len(c), "maximum_source_share": max(c.values()) / n}
        composition[ep] = {"O_SRC_sources": sum(p["endpoint"] == ep for p in panel),
                           "eligible_before": before[ep], "eligible_after": len(eligible[ep]),
                           "eligible_removed": before[ep] - len(eligible[ep]), "selected_sources": n,
                           "selected_links": 2 * n, "unique_FIT_donor_rows": len(donors),
                           "max_donor_row_reuse": max(donors.values()), "reference_design_different": different,
                           "reference_design_equal": n - different, "input_identity_counts": counts}
    require(not ({x["pair_group_id"] for x in selected} & excluded), "ORIGINAL_PAIR_NOT_EXCLUDED")
    ledger.set_actual_size(len(selected))
    files.emit("panel_composition.json", {"endpoint": composition, "old_pair_group_count": len(excluded),
                                         "selected_sources": len(selected), "selected_links": 2 * len(selected),
                                         "old_source_pair_overlap": 0, "exposure": "EXPOSED_DEVELOPMENT_ONLY"})
    files.emit("panel_and_references.json", {"hash_domain": HASH_DOMAIN, "endpoints": EPS, "operations": OPS,
                                           "spans": SPANS, "functions": FUN, "references": POLICIES,
                                           "case_order": [c["case_id"] for c in cases], "sources": selected})
    files.emit("panel_seal.json", {"artifacts": dict(files.hashes), "sealed_before_numerical_inputs": True,
                                   "planned_caps_after_lock": ledger.caps, "locked_at_utc": utc()})
    ledger.panel_committed = True
    ledger.event("PANEL_LOCKED", sources=len(selected), links=2 * len(selected),
                 eligible_after={ep: composition[ep]["eligible_after"] for ep in EPS})
    return selected, composition


def affine_slots(ledger, cases):
    text = ledger.input("calibrators").decode()
    with ledger.block("AFFINE_PROJECT", affine_slots=20, affine_coefficients=40) as used:
        slot_start = dict(json_members(text))["slots"][0]
        slots = {}
        for _, (start, _) in json_members(text, slot_start, object_mode=False):
            fields = dict(json_members(text, start))
            a, b = fields["family"]
            if json.loads(text[a:b]) != "affine":
                continue
            row = {k: json.loads(text[a:b]) for k, (a, b) in fields.items()
                   if k in {"case_id", "endpoint", "coefficients", "mode", "slot_sha256"}}
            require(used["affine_slots"] < 20 and len(row["coefficients"]) == 2, "AFFINE_QUOTA_OR_FIELDS")
            used.update(affine_slots=1, affine_coefficients=2)
            slope, intercept = map(float, row["coefficients"])
            key = (row["case_id"], row["endpoint"])
            require(key not in slots and math.isfinite(slope) and slope > 0 and math.isfinite(intercept),
                    "AFFINE_COEFFICIENT_OR_IDENTITY")
            slots[key] = (slope, intercept)
        require(set(slots) == {(c["case_id"], ep) for c in cases for ep in EPS}, "AFFINE20")
    return slots


def load_head(torch, ledger, source_id, case, schema, high):
    raw = ledger.input(source_id)
    cap = schema["per_h1"] if high else schema["per_original"][case["arm"]]
    shapes = schema["head_state_shapes"] if high else schema["state_shapes"][case["arm"]]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        prefix = {e.filename.split("/")[0] for e in entries}
        require(len(prefix) == 1 and len({e.filename for e in entries}) == len(entries)
                and len(entries) <= cap["zip_entries_max"] and all(
                    not e.flag_bits & 1 and e.compress_type == 0
                    and not ({"", ".", ".."} & set(e.filename.split("/"))) for e in entries),
                "CHECKPOINT_ZIP_LAYOUT")
        storage = [e.file_size for e in entries if STORAGE.fullmatch(e.filename)]
        other = [e for e in entries if not STORAGE.fullmatch(e.filename)]
        require(sorted(storage) == sorted(math.prod(dims) * 4 for dims in shapes.values())
                and len(storage) == cap["storages"] and sum(storage) == cap["storage_bytes"],
                "CHECKPOINT_STORAGE_LAYOUT")
        require(len(other) <= cap["nonstorage_entries_max"]
                and sum(e.file_size for e in other) <= cap["nonstorage_payload_bytes_max"]
                and all(e.filename.split("/", 1)[1] in schema["zip_metadata_names"] for e in other),
                "CHECKPOINT_METADATA_LAYOUT")
    reservation = dict(checkpoint_decode=1, head_construct=1, head_parameters=295554,
                       checkpoint_storages=len(storage), checkpoint_storage_bytes=sum(storage))
    with ledger.block("DECODE_" + source_id, **reservation) as used:
        obj = torch.load(io.BytesIO(raw), weights_only=True, map_location="cpu", mmap=False)
        used.update(checkpoint_decode=1, checkpoint_storages=len(storage), checkpoint_storage_bytes=sum(storage))
        roots = schema["roots"][case["arm"] + "_h1" if high else "original"]
        require(set(obj) == set(roots) and obj["arm"] == case["arm"] and obj["seed"] == case["seed"]
                and isinstance(obj["epoch"], int) and 1 <= obj["epoch"] <= 150, "HEAD_IDENTITY")
        if high:
            require(obj["LR"] == 0.0006 and obj[roots[-1]] is True
                    and obj["cache_sha256"] == ledger.source_list[case["cache"]]["sha256"], "HIGH_LR_IDENTITY")
        else:
            require(obj["epoch"] == case["h0_original_epoch"], "ORIGINAL_EPOCH")
        state = obj["state_dict"]
        require(set(state) == set(shapes), "HEAD_TENSOR_KEYS")
        storage_pointers = set()
        for key, shape in shapes.items():
            t = state[key]
            require(isinstance(t, torch.Tensor) and list(t.shape) == shape and t.dtype == torch.float32
                    and t.storage_offset() == 0 and t.is_contiguous()
                    and t.untyped_storage().nbytes() == t.numel() * 4, "HEAD_TENSOR_LAYOUT")
            ptr = t.untyped_storage().data_ptr()
            require(ptr not in storage_pointers, "HEAD_STORAGE_ALIAS")
            storage_pointers.add(ptr)
        fused = {}
        for key in schema["head_state_shapes"]:
            t = state[key if high else "fusion." + key]
            require(bool(torch.isfinite(t).all()), "HEAD_PARAMETER_NONFINITE")
            fused[key] = t.detach().to(dtype=torch.float64)
            used["head_parameters"] += t.numel()
        used["head_construct"] += 1
    return fused


def load_cache(np, ledger, source_id, panel):
    rows = sorted({i for p in panel for link in p["links"] for i in link["cache_rows_ABCD"]})
    require(len(rows) <= len(panel) * 7, "UNIQUE_CACHE_ROW_CAP")
    raw = ledger.input(source_id)
    cache = npy_view(np, raw, (56962, 1024), "<f4")
    with ledger.block("CACHE_PROJECT_" + source_id, cache_rows=len(rows), cache_coords=len(rows) * 1024) as used:
        selected = {}
        for i in rows:
            z = cache[i].astype(np.float64)
            used.update(cache_rows=1, cache_coords=1024)
            require(bool(np.isfinite(z).all()), "CACHE_NONFINITE")
            selected[i] = z
        for p in panel:
            for link in p["links"]:
                a, b, c, d = (selected[i] for i in link["cache_rows_ABCD"])
                require(all(np.array_equal(x[lo:hi], y[lo:hi]) for x, y, lo, hi in (
                    (a, b, 0, 256), (a, c, 256, 1024), (c, d, 0, 256), (b, d, 256, 1024))),
                    "FOUR_CORNER_COMPOSABILITY")
    return selected


class Case:
    def __init__(self, np, torch, case, ledger, panel, affine, schema):
        self.np, self.torch, self.case, self.ledger = np, torch, case, ledger
        self.id = case["case_id"]
        self.heads = [load_head(torch, ledger, case[k], case, schema, k == "h1_head")
                      for k in ("h0_checkpoint", "h1_head")]
        self.affine = {ep: affine[self.id, ep] for ep in EPS}
        cache = load_cache(np, ledger, case["cache"], panel)
        self.point_key = {}
        self.points = []
        self.source_points = set()
        self.geometry = []
        self.values = [{}, {}]
        self.gradients = [{}, {}]
        self.graphs = [{}, {}]
        self.routes = [torch.tensor([[1., 0.]], dtype=torch.float64), torch.tensor([[0., 1.]], dtype=torch.float64)]
        self.result = np.empty((len(panel), 2, 3, len(SPANS), 4, 2))
        self.zeros = [0] * len(OPS)
        self.zero_by_endpoint = {ep: [0] * len(OPS) for ep in EPS}
        self.source_point_repeats = collections.Counter()
        for p in panel:
            ep = EPS.index(p["endpoint"])
            source = cache[p["links"][0]["cache_rows_ABCD"][0]]
            sid = self.point(ep, source)
            self.source_point_repeats[p["endpoint"]] += int(sid in self.source_points)
            self.source_points.add(sid)
            link_geometry = []
            for link in p["links"]:
                a, b, c, d = (cache[i] for i in link["cache_rows_ABCD"])
                require(np.array_equal(a, source), "SOURCE_POINT_NOT_SHARED")
                operations = []
                for oi, target in enumerate((c, b, d)):
                    delta = target - a
                    zero = int(not np.any(delta))
                    self.zeros[oi] += zero
                    self.zero_by_endpoint[p["endpoint"]][oi] += zero
                    points = [self.point(ep, a + span * delta if span != 1 else target) for span in SPANS]
                    operations.append((delta, points))
                link_geometry.append(operations)
            self.geometry.append((sid, link_geometry))
        require(len(self.points) <= 37 * len(panel), "POINT_REUSE_CAP")
        self.old_raw = ledger.input(case["old_response"])
        self.old = npy_view(np, self.old_raw, (322659, 4, 12), "<f8")

    def point(self, endpoint, vector):
        canonical = vector.copy()
        canonical[canonical == 0] = 0.0
        key = (endpoint, canonical.tobytes())
        pid = self.point_key.get(key)
        if pid is None:
            require(bool(self.np.isfinite(vector).all()), "PATH_COORDINATE_NONFINITE")
            pid = len(self.points)
            self.point_key[key] = pid
            self.points.append((endpoint, vector))
        return pid

    def value(self, hi, pid, used):
        if pid in self.values[hi]:
            return self.values[hi][pid]
        torch = self.torch
        t = self.heads[hi]
        ep, vector = self.points[pid]
        grad = pid in self.source_points
        with torch.set_grad_enabled(grad):
            x = torch.from_numpy(vector).reshape(1, 1024).requires_grad_(grad)
            linear = torch.nn.functional.linear
            p1 = linear(x, t["first.weight"], t["first.bias"])
            p2 = linear(torch.relu(p1), t["second.weight"], t["second.bias"])
            hidden = torch.relu(p2)
            both = torch.cat([linear(hidden, t[e + ".weight"], t[e + ".bias"]) for e in EPS], dim=-1)
            y = (both * self.routes[ep]).sum(dim=-1)
            used["forward"] += 1
        val = y.detach().item()
        require(math.isfinite(val), "NATIVE_OUTPUT_NONFINITE")
        if grad:
            self.graphs[hi][pid] = (x, y, p1.detach(), p2.detach())
        self.values[hi][pid] = val
        return val

    def derivative(self, hi, pid, used):
        if pid not in self.gradients[hi]:
            x, y, p1, p2 = self.graphs[hi].pop(pid)
            g = self.torch.autograd.grad(y, x, create_graph=False)[0].numpy().ravel()
            used["input_AD"] += 1
            require(bool(self.np.isfinite(g).all()), "INPUT_AD_NONFINITE")
            self.gradients[hi][pid] = (g, p1, p2)
        return self.gradients[hi][pid]

    def chain(self, hi, pid, gradient, used):
        np, torch = self.np, self.torch
        g, p1, p2 = gradient
        ep = EPS[self.points[pid][0]]
        t = self.heads[hi]
        require(bool(torch.isfinite(p1).all() and torch.isfinite(p2).all()), "PILOT_PREACTIVATION_NONFINITE")
        second = t["second.weight"].T @ ((p2.reshape(-1) > 0) * t[ep + ".weight"].reshape(-1))
        first = t["first.weight"].T @ ((p1.reshape(-1) > 0) * second)
        chain = first.numpy()
        error = np.abs(chain - g)
        allowance = 1e-6 + 1e-5 * np.maximum(np.abs(chain), np.abs(g))
        used.update(chain_groups=1, chain_matvec=2, chain_mask=384, chain_coords=1024)
        require(bool(np.isfinite(chain).all() and np.all(error <= allowance)), "PILOT_CHAIN_AD_MISMATCH")
        return {"case_id": self.id, "endpoint": ep, "head": HEADS[hi], "max_abs": float(error.max()),
                "max_tolerance_fraction": float((error / allowance).max()),
                "zero_preactivations": int((p1 == 0).sum() + (p2 == 0).sum())}

    def run_source(self, p, pilot, sink):
        np = self.np
        si, ep = p["source_index"], p["endpoint"]
        sid, geometry = self.geometry[si]
        reserve = dict(forward=74, input_AD=2, dot=12, carry_scalars=48, span_views=144)
        if pilot:
            reserve.update(chain_groups=2, chain_matvec=4, chain_mask=768, chain_coords=2048)
        with self.ledger.block(f"SOURCE_{self.id}_{si}", **reserve) as used:
            y0 = [self.value(hi, sid, used) for hi in range(2)]
            gradients = [self.derivative(hi, sid, used) for hi in range(2)]
            chain_rows = [self.chain(hi, sid, grad, used) for hi, grad in enumerate(gradients)] if pilot else []
            slope, intercept = self.affine[ep]
            f0 = self.function_values(*y0, slope, intercept)
            carry = []
            for li, operations in enumerate(geometry):
                row = self.result[si, li]
                for oi, (delta, pids) in enumerate(operations):
                    m0, m1 = (float(np.dot(grad[0], delta)) for grad in gradients)
                    used["dot"] += 2
                    ma = slope * m0
                    m = np.array([m1 - ma, m0, ma, m1])
                    for j, (span, pid) in enumerate(zip(SPANS, pids)):
                        v0, v1 = (self.value(hi, pid, used) for hi in range(2))
                        row[oi, j, :, 0] = span * m
                        row[oi, j, :, 1] = self.function_values(v0, v1, slope, intercept) - f0
                        used["span_views"] += 4
                require(bool(np.isfinite(row).all()), "DERIVED_RESULT_NONFINITE")
                old = self.old[p["links"][li]["operation_row"], :, :6].reshape(4, 2, 3)
                used["carry_scalars"] += 24
                new = row[:, -1].transpose(1, 2, 0)
                delta = new - old
                absolute = np.abs(delta)
                allow = np.array([1e-6, 1e-5])[None, :, None] + 1e-5 * np.maximum(np.abs(new), np.abs(old))
                require(bool(np.isfinite(old).all() and np.all(absolute <= allow)), "OLD_FULL_ENDPOINT_CARRY_MISMATCH")
                carry.append({"old_denominators": p["links"][li]["old_denominators"],
                              "delta_by_function_quantity_operation": delta.tolist()})
            sink.append({"case_id": self.id, "source_index": si, "endpoint": ep, "pilot": pilot,
                         "A_r": self.result[si].tolist(), "full_span_carry": carry})
        return chain_rows

    def function_values(self, h0, h1, a, b):
        ha = a * h0 + b
        return self.np.array([h1 - ha, h0, ha, h1])

    def reuse(self, panel):
        possible = 37 * len(panel)
        require(len(self.values[0]) == len(self.values[1]) == len(self.points) and not any(self.graphs),
                "NATIVE_POINT_REUSE_CLOSURE")
        require(all(set(gradients) == self.source_points for gradients in self.gradients), "SOURCE_AD_REUSE_CLOSURE")
        return {"case_id": self.id, "logical_forward_after_required_source_sharing": possible * 2,
                "unique_physical_points": len(self.points), "physical_forward": len(self.points) * 2,
                "additional_reused_forward": (possible - len(self.points)) * 2,
                "unique_source_points": len(self.source_points), "physical_AD": len(self.source_points) * 2,
                "additional_reused_AD": (len(panel) - len(self.source_points)) * 2,
                "coincident_source_points_by_endpoint": self.source_point_repeats,
                "zero_displacements_by_endpoint_and_operation": self.zero_by_endpoint}


class Results:
    def __init__(self):
        self.path = RUN_DIR / "source_results.jsonl"
        self.handle = self.path.open("xb")
        self.digest = hashlib.sha256()
        self.count = 0
        self.carry_max_abs = {"A": 0., "r": 0.}
        self.pilot_carry_max_abs = {"A": 0., "r": 0.}

    def append(self, row):
        raw = encoded(row) + b"\n"
        require(self.handle.tell() + len(raw) <= 64 * 2**20, "SOURCE_RESULTS_CAP")
        require(self.handle.write(raw) == len(raw), "SOURCE_RESULTS_SHORT_WRITE")
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.count += 1
        self.digest.update(raw)
        for name, j in (("A", 0), ("r", 1)):
            maximum = max(abs(v) for c in row["full_span_carry"]
                          for f in c["delta_by_function_quantity_operation"] for v in f[j])
            self.carry_max_abs[name] = max(self.carry_max_abs[name], maximum)
            if row["pilot"]:
                self.pilot_carry_max_abs[name] = max(self.pilot_carry_max_abs[name], maximum)

    def close(self):
        size = self.handle.tell()
        self.handle.close()
        return {"bytes": size, "sha256": self.digest.hexdigest()}


def summaries(np, cases, panel, ledger, files):
    e_count = len(panel) // 2
    weighted = len(panel) * len(cases) * 288
    reservation = dict(weighted_views=weighted, summary_rows=5184, paired_rows=1296, paired_fields=7776)
    with ledger.block("WEIGHT_AND_SUMMARIZE", **reservation) as used:
        raw = {}
        for ci, case in enumerate(cases):
            for ei, endpoint in enumerate(EPS):
                chosen = panel[ei * e_count:(ei + 1) * e_count]
                require(all(p["endpoint"] == endpoint for p in chosen), "AGGREGATE_ENDPOINT_ORDER")
                data = case.result[ei * e_count:(ei + 1) * e_count].reshape(-1, 3, 6, 4, 2)
                a, r = data[..., 0], data[..., 1]
                metrics = np.stack((np.abs(r - a), np.abs(r)), axis=-1)
                for pi in range(2):
                    weights = np.array([ln["reference_weights"][pi] / e_count for p in chosen for ln in p["links"]])
                    require(math.isclose(float(weights.sum()), 1., rel_tol=0, abs_tol=2e-15), "REFERENCE_MASS")
                    raw[ci, ei, pi] = np.tensordot(weights, metrics, axes=(0, 0))
                    used["weighted_views"] += len(weights) * 3 * 6 * 4
        lines = []
        primary = []
        for arm in ("S", "G"):
            for seed in (*SEEDS, "MEAN5"):
                indices = [i for i, c in enumerate(cases)
                           if c.case["arm"] == arm and (seed == "MEAN5" or c.case["seed"] == seed)]
                require(len(indices) == (5 if seed == "MEAN5" else 1), "SUMMARY_SEED_CLOSURE")
                for endpoint in (*EPS, "KD_KI_EQUAL"):
                    endpoints = tuple(range(2)) if endpoint == "KD_KI_EQUAL" else (EPS.index(endpoint),)
                    for pi, policy in enumerate(POLICIES):
                        numbers = np.array([raw[i, e, pi] for i in indices for e in endpoints]).mean(axis=0)
                        require(bool(np.isfinite(numbers).all()), "SUMMARY_NONFINITE")
                        for oi, operation in enumerate(OPS):
                            for j, span in enumerate(SPANS):
                                vec = {}
                                for fi, fun in enumerate(FUN):
                                    E, B = map(float, numbers[oi, j, fi])
                                    vec[fun] = (E, B, B - E)
                                for fun in FUN:
                                    E, B, skill = vec[fun]
                                    row = {
                                        "arm": arm, "seed_layer": seed, "endpoint_layer": endpoint,
                                        "operation": operation, "lambda": span, "function_id": fun,
                                        "reference_scheme": policy, "E": E, "B": B, "Skill": skill,
                                        "sign": "POSITIVE" if skill > 0 else "NEGATIVE" if skill < 0 else "ZERO",
                                        "Kd_or_Ki_sources_per_case": e_count, "links_per_endpoint_per_case": 2 * e_count,
                                        "seed_count": len(indices), "endpoint_count": len(endpoints),
                                    }
                                    if fun == "h1":
                                        pairs = {}
                                        for base in ("h0", "h_aff"):
                                            dE, dB, dS = (x - y for x, y in zip(vec["h1"], vec[base]))
                                            require(math.isclose(dS, dB - dE, rel_tol=0, abs_tol=1e-12),
                                                    "PAIRED_SKILL_IDENTITY")
                                            pairs[base] = {"DeltaE": dE, "DeltaB": dB, "DeltaSkill": dS}
                                        row["h1_paired_differences"] = pairs
                                        used.update(paired_rows=1, paired_fields=6)
                                    used["summary_rows"] += 1
                                    lines.append(encoded(row) + b"\n")
                                    if arm == "G" and seed == "MEAN5" and fun == "q" and pi == 0:
                                        if endpoint == "KD_KI_EQUAL" or (operation == "D" and span == 0.5):
                                            primary.append({"summary_line": len(lines), **row})
        require(dict(used) == reservation and len(primary) == 20, "AGGREGATION_CLOSURE")
        files.write("span_summary.jsonl", b"".join(lines))
        files.emit("primary_results.json", {"units": "pAffinity", "rows": primary,
                                            "finite_development_description": True,
                                            "not_adjudicated": ["continuous_radius", "population_inference", "independent_confirmation"]})


def validate_bindings(binding, plan, prior):
    require(binding["launcher"]["path"] == str(Path(__file__).resolve())
            and binding["launcher"] == file_descriptor(Path(__file__).resolve()), "EXECUTOR_SEAL")
    require(binding["plan"] == file_descriptor(PLAN) and binding["contract"] == file_descriptor(CONTRACT),
            "CONTRACT_OR_PLAN_SEAL")
    require(binding["prior"] == file_descriptor(HANDOFF / PRIOR_REL), "HISTORICAL_CONTRACT_SEAL")
    expected = plan["metadata_control_evidence_checked"][str((HANDOFF / PRIOR_REL).relative_to(ROOT))]
    require(expected["matches_snapshot_manifest"] is True and
            all(binding["prior"][k] == expected[k] for k in ("sha256", "bytes")), "HISTORICAL_CONTRACT_IDENTITY")
    refs = {q["id"]: q for q in plan["future_input_descriptors_reference_only"]}
    old = {q["id"]: q for q in prior["sources"]}
    require(len(refs) == 44 and sum(q["bytes"] for q in refs.values()) == 3647205152, "INPUT_DESCRIPTOR_COUNT")
    for code, rec in refs.items():
        if code != "original_path_source_panel_exclusion":
            require(all(rec[k] == old[code][k] for k in ("path", "sha256", "bytes")), "INPUT_DESCRIPTOR_CONFLICT")
    cases = prior["cases"]
    require(len(cases) == 10 and {(c["seed"], c["arm"]) for c in cases} == {(s, a) for s in SEEDS for a in "GS"},
            "CASE_DESCRIPTORS")
    require(tuple(plan["fixed_science"]["references"]) == POLICIES
            and tuple(plan["fixed_science"]["operations"]) == OPS
            and plan["panel"]["hash_domain"] == HASH_DOMAIN, "SCIENCE_COMMITMENT")
    return cases


def write_control(name, data):
    with (RUN_DIR / name).open("xb") as f:
        raw = encoded(data) + b"\n"
        require(f.write(raw) == len(raw), "SHORT_CONTROL_WRITE")
        f.flush()
        os.fsync(f.fileno())


def worker():
    binding = load_control(RUN_DIR / "binding.json")
    ledger = files = sink = None
    phase = "QUALIFYING"
    try:
        require(os.getppid() == binding["owner_pid"] and os.path.abspath(sys.executable) == str(PYTHON),
                "WORKER_LAUNCH_IDENTITY")
        plan, prior = load_control(PLAN), load_control(HANDOFF / PRIOR_REL)
        cases = validate_bindings(binding, plan, prior)
        clock_guard(binding)
        import numpy as np
        import psutil
        import torch

        require(all(str(module.__version__) == binding["dependencies"][name]["version"] and
                    str(Path(module.__file__).resolve()) == binding["dependencies"][name]["origin"]
                    for name, module in (("numpy", np), ("torch", torch), ("psutil", psutil))), "RUNTIME_IDENTITY")
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        torch.use_deterministic_algorithms(True)
        require(torch.get_num_threads() == torch.get_num_interop_threads() == 1
                and torch.are_deterministic_algorithms_enabled(), "THREAD_OR_DETERMINISM")
        check_resources(resources(psutil, [os.getpid(), os.getppid()]))
        files, ledger = Artifacts(binding), Ledger(binding, plan)
        phase = "PANEL"
        panel, composition = lock_panel(np, plan, cases, ledger, files)
        print(f"PANEL_LOCKED sources={len(panel)} links={len(panel) * 2}", flush=True)

        phase = "QUALIFY_PAYLOADS"
        affine = affine_slots(ledger, cases)
        case_runs = []
        for case in cases:
            check_resources(resources(psutil, [os.getpid(), os.getppid()]))
            case_runs.append(Case(np, torch, case, ledger, panel, affine, prior["checkpoint_schema"]))
        files.emit("inputs_receipt.json", {"source_reads": ledger.source_records,
                                          "sources": len(ledger.source_records),
                                          "whole_file_bytes": sum(r["bytes"] for r in ledger.source_records),
                                          "nonfusion_storage_bytes_incidental_only": prior["checkpoint_schema"]["total"]["original_nonfusion_bytes"],
                                          "original_buffers_reused_for_projection": True,
                                          "holds_authorized_affine_slots": len(affine)})
        require(set(ledger.source_list) == ledger.issued_sources and len(ledger.source_records) == 44, "SOURCE_READ_CLOSURE")
        phase = "PILOT"
        sink = Results()
        first = {ep: next(p["source_index"] for p in panel if p["endpoint"] == ep) for ep in EPS}
        pilot_started = time.monotonic()
        left = dict(ledger.returned)
        chains = [chain for c in case_runs for si in first.values() for chain in c.run_source(panel[si], True, sink)]
        elapsed = time.monotonic() - pilot_started
        calls = {k: n - left.get(k, 0) for k, n in ledger.returned.items() if n != left.get(k, 0)}
        limit = plan["proposed_caps_pending_release"]["pilot_included_in_main"]
        require(len(chains) == 40 and sink.count == 20 and
                all(calls[k] <= limit[x] for k, x in (("forward", "forward"), ("input_AD", "input_AD"),
                                                     ("dot", "direction_dot"), ("carry_scalars", "old_A_r_scalars"))),
                "PILOT_CLOSURE")
        projected = elapsed * ((len(panel) * len(cases) - sink.count) / sink.count)
        check_resources(resources(psutil, [os.getpid(), os.getppid()]))
        require(projected < min(binding["wall_deadline"] - time.time(), binding["monotonic_deadline"] - time.monotonic()),
                "PILOT_TIME_PROJECTION_EXCEEDS_DEADLINE")
        files.emit("pilot_receipt.json", {"status": "PASSED_ENGINEERING_RELEASE", "pilot_sources": first,
                                         "case_source_blocks": sink.count, "completed_calls": calls,
                                         "chain_checks": chains, "full_span_carry_max_abs": sink.pilot_carry_max_abs,
                                         "pilot_elapsed_seconds": elapsed, "remaining_compute_projected_seconds": projected,
                                         "pilot_blocks_reused": True, "sign_or_gain_release_gate": False})
        print(f"PILOT_PASSED blocks=20 project_compute_seconds={projected:.1f}", flush=True)

        phase = "MAIN"
        for i, c in enumerate(case_runs, 1):
            for p in panel:
                if p["source_index"] not in first.values():
                    c.run_source(p, False, sink)
            del c.old, c.old_raw
            print(f"CASE_COMPUTED {i}/10 source_blocks={sink.count}", flush=True)
        require(sink.count == len(cases) * len(panel), "SOURCE_RESULTS_CLOSURE")
        files.hashes[sink.path.name] = sink.close()
        reuse = [c.reuse(panel) for c in case_runs]
        require(sum(r["physical_forward"] for r in reuse) == ledger.returned["forward"]
                and sum(r["physical_AD"] for r in reuse) == ledger.returned["input_AD"], "PHYSICAL_CALL_CLOSURE")
        files.emit("dedup_and_carry_receipt.json", {"reuse": reuse, "full_span_max_abs": sink.carry_max_abs,
                                                   "carry_scalars": ledger.returned["carry_scalars"],
                                                   "past_protection_or_scientific_records_edited": False,
                                                   "carry_sign_or_gain_gate": False})
        phase = "AGGREGATION"
        summaries(np, case_runs, panel, ledger, files)
        require(ledger.unknown is None and ledger.returned["source_files"] == 44, "DISPATCH_CLOSURE")
        for key, count in ledger.charged.items():
            require(ledger.returned[key] <= count <= ledger.caps[key], "FINAL_BUDGET_CLOSURE")
        ledger.event("WORKER_COMPLETE", total_source_case_blocks=sink.count)
        ledger.journal.close()
        files.emit("execution_receipt.json", {
            "status": "READY_FOR_LOCAL_ACCEPTANCE", "phase": phase,
            "payload_artifacts": files.hashes, "capped": ledger.caps, "reserved_upper": dict(ledger.charged),
            "confirmed_returned": dict(ledger.returned), "unknown": ledger.unknown,
            "selected_sources": len(panel), "selected_links": len(panel) * 2,
            "zero_calls": {key: 0 for key in ("training", "encoder", "calibrator_fit", "label_truth_score",
                                             "parameter_gradient", "new_FD", "interior_AD", "scientific_retry",
                                             "new_inferential_statistics")},
            "scope": {"FR50": "UNRELEASED", "FR60": "UNRELEASED", "GATE": "EXPOSED_DEVELOPMENT_ONLY",
                      "independent_confirmation": "NOT_EVALUATED", "CI_p_margin_scientific_PASS": "NOT_EVALUATED"},
        })
    except BaseException as error:
        failure = {"phase": phase, "code": type(error).__name__, "reason": str(error),
                   "confirmed_returned": dict(ledger.returned) if ledger else {},
                   "reserved_upper": dict(ledger.charged) if ledger else {},
                   "unknown": ledger.unknown if ledger else None, "at_utc": utc()}
        write_control("worker_failure.json", failure)
        print(f"WORKER_STOP phase={phase} code={type(error).__name__} reason={error}", flush=True)
        return 1
    return 0


def audit_outputs(binding, status, hard_stop):
    clock_guard(binding)
    directory = list(RUN_DIR.iterdir())
    require(len(directory) <= 38 and all(path.is_file() and not path.is_symlink() for path in directory),
            "OUTPUT_INVENTORY")
    hashes = {}
    small_controls = {}
    line_counts = {}
    accepted_bytes = 0
    pending = None
    committed = collections.Counter()
    for path in sorted(directory):
        clock_guard(binding)
        size = path.stat().st_size
        require(accepted_bytes + size <= 128 * 2**20, "ACCEPTANCE_BYTES_CAP")
        digest = hashlib.sha256()
        seen = 0
        with path.open("rb") as f:
            if path.name == "dispatch_ledger.jsonl":
                for line in f:
                    digest.update(line)
                    seen += len(line)
                    if not line.endswith(b"\n"):
                        require(status != 0, "TRUNCATED_DISPATCH_JOURNAL")
                        break
                    rec = json.loads(line)
                    if rec["event"] == "RESERVED_BEFORE_DISPATCH":
                        require(pending is None, "AUDIT_DOUBLE_DISPATCH")
                        pending = {"sequence": rec["sequence"], "block": rec["block"], "reserved": rec["reserved"]}
                    if rec["event"] == "RETURN_COMMITTED":
                        require(pending and pending["sequence"] == rec["sequence"], "AUDIT_DISPATCH_ORDER")
                        committed.update(rec["returned"])
                        pending = None
            elif path.name in {"execution_receipt.json", "worker_failure.json", "pilot_receipt.json"}:
                raw = f.read()
                seen = len(raw)
                digest.update(raw)
                small_controls[path.name] = json.loads(raw)
            else:
                lines = 0
                for buf in iter(lambda: f.read(1024 * 1024), b""):
                    seen += len(buf)
                    digest.update(buf)
                    if path.suffix == ".jsonl":
                        lines += buf.count(b"\n")
                line_counts[path.name] = lines
        require(seen == size, "AUDIT_FILE_MUTATED")
        accepted_bytes += size
        hashes[path.name] = {"bytes": seen, "sha256": digest.hexdigest()}
    ready = small_controls.get("execution_receipt.json")
    if status == 0 and hard_stop is None:
        require(ready is not None and pending is None and
                ready["status"] == "READY_FOR_LOCAL_ACCEPTANCE" and ready["unknown"] is None and
                dict(committed) == ready["confirmed_returned"], "ACCEPTANCE_DISPATCH_OR_RECEIPT")
        require(all(hashes[k] == v for k, v in ready["payload_artifacts"].items()), "ACCEPTANCE_HASH")
        require(small_controls["pilot_receipt.json"]["status"] == "PASSED_ENGINEERING_RELEASE" and
                line_counts["source_results.jsonl"] == ready["selected_sources"] * 10 and
                line_counts["span_summary.jsonl"] == 5184 and committed["source_files"] == 44, "ACCEPTANCE_COUNTS")
    return hashes, accepted_bytes, pending, dict(committed), small_controls.get("worker_failure.json")


def supervisor():
    import psutil

    require(os.path.abspath(sys.executable) == str(PYTHON) and sys.flags.isolated
            and sys.flags.dont_write_bytecode, "LAUNCH_WITH_PROJECT_RUNTIME_I_B")
    check_resources(resources(psutil, [os.getpid()]))
    wall_started, monotonic_started = time.time(), time.monotonic()
    RUN_DIR.mkdir(parents=True, exist_ok=False)
    plan = load_control(PLAN)
    caps = plan["proposed_caps_pending_release"]
    binding = {
        "authority": AUTHORITY, "status": "RELEASED_FOR_ONE_CONTROLLED_EXECUTION",
        "owner_pid": os.getpid(), "wall_start": wall_started, "monotonic_start": monotonic_started,
        "started_at_utc": datetime.datetime.fromtimestamp(wall_started, datetime.timezone.utc).isoformat(),
        "wall_deadline": wall_started + caps["formal_clock_wall_seconds"],
        "monotonic_deadline": monotonic_started + caps["formal_clock_monotonic_seconds"],
        "launcher": file_descriptor(Path(__file__).resolve()), "plan": file_descriptor(PLAN),
        "contract": file_descriptor(CONTRACT), "prior": file_descriptor(HANDOFF / PRIOR_REL),
        "python": str(PYTHON), "python_resolved": str(PYTHON.resolve()), "python_version": sys.version,
        "dependencies": {m: {"version": importlib.metadata.version(m),
                             "origin": str(Path(importlib.util.find_spec(m).origin).resolve())}
                         for m in ("numpy", "torch", "psutil")},
        "dispatch_device": "CPU", "numeric_dtype": "float64", "batch": 1, "threads": 1,
        "interop_threads": 1, "source_manifest_descriptor_count": len(plan["future_input_descriptors_reference_only"]),
        "scientific_retries": 0, "resource_check_seconds": 5,
    }
    clock_guard(binding)
    write_control("binding.json", binding)
    env = dict(os.environ)
    env.update({k: "1" for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                                  "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS")})
    argv = [str(PYTHON), "-I", "-B", str(Path(__file__).resolve()), "--worker"]
    hard_stop = audit_error = None
    peaks = {"rss_bytes_max": 0, "mps_driver_bytes_max": 0, "available_bytes_min": None, "free_disk_bytes_min": None}
    samples = 0
    child = subprocess.Popen(argv, cwd=ROOT, env=env)
    try:
        with (RUN_DIR / "resources.jsonl").open("xb") as log:
            while True:
                status = child.poll()
                try:
                    c = psutil.Process(child.pid)
                    child_metadata = {"state": c.status(), "create_time": c.create_time(), "command": c.cmdline()}
                    if status is None:
                        require(child_metadata["state"] not in ("stopped", "tracing-stop")
                                and child_metadata["command"] == argv, "WORKER_IDENTITY_OR_SUSPENSION")
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    child_metadata = {"state": "EXITED_OR_REAPED", "create_time": None, "command": None}
                rr = resources(psutil, [os.getpid(), child.pid] if status is None else [os.getpid()])
                raw = encoded({"at_utc": utc(), "elapsed_seconds": time.monotonic() - monotonic_started,
                               "pid": child.pid, "process": child_metadata, "exit_if_known": status, **rr}) + b"\n"
                require(log.write(raw) == len(raw), "SHORT_RESOURCE_LOG_WRITE")
                log.flush()
                os.fsync(log.fileno())
                samples += 1
                for key in ("rss_bytes", "mps_driver_bytes"):
                    peaks[key + "_max"] = max(peaks[key + "_max"], rr[key])
                for key in ("available_bytes", "free_disk_bytes"):
                    previous = peaks[key + "_min"]
                    peaks[key + "_min"] = rr[key] if previous is None else min(previous, rr[key])
                clock_guard(binding)
                check_resources(rr)
                if status is not None:
                    break
                try:
                    child.wait(timeout=min(5., binding["monotonic_deadline"] - time.monotonic(),
                                           binding["wall_deadline"] - time.time()))
                except subprocess.TimeoutExpired:
                    pass
    except BaseException as error:
        hard_stop = f"{type(error).__name__}: {error}"
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5.)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    status = child.wait()
    hashes, accepted_bytes, pending, committed, worker_failure = {}, 0, None, {}, None
    try:
        check_resources(resources(psutil, [os.getpid()]))
        hashes, accepted_bytes, pending, committed, worker_failure = audit_outputs(binding, status, hard_stop)
        clock_guard(binding)
    except BaseException as error:
        audit_error = f"{type(error).__name__}: {error}"
    accepted = status == 0 and hard_stop is None and audit_error is None
    closing = {
        "status": "PRIVATE_FINITE_DEVELOPMENT_ACCEPTED" if accepted else "STOPPED_NOT_ACCEPTED",
        "worker_pid": child.pid, "worker_exit_code": status, "supervisor_stop": hard_stop,
        "acceptance_error": audit_error, "worker_failure": worker_failure,
        "uncommitted_dispatch": pending if not audit_error else "UNKNOWN_AUDIT_NOT_CLOSED",
        "completed_ledger_totals": committed, "acceptance_bytes_read": accepted_bytes,
        "resource_samples": samples, "sampled_resource_extrema": peaks,
        "elapsed_wall_seconds": time.time() - wall_started, "elapsed_monotonic_seconds": time.monotonic() - monotonic_started,
        "closed_at_utc": utc(),
    }
    write_control("closing_receipt.json", closing)
    if accepted:
        clock_guard(binding)
        seal = {
            "status": closing["status"], "artifacts": {**hashes, "closing_receipt.json": {
                "bytes": len(encoded(closing)) + 1, "sha256": sha(encoded(closing) + b"\n")}},
            "completed_sources": committed["source_files"], "completed_summary_rows": committed["summary_rows"],
            "result_scope": "FINITE_EXPOSED_DEVELOPMENT_O_SRC",
        }
        write_control("run_seal.json", seal)
        print(f"ACCEPTED summary_rows={committed['summary_rows']} elapsed_seconds={closing['elapsed_wall_seconds']:.2f}", flush=True)
        return 0
    print(f"STOPPED worker_exit={status} reason={hard_stop or audit_error or 'see_worker_failure'}", flush=True)
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    sys.exit(worker() if parser.parse_args().worker else supervisor())
