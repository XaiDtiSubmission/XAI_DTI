"""Freeze the FR40 candidate panel from four registered metadata inputs."""

import ast
from collections import Counter, defaultdict
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time

import psutil


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "planning/manuscript_midpoint_query_a2_execution_contract_v0_2.json"
AUTHORITY = ROOT / "planning/manuscript_midpoint_query_a2_fr40_metadata_authority_v0_2.json"
OUT = ROOT / "output/manuscript_midpoint_query_v0_1/A2/fr40_v0_2/01_candidate_metadata"
ENDPOINTS = ("Kd", "Ki")
METADATA_IDS = ("panel", "indices", "original_path_source_panel_exclusion", "path_recheck_panel")
CASE_IDS = {f"formal_{s:03d}_{arm}" for arm in ("S", "G") for s in (17, 29, 43, 71, 101)}
IDENTITY = {"label_group_id", "pair_group_id", "endpoint"}
GIB = 2**30
TOKEN = re.compile(r'\s*("(?:[^"\\]|\\.)*"|[-+0-9.eE]+|true|false|null|[][{}:,])')
DECODER = json.JSONDecoder()


class Stop(RuntimeError):
    """Recordable control failure."""


def require(condition, code):
    if not condition:
        raise Stop(code)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def stamp(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": digest(raw)}


def emit(name, value):
    content = canonical(value) + b"\n"
    require(len(content) <= 8 * 2**20, "ARTIFACT_SIZE")
    with (OUT / name).open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return {"bytes": len(content), "sha256": digest(content)}


def audit(item):
    with (OUT / "dispatch.jsonl").open("ab") as stream:
        stream.write(canonical({"at_utc": utc(), **item}) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def value_end(text, start):
    """Locate the end of a sealed JSON value without decoding skipped fields."""
    stack = []
    pos = start
    while True:
        match = TOKEN.match(text, pos)
        require(match is not None, "METADATA_LEXICAL_TOKEN")
        tok = match[1]
        pos = match.end()
        if tok in ("[", "{"):
            stack.append("]" if tok == "[" else "}")
        elif tok in ("]", "}"):
            require(bool(stack) and stack.pop() == tok, "METADATA_CONTAINER")
        if not stack:
            return pos


def members(text, object_mode=True):
    begin, end = ("{", "}") if object_mode else ("[", "]")
    require(text.startswith(begin), "METADATA_MEMBER_START")
    pos = 1
    seen = set()
    while True:
        while text[pos].isspace():
            pos += 1
        if text[pos] == end:
            return
        key = None
        if object_mode:
            key, pos = DECODER.raw_decode(text, pos)
            require(isinstance(key, str) and key not in seen, "METADATA_DUPLICATE_KEY")
            seen.add(key)
            while text[pos].isspace():
                pos += 1
            require(text[pos] == ":", "METADATA_MISSING_COLON")
            pos += 1
        finish = value_end(text, pos)
        yield key, text[pos:finish].lstrip()
        pos = finish
        while text[pos].isspace():
            pos += 1
        if text[pos] == end:
            return
        require(text[pos] == ",", "METADATA_MISSING_COMMA")
        pos += 1


def project(text, fields):
    selected = {key: json.loads(value) for key, value in members(text) if key in fields}
    require(set(selected) == fields, "METADATA_REQUIRED_FIELD")
    return selected


def identity(row):
    return tuple(row[key] for key in ("endpoint", "label_group_id", "pair_group_id"))


def input_metadata(item, clock):
    guard_clock(clock)
    audit({"event": "RESERVED", "id": item["id"], "max_physical_bytes": item["bytes"],
           "max_identity_plus_use_logical_bytes": 2*item["bytes"]})
    path = ROOT / item["path"]
    physically_read = 0
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            require(before.st_size == item["bytes"], "METADATA_SIZE_BEFORE_READ")
            raw = stream.read(item["bytes"])
            physically_read = len(raw)
            after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "METADATA_CHANGED")
        require(physically_read == item["bytes"] and digest(raw) == item["sha256"], "METADATA_SEAL")
    except Exception as exc:
        audit({"event": "FAILED", "id": item["id"], "reason": str(exc) if isinstance(exc, Stop) else type(exc).__name__,
               "physical_bytes_observed": physically_read, "logical_completion": "UNKNOWN"})
        raise
    audit({"event": "READ_VERIFIED", "id": item["id"], "whole_reads": 1,
           "physical_bytes": physically_read, "identity_plus_use_logical_bytes_charged": 2*physically_read,
           "sha256": item["sha256"]})
    return raw


def load_identity_metadata(inputs, clock):
    fields = IDENTITY | {"source_id", "case_ids", "n_reference_rows", "n_donor_proteins"}
    by_id = {}
    by_identity = {}
    pair_endpoints = set()
    for line in input_metadata(inputs["panel"], clock).decode("utf-8").splitlines():
        row = project(line, fields)
        key = identity(row)
        sid = row["source_id"]
        require(key[0] in ENDPOINTS and type(sid) is int and 0 <= sid < 5697
                and sid not in by_id and key not in by_identity and (key[0], key[2]) not in pair_endpoints,
                "SOURCE_IDENTITY")
        require(set(row["case_ids"]) == CASE_IDS and len(row["case_ids"]) == len(CASE_IDS)
                and 0 < row["n_donor_proteins"] <= row["n_reference_rows"], "SOURCE_SUPPORT")
        by_id[sid] = row
        by_identity[key] = row
        pair_endpoints.add((key[0], key[2]))
    require(Counter(r["endpoint"] for r in by_id.values()) == {"Kd": 206, "Ki": 2036}, "SOURCE_COUNTS")
    old_raw = input_metadata(inputs["original_path_source_panel_exclusion"], clock)
    old = [identity(project(line, IDENTITY)) for line in old_raw.decode("utf-8").splitlines()]
    new_text = input_metadata(inputs["path_recheck_panel"], clock).decode("utf-8")
    sources = [value for key, value in members(new_text) if key == "sources"]
    require(len(sources) == 1, "RECHECK_SOURCE_MEMBER")
    recheck = []
    for _, value in members(sources[0], object_mode=False):
        row = project(value, IDENTITY | {"source_id"})
        key = identity(row)
        require(key in by_identity and row["source_id"] == by_identity[key]["source_id"], "RECHECK_SOURCE_JOIN")
        recheck.append(key)
    require(Counter(k[0] for k in old) == {"Kd": 16, "Ki": 16}
            and Counter(k[0] for k in recheck) == {"Kd": 64, "Ki": 64}, "PRIOR_PANEL_COUNTS")
    require(len(set(old)) == len(old) and len(set(recheck)) == len(recheck)
            and set(old + recheck) <= by_identity.keys(), "PRIOR_PANEL_JOIN")
    old_groups = {key[2] for key in old}
    recheck_groups = {key[2] for key in recheck}
    require(old_groups.isdisjoint(recheck_groups), "PRIOR_PANELS_PAIR_GROUP_OVERLAP")
    return by_id, old_groups, recheck_groups


def index_records(raw, shape):
    require(raw[:8] == b"\x93NUMPY\x01\x00", "INDEX_NPY_VERSION")
    offset = 10 + struct.unpack_from("<H", raw, 8)[0]
    header = ast.literal_eval(raw[10:offset].decode("ascii").strip())
    require(header == {"descr": "<i8", "fortran_order": False, "shape": tuple(shape)}, "INDEX_NPY_HEADER")
    require(len(raw) == offset + shape[0]*shape[1]*8, "INDEX_NPY_EXTENT")
    return struct.iter_unpack("<14q", memoryview(raw)[offset:])


def read_indices(raw, by_id, shape, clock):
    links = defaultdict(list)
    seen_donors = defaultdict(set)
    source_corners = {}
    cache_to_key = {}
    key_to_endpoint = {}
    counts = Counter()
    for number, ix in enumerate(index_records(raw, shape)):
        if number % 8192 == 0:
            guard_clock(clock)
        sid, donor, ep = ix[:3]
        corners, cache = ix[3:7], ix[7:11]
        require(sid in by_id and ep in (0, 1) and by_id[sid]["endpoint"] == ENDPOINTS[ep]
                and 0 <= donor < 46140 and donor not in seen_donors[sid] and ix[13] == 1,
                "LINK_SOURCE_DONOR_ENDPOINT")
        require(ix[11] == by_id[sid]["n_reference_rows"] and ix[12] > 0, "LINK_REFERENCE_DENOMINATORS")
        require(len(set(corners)) == 4 and all(0 <= key < 19748 for key in corners)
                and 51265 <= cache[0] < 56962 and 0 <= cache[3] < 46140,
                "CORNER_IDENTITY_ROLE")
        for key, row in zip(corners, cache):
            require((0 <= row < 46140 or 51265 <= row < 56962)
                    and cache_to_key.setdefault(row, key) == key
                    and key_to_endpoint.setdefault(key, ep) == ep, "CORNER_CACHE_ALIAS")
        source_corner = (corners[0], cache[0])
        require(source_corners.setdefault(sid, source_corner) == source_corner, "SOURCE_CORNER_JOIN")
        seen_donors[sid].add(donor)
        links[sid].append((number, donor, corners[2], cache[2]))
        counts[ENDPOINTS[ep]] += 1
    require(counts == {"Kd": 8150, "Ki": 314509} and links.keys() == by_id.keys()
            and all(len(links[sid]) == row["n_reference_rows"] for sid, row in by_id.items()),
            "LINK_TOTALS")
    return links, counts


def rank(key, row):
    full = canonical(key)
    return (hashlib.sha256(full).digest(), full, row)


def describe(values):
    histogram = Counter(values)
    return {"n": len(values), "min": min(values, default=None), "max": max(values, default=None),
            "histogram": dict(sorted(histogram.items()))}


def build_panel(plan, by_id, links, old, recheck, clock):
    domain = plan["ranking_rule"]["hash_domain"]
    threshold = plan["distinct_C_per_source"]
    summary = {ep: {scope: [] for scope in ("all", "after_exclusions")} for ep in ENDPOINTS}
    qualified = {ep: [] for ep in ENDPOINTS}
    for sid, source in by_id.items():
        ep = source["endpoint"]
        key = [domain, "source", ep, str(source["label_group_id"]), str(source["pair_group_id"])]
        options = defaultdict(list)
        for op, donor, c_id, c_row in links[sid]:
            options[c_id].append((op, donor, c_row))
        different_aliases = sum(len({link[2] for link in group}) > 1 for group in options.values())
        entry = (len(links[sid]), len(options), different_aliases)
        summary[ep]["all"].append(entry)
        if source["pair_group_id"] in old or source["pair_group_id"] in recheck:
            continue
        summary[ep]["after_exclusions"].append(entry)
        if len(options) >= threshold:
            qualified[ep].append((rank(key, sid), sid, options))
    chosen = []
    aggregate = {}
    ready = all(len(qualified[ep]) >= plan["sources"][ep] for ep in ENDPOINTS)
    for ep in ENDPOINTS:
        aggregate[ep] = {}
        for scope, entries in summary[ep].items():
            all_links, distinct, aliases = zip(*entries) if entries else ((), (), ())
            aggregate[ep][scope] = {
                "sources": len(entries), "links": sum(all_links),
                "sources_with_at_least_32_distinct_C": sum(value >= threshold for value in distinct),
                "link_count_per_source": describe(all_links), "distinct_C_per_source": describe(distinct),
                "source_C_keys_with_multiple_valid_cache_aliases": sum(aliases)
            }
        aggregate[ep]["frozen_sources"] = plan["sources"][ep] if ready else 0
        if not ready:
            continue
        for source_rank, sid, options in sorted(qualified[ep])[:plan["sources"][ep]]:
            guard_clock(clock)
            source = by_id[sid]
            prefix = [domain, "candidate", ep, str(source["label_group_id"]), str(source["pair_group_id"])]
            candidates = []
            for c_id, group in options.items():
                options_for_key = (
                    (rank(prefix + [c_id, str(donor), str(op)], op), op, donor, c_row)
                    for op, donor, c_row in group
                )
                candidate_rank, op, donor, c_row = min(options_for_key)
                candidates.append((candidate_rank, op, donor, c_id, c_row))
            chosen.append({
                **{field: source[field] for field in IDENTITY | {"source_id"}},
                "source_order_sha256": source_rank[0].hex(),
                "candidates": [
                    {"operation_row": op, "FIT_donor_row": donor, "C_identity": c_id,
                     "C_cache_row": c_row, "order_sha256": candidate_rank[0].hex()}
                    for candidate_rank, op, donor, c_id, c_row in sorted(candidates)[:threshold]
                ]
            })
    return ready, chosen, aggregate


def guard_clock(clock):
    require(time.time() < clock["wall_deadline"] and time.monotonic() < clock["monotonic_deadline"], "HARD_CLOCK")


def resource_check(pids):
    rss = 0
    for pid in pids:
        try:
            rss += psutil.Process(pid).memory_info().rss
        except psutil.NoSuchProcess:
            pass
    sample = {"at_utc": utc(), "rss_bytes": rss, "available_bytes": psutil.virtual_memory().available,
              "disk_free_bytes": psutil.disk_usage(str(ROOT)).free,
              "MPS_driver_bytes": 0, "MPS_basis": "CPU metadata only, no MPS allocations"}
    with (OUT / "resources.jsonl").open("ab") as stream:
        stream.write(canonical(sample) + b"\n")
    require(rss <= 36*GIB and sample["available_bytes"] >= 4*GIB and sample["disk_free_bytes"] >= 20*GIB,
            "HARD_RESOURCE")


def worker(plan, binding):
    require(os.getppid() == binding["supervisor_pid"], "WORKER_PARENT")
    clock = binding["clock"]
    caps = plan["candidate_qualification"]
    inputs = {item["id"]: item for item in caps["inputs"]}
    require(tuple(inputs) == METADATA_IDS and sum(item["bytes"] for item in inputs.values())
            == caps["limits"]["physical_input_bytes"], "METADATA_INPUT_CAP")
    by_id, old, recheck = load_identity_metadata(inputs, clock)
    raw_index = input_metadata(inputs["indices"], clock)
    links, counts = read_indices(raw_index, by_id, caps["indices"]["shape"], clock)
    del raw_index
    ready, selected, aggregate = build_panel(caps, by_id, links, old, recheck, clock)
    guard_clock(clock)
    panel = None
    if ready:
        require(len(selected) == 32 and sum(len(source["candidates"]) for source in selected) == 1024, "FROZEN_PANEL_SIZE")
        panel = emit("candidate_manifest.private.json", {
            "scope": plan["evidence_scope"], "operation": "D", "span": 1, "function": "q",
            "GINE_seeds": caps["GINE_seeds"], "rank_rule": caps["ranking_rule"],
            "sources": selected
        })
    result = {
        "status": "FR40_CANDIDATE_QUALIFIED" if ready else "FR40_CANDIDATE_NOT_READY",
        "old_excluded_pair_groups": len(old), "recheck_excluded_pair_groups": len(recheck),
        "combined_excluded_pair_groups": len(old | recheck), "by_endpoint": aggregate,
        "decoded_links": sum(counts.values()), "links_by_endpoint": counts, "candidate_manifest_seal": panel,
        "response_reads": 0, "model_forwards": 0, "input_AD": 0, "scoring_calls": 0
    }
    emit("candidate_receipt.json", result)


def accept_candidate_stage(plan, clock):
    filenames = ("binding.json", "dispatch.jsonl", "resources.jsonl", "candidate_receipt.json")
    blobs = {name: (OUT / name).read_bytes() for name in filenames}
    receipt = json.loads(blobs["candidate_receipt.json"])
    status = receipt["status"]
    ledger = [json.loads(line) for line in blobs["dispatch.jsonl"].splitlines()]
    reserved = [e for e in ledger if e["event"] == "RESERVED"]
    verified = [e for e in ledger if e["event"] == "READ_VERIFIED"]
    require(len(ledger) == 8 and sorted(e["id"] for e in reserved) == sorted(METADATA_IDS)
            and sorted(e["id"] for e in verified) == sorted(METADATA_IDS), "METADATA_LEDGER")
    actual_physical = sum(e["physical_bytes"] for e in verified)
    actual_logical = sum(e["identity_plus_use_logical_bytes_charged"] for e in verified)
    caps = plan["candidate_qualification"]["limits"]
    require(actual_physical == caps["physical_input_bytes"]
            and actual_logical == caps["identity_plus_use_logical_bytes"]
            and receipt["decoded_links"] == plan["candidate_qualification"]["rows_limit"],
            "METADATA_ACCOUNTING")
    if status == "FR40_CANDIDATE_QUALIFIED":
        name = "candidate_manifest.private.json"
        blobs[name] = (OUT / name).read_bytes()
        require(receipt["candidate_manifest_seal"] == {"bytes": len(blobs[name]), "sha256": digest(blobs[name])},
                "FROZEN_MANIFEST_HASH")
        panel = json.loads(blobs[name])
        require(Counter(s["endpoint"] for s in panel["sources"]) == {"Kd": 16, "Ki": 16}
                and all(len(s["candidates"]) == len({c["C_identity"] for c in s["candidates"]}) == 32
                        for s in panel["sources"]), "FROZEN_SOURCE_CANDIDATE_COUNTS")
    else:
        require(status == "FR40_CANDIDATE_NOT_READY" and receipt["candidate_manifest_seal"] is None,
                "UNKNOWN_CANDIDATE_STATUS")
    bytes_read = sum(map(len, blobs.values()))
    require(bytes_read <= caps["cumulative_acceptance_read_MiB"]*2**20
            and len(blobs) + 2 <= caps["artifacts"], "ACCEPTANCE_CAP")
    guard_clock(clock)
    emit("candidate_acceptance.json", {
        "candidate_status": status, "verified_metadata_inputs": len(verified), "physical_input_bytes": actual_physical,
        "identity_plus_use_logical_bytes": actual_logical, "acceptance_read_bytes": bytes_read,
        "artifacts": {name: {"bytes": len(content), "sha256": digest(content)} for name, content in blobs.items()}
    })
    return status


def supervise(plan, auth, authority_stamp, entry_started):
    controls = {"machine_contract": stamp(PLAN), "markdown_contract": stamp(ROOT / plan["contract"]),
                "executor": stamp(Path(__file__)), "authority": authority_stamp}
    require(auth["bound_controls"] == {k: controls[k] for k in auth["bound_controls"]}, "AUTHORITY_CONTROL_MISMATCH")
    require(auth["scope"] == list(METADATA_IDS) and auth["actual_workload"] is None
            and not auth["response_reads_released"], "AUTHORITY_SCOPE")
    for item in plan["controls"].values():
        require(stamp(ROOT / item["path"]) == {k: item[k] for k in ("bytes", "sha256")}, "INHERITED_CONTROL_MISMATCH")
    limits = plan["candidate_qualification"]["limits"]
    clock = {"wall_start": entry_started[0], "monotonic_start": entry_started[1],
             "wall_deadline": entry_started[0] + limits["wall_seconds"],
             "monotonic_deadline": entry_started[1] + limits["monotonic_seconds"]}
    guard_clock(clock)
    OUT.mkdir(parents=True, exist_ok=False)
    emit("binding.json", {"at_utc": utc(), "supervisor_pid": os.getpid(), "clock": clock, "controls": controls})
    process = None
    status, failure = "ENGINEERING_FAILED", None
    try:
        resource_check([os.getpid()])
        process = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__)), "--worker"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while True:
            guard_clock(clock)
            resource_check([os.getpid(), process.pid])
            try:
                code = process.wait(timeout=min(5, clock["monotonic_deadline"] - time.monotonic()))
                require(code == 0, "METADATA_WORKER_FAILED")
                break
            except subprocess.TimeoutExpired:
                continue
        guard_clock(clock)
        resource_check([os.getpid()])
        status = accept_candidate_stage(plan, clock)
    except Exception as exc:
        failure = str(exc) if isinstance(exc, Stop) else type(exc).__name__
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        emit("supervisor_receipt.json", {
            "status": status, "failure": failure, "worker_exit_code": process.returncode if process is not None else None,
            "wall_elapsed_seconds": time.time()-clock["wall_start"],
            "monotonic_elapsed_seconds": time.monotonic()-clock["monotonic_start"],
            "worker_pid": process.pid if process is not None else None,
            "ended_at_utc": utc()
        })
    return 0 if failure is None else 1


def main():
    entry_started = (time.time(), time.monotonic())
    plan = json.loads(PLAN.read_bytes())
    if sys.argv[1:] == ["--worker"]:
        try:
            binding = json.loads((OUT / "binding.json").read_bytes())
            worker(plan, binding)
        except Exception as exc:
            emit("worker_failure.json", {"failure": str(exc) if isinstance(exc, Stop) else type(exc).__name__})
            return 1
        return 0
    auth_raw = AUTHORITY.read_bytes()
    return supervise(plan, json.loads(auth_raw), {"bytes": len(auth_raw), "sha256": digest(auth_raw)}, entry_started)


if __name__ == "__main__":
    sys.exit(main())
