"""Time one sealed exact-response lookup for the local FR40 A2 cost check."""

import ast
from collections import Counter
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import time

import psutil


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "planning/manuscript_midpoint_query_a2_execution_contract_v0_2.json"
AUTH = ROOT / "planning/manuscript_midpoint_query_a2_fr40_lookup_authority_v0_2.json"
RUN = ROOT / "output/manuscript_midpoint_query_v0_1/A2/fr40_v0_2"
META = RUN / "01_candidate_metadata"
OUT = RUN / "02_exact_lookup"
GIB = 2**30


class Stop(RuntimeError):
    """Cost check cannot proceed."""


def require(ok, code):
    if not ok:
        raise Stop(code)


def descriptor(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def emit(filename, record, append=False):
    raw = (json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()
    with (OUT / filename).open("ab" if append else "xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return descriptor(raw)


def guard(clock, pids=None):
    wall, mono = clock
    require(time.time() < wall and time.monotonic() < mono, "HARD_CLOCK")
    if pids is None:
        return
    rss = 0
    for pid in pids:
        try:
            rss += psutil.Process(pid).memory_info().rss
        except psutil.NoSuchProcess:
            pass
    record = {"rss_bytes": rss, "mps_driver_bytes": 0, "mps_scope": "CPU-only lookup",
              "available_bytes": psutil.virtual_memory().available,
              "disk_free_bytes": psutil.disk_usage(str(ROOT)).free}
    emit("resources.jsonl", record, append=True)
    require(rss <= 36*GIB and record["available_bytes"] >= 4*GIB
            and record["disk_free_bytes"] >= 20*GIB, "HARD_RESOURCE")


def clocked_source_lookups(raw, offsets_by_endpoint, clock):
    timings = {ep: [] for ep in ("Kd", "Ki")}
    for ep, i, offsets in offsets_by_endpoint:
        guard(clock)
        start = time.perf_counter_ns()
        values = [struct.unpack_from("<d", raw, i0)[0] for i0 in offsets]
        queried_ns = time.perf_counter_ns() - start
        require(all(math.isfinite(v) for v in values), "NONFINITE_EXACT_RESPONSE")
        start = time.perf_counter_ns()
        maximum = max(map(abs, values))
        rank_ns = time.perf_counter_ns() - start
        require(math.isfinite(maximum), "NONFINITE_MAXIMUM")
        timings[ep].append({"panel_ordinal_within_endpoint": i, "pilot": i == 1, "responses": len(values),
                            "lookup_ns": queried_ns, "scan_max_ns": rank_ns,
                            "lookup_plus_scan_max_ns": queried_ns + rank_ns})
    require(all(len(group) == 16 for group in timings.values()), "TIMING_GROUP_COUNTS")
    return timings


def worker(plan, binding):
    require(os.getppid() == binding["supervisor_pid"], "WORKER_PARENT")
    clock = binding["dual_deadlines"]
    info = plan["exact_saved_lookup"]
    source = info["input"]
    manifest_start = time.perf_counter_ns()
    manifest = (META / "candidate_manifest.private.json").read_bytes()
    require(descriptor(manifest) == binding["sealed_candidate_manifest"], "MANIFEST_SEAL")
    candidate = json.loads(manifest)
    require(candidate["span"] == 1 and candidate["function"] == "q" and candidate["operation"] == "D",
            "MANIFEST_SCOPE")
    sources = candidate["sources"]
    shape = info["array"]["shape"]
    all_rows = [c["operation_row"] for s in sources for c in s["candidates"]]
    require(Counter(s["endpoint"] for s in sources) == {"Kd": 16, "Ki": 16}
            and all(len(s["candidates"]) == 32 for s in sources)
            and len(all_rows) == len(set(all_rows)) == info["projected_targets_max"]
            and all(type(row) is int and 0 <= row < shape[0] for row in all_rows), "MANIFEST_LOOKUP_ROWS")
    manifest_ns = time.perf_counter_ns() - manifest_start
    guard(clock)
    emit("dispatch.jsonl", {"event": "RESERVED", "id": source["id"], "physical_bytes": source["bytes"],
                           "identity_plus_use_logical_bytes": 2*source["bytes"],
                           "allowed_field_count": info["projected_targets_max"]}, append=True)
    physical_read = 0
    try:
        start = time.perf_counter_ns()
        with Path(source["path"]).open("rb") as stream:
            before = os.fstat(stream.fileno())
            require(before.st_size == source["bytes"], "RESPONSE_BYTE_COUNT")
            raw = stream.read(source["bytes"])
            physical_read = len(raw)
            after = os.fstat(stream.fileno())
        first_read_ns = time.perf_counter_ns() - start
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "RESPONSE_FILE_CHANGED")
        start = time.perf_counter_ns()
        require(descriptor(raw) == {k: source[k] for k in ("bytes", "sha256")}, "RESPONSE_SEAL")
        seal_ns = time.perf_counter_ns() - start
    except Exception as exc:
        emit("dispatch.jsonl", {"event": "FAILED", "id": source["id"], "physical_bytes_observed": physical_read,
                                "logical_completion": "UNKNOWN",
                                "failure": str(exc) if isinstance(exc, Stop) else type(exc).__name__}, append=True)
        raise
    emit("dispatch.jsonl", {"event": "READ_VERIFIED", "id": source["id"], "physical_bytes": physical_read,
                           "identity_plus_use_logical_bytes": 2*physical_read, "sha256": source["sha256"]}, append=True)
    guard(clock)
    start = time.perf_counter_ns()
    require(raw[:8] == b"\x93NUMPY\x01\x00", "RESPONSE_NPY_VERSION")
    start_of_data = 10 + struct.unpack_from("<H", raw, 8)[0]
    header = ast.literal_eval(raw[10:start_of_data].decode("ascii").strip())
    require(header == {"descr": "<f8", "fortran_order": False, "shape": tuple(shape)}
            and len(raw) == start_of_data + math.prod(shape)*8, "RESPONSE_NPY_HEADER")
    ep_ord = Counter()
    scope = []
    field = info["array"]["q_index"]*shape[2] + info["array"]["D_response_field_index"]
    for s in sources:
        ep = s["endpoint"]
        ep_ord[ep] += 1
        offset = [start_of_data + 8*(c["operation_row"]*shape[1]*shape[2] + field) for c in s["candidates"]]
        scope.append((ep, ep_ord[ep], offset))
    index_ns = time.perf_counter_ns() - start
    # Resolve both fixed pilot sources first. The pilot consumes two of the 32 source slots.
    scope.sort(key=lambda entry: entry[1] != 1)
    times = clocked_source_lookups(raw, scope, clock)
    sums = {}
    for ep, blocks in times.items():
        elapsed = [m["lookup_plus_scan_max_ns"] for m in blocks]
        sums[ep] = {"min": min(elapsed), "median": statistics.median(elapsed), "max": max(elapsed)}
    lookup_total_ns = sum(sum(m["lookup_plus_scan_max_ns"] for m in blocks) for blocks in times.values())
    record = {
        "status": "A2_FR40_LOCAL_EXACT_LOOKUP_VERIFIED", "case": info["case"], "scope": "LOCAL_ARCHIVE_ONLY",
        "all_sources_enumerated": len(sources), "candidates_per_source": 32, "decoded_exact_responses": len(all_rows),
        "row_deduplication_savings": 0, "input_physical_bytes": physical_read, "input_logical_bytes": 2*physical_read,
        "first_archive_read_ns": first_read_ns, "OS_page_cache_state": "UNKNOWN",
        "archive_seal_sha256_ns": seal_ns, "sealed_manifest_setup_ns": manifest_ns,
        "header_and_offset_setup_ns": index_ns, "summed_resident_lookup_and_scan_max_ns": lookup_total_ns,
        "by_endpoint_resident_lookup_plus_max_ns": sums, "timings_by_endpoint": times,
        "model_payload_reads": 0, "head_forward": 0, "input_AD": 0, "scientific_scoring": 0,
        "actual_source_budget": None, "actual_source_cost_qualification": "NOT_EVALUATED"
    }
    start = time.perf_counter_ns()
    seal = emit("lookup_receipt.json", record)
    emit("lookup_output_receipt.json", {"lookup_receipt": seal, "timing_output_write_and_fsync_ns": time.perf_counter_ns() - start})


def supervisor(plan, auth, entry):
    controls = {name: descriptor(path.read_bytes()) for name, path in {
        "contract": PLAN, "executor": Path(__file__), "candidate_acceptance": META / "candidate_acceptance.json"
    }.items()}
    require(auth["bound_controls"] == controls and auth["released_input"] == plan["exact_saved_lookup"]["input"],
            "LOOKUP_AUTHORITY_BINDING")
    require(auth["scope"] == "LOCAL_ARCHIVE_ONLY" and auth["actual_source_budget"] is None, "LOOKUP_SCOPE")
    checked = json.loads((META / "candidate_acceptance.json").read_text())
    require(checked["candidate_status"] == "FR40_CANDIDATE_QUALIFIED", "CANDIDATE_NOT_ACCEPTED")
    caps = plan["exact_saved_lookup"]["limits"]
    times = (entry[0] + caps["wall_seconds"], entry[1] + caps["monotonic_seconds"])
    guard(times)
    OUT.mkdir(parents=True, exist_ok=False)
    emit("binding.json", {"supervisor_pid": os.getpid(), "dual_deadlines": times, "authority": descriptor(AUTH.read_bytes()),
                          "controls": controls, "sealed_candidate_manifest": checked["artifacts"]["candidate_manifest.private.json"]})
    process = None
    failure = None
    try:
        guard(times, [os.getpid()])
        process = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__)), "--worker"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while True:
            guard(times, [os.getpid(), process.pid])
            try:
                need = process.wait(timeout=min(5, times[1] - time.monotonic()))
                require(need == 0, "LOOKUP_WORKER_FAILED")
                break
            except subprocess.TimeoutExpired:
                continue
        guard(times, [os.getpid()])
        names = ("binding.json", "dispatch.jsonl", "resources.jsonl", "lookup_receipt.json", "lookup_output_receipt.json")
        files = {name: (OUT / name).read_bytes() for name in names}
        accepted = {name: descriptor(raw) for name, raw in files.items()}
        amount = sum(item["bytes"] for item in accepted.values())
        receipt = json.loads(files["lookup_receipt.json"])
        dispatch = [json.loads(line) for line in files["dispatch.jsonl"].splitlines()]
        require([event["event"] for event in dispatch] == ["RESERVED", "READ_VERIFIED"]
                and receipt["status"] == "A2_FR40_LOCAL_EXACT_LOOKUP_VERIFIED"
                and receipt["input_physical_bytes"] == caps["physical_input_bytes"] == dispatch[1]["physical_bytes"]
                and receipt["input_logical_bytes"] == caps["identity_plus_use_logical_bytes"]
                and receipt["decoded_exact_responses"] == plan["exact_saved_lookup"]["projected_targets_max"]
                and json.loads(files["lookup_output_receipt.json"])["lookup_receipt"] == accepted["lookup_receipt.json"]
                and amount <= caps["cumulative_acceptance_read_MiB"]*2**20 and len(names)+2 <= caps["artifacts"],
                "LOOKUP_ACCEPTANCE")
        guard(times)
        emit("lookup_acceptance.json", {"status": receipt["status"], "acceptance_read_bytes": amount, "artifacts": accepted})
    except Exception as exc:
        failure = str(exc) if isinstance(exc, Stop) else type(exc).__name__
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        emit("supervisor_receipt.json", {
            "failure": failure, "worker_exit_code": process.returncode if process is not None else None,
            "wall_elapsed_seconds": time.time() - entry[0], "monotonic_elapsed_seconds": time.monotonic() - entry[1],
            "ended_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    return 0 if failure is None else 1


def main():
    entry = (time.time(), time.monotonic())
    plan = json.loads(PLAN.read_text())
    if sys.argv[1:] == ["--worker"]:
        try:
            worker(plan, json.loads((OUT / "binding.json").read_text()))
        except Exception as exc:
            emit("worker_failure.json", {"failure": str(exc) if isinstance(exc, Stop) else type(exc).__name__})
            return 1
        return 0
    return supervisor(plan, json.loads(AUTH.read_text()), entry)


if __name__ == "__main__":
    sys.exit(main())
