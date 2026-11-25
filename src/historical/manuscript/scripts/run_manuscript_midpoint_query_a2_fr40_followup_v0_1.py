"""Run one local FR40 board request and privately return candidate ordinals."""

import ast
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import struct
import subprocess
import sys
import time

import psutil


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
RUNTIME = ROOT / "research/mechanism_v0_2/runtime_v0_1/bin/python"
RUN = ROOT / "output/manuscript_midpoint_query_v0_1/A2/fr40_followup_v0_1/run_001"
AUTH = ROOT / "planning/manuscript_midpoint_query_a2_fr40_followup_authority_v0_1.json"
PLAN = ROOT / "planning/manuscript_midpoint_query_a2_execution_contract_v0_2.json"
CANDIDATE = RUN.parents[1] / "fr40_v0_2/01_candidate_metadata"
CASE = "formal_017_G"
COUNTS = {"Kd": 16, "Ki": 16}
GIB = 2**30
MIB = 2**20


class Stop(RuntimeError):
    """Execution or acceptance cannot continue at the registered scope."""


def require(ok, reason):
    if not ok:
        raise Stop(reason)


def stamp(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def emit(name, value, append=False):
    raw = (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()
    mode = "ab" if append else "xb"
    with (RUN / name).open(mode) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return stamp(raw)


def event(kind, **fields):
    emit("dispatch.jsonl", {"event": kind, "time_ns": time.time_ns(), **fields}, append=True)


def guard(deadlines):
    require(time.time() < deadlines["wall"] and time.monotonic() < deadlines["monotonic"], "HARD_CLOCK")


def resources(deadlines, *pids):
    guard(deadlines)
    rss = 0
    for pid in pids:
        try:
            rss += psutil.Process(pid).memory_info().rss
        except psutil.NoSuchProcess:
            pass
    available = psutil.virtual_memory().available
    free = psutil.disk_usage(str(ROOT)).free
    emit("resources.jsonl", {"time_ns": time.time_ns(), "rss_bytes": rss, "available_bytes": available,
                            "disk_free_bytes": free, "MPS_driver_bytes": 0, "MPS_scope": "CPU-only"},
         append=True)
    require(rss <= 36*GIB and available >= 4*GIB and free >= 20*GIB, "HARD_RESOURCE")


def read_archive(source):
    path = Path(source["path"])
    actual = 0
    event("ARCHIVE_RESERVED", descriptor=source["id"], physical_cap=source["bytes"], logical_cap=2*source["bytes"])
    try:
        started = time.perf_counter_ns()
        with path.open("rb") as handle:
            initial = os.fstat(handle.fileno())
            require(initial.st_size == source["bytes"], "ARCHIVE_BYTE_COUNT")
            raw = handle.read(source["bytes"])
            actual = len(raw)
            final = os.fstat(handle.fileno())
        reading_ns = time.perf_counter_ns() - started
        require((initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns)
                == (final.st_size, final.st_mtime_ns, final.st_ctime_ns), "ARCHIVE_CHANGED")
        started = time.perf_counter_ns()
        require(stamp(raw) == {key: source[key] for key in ("bytes", "sha256")}, "ARCHIVE_SHA256")
        verify_ns = time.perf_counter_ns() - started
    except Exception as exc:
        event("ARCHIVE_FAILED", physical_bytes_observed=actual, uncertain_use=True,
              reason=str(exc) if isinstance(exc, Stop) else type(exc).__name__)
        raise
    event("ARCHIVE_VERIFIED", descriptor=source["id"], physical_bytes=actual, logical_bytes=2*actual,
          whole_reads=1, sha256=source["sha256"])
    return raw, reading_ns, verify_ns


def offsets(raw, array, sources):
    require(raw[:8] == b"\x93NUMPY\x01\x00", "ARCHIVE_NPY_VERSION")
    start = 10 + struct.unpack_from("<H", raw, 8)[0]
    shape = tuple(array["shape"])
    header = ast.literal_eval(raw[10:start].decode("ascii").strip())
    require(header == {"descr": "<f8", "fortran_order": False, "shape": shape}
            and len(raw) == start + math.prod(shape)*8, "ARCHIVE_ARRAY_HEADER")
    require(array["q_index"] == 0 and array["D_response_field_index"] == 3, "ARCHIVE_FIELD")
    groups = {ep: [] for ep in COUNTS}
    seen = set()
    for source in sources:
        rows = source["candidates"]
        ep = source["endpoint"]
        require(ep in groups and len(rows) == len({r["C_identity"] for r in rows}) == 32,
                "MANIFEST_CANDIDATE_COUNTS")
        row_set = {r["operation_row"] for r in rows}
        require(len(row_set) == 32 and not (seen & row_set)
                and all(type(i) is int and 0 <= i < shape[0] for i in row_set), "OPERATION_ROW_JOIN")
        seen.update(row_set)
        groups[ep].append([start + 8*(r["operation_row"]*shape[1]*shape[2]+3) for r in rows])
    require({ep: len(rows) for ep, rows in groups.items()} == COUNTS and len(seen) == 1024, "MANIFEST_FULL_PANEL")
    return groups


def select_first_max(values):
    maximum = -1.0
    winners = []
    for index, value in enumerate(values, start=1):
        require(math.isfinite(value), "NONFINITE_RESPONSE")
        magnitude = abs(value)
        if magnitude > maximum:
            maximum = magnitude
            winners = [index]
        elif magnitude == maximum:
            winners.append(index)
    return winners[0], len(winners)


def query(raw, groups, deadlines):
    timings = {ep: [] for ep in COUNTS}
    results = {ep: [] for ep in COUNTS}
    for i in range(16):
        guard(deadlines)
        event("SOURCE_PAIR_RESERVED", endpoint_source_ordinal=i+1, response_slots=64, pilot=i == 0)
        for ep, source_offsets in groups.items():
            started = time.perf_counter_ns()
            values = [struct.unpack_from("<d", raw, n)[0] for n in source_offsets[i]]
            read_ns = time.perf_counter_ns() - started
            started = time.perf_counter_ns()
            ordinal, tie_count = select_first_max(values)
            select_ns = time.perf_counter_ns() - started
            timings[ep].append({"source_ordinal": i+1, "lookup_ns": read_ns, "selection_ns": select_ns,
                                "lookup_and_selection_ns": read_ns+select_ns})
            results[ep].append({"source_ordinal": i+1, "candidate_ordinal": ordinal,
                                "max_abs_tie_count": tie_count, "responses": len(values)})
        event("SOURCE_PAIR_COMPLETE", endpoint_source_ordinal=i+1, response_slots=64, pilot=i == 0)
    return timings, results


def worker():
    entered = time.perf_counter_ns()
    binding = json.loads((RUN / "binding.json").read_bytes())
    require(os.getppid() == binding["supervisor_pid"], "SUPERVISOR_IDENTITY")
    deadlines = binding["deadlines"]
    selected = binding["response_archive"]
    started = time.perf_counter_ns()
    manifest = (CANDIDATE / "candidate_manifest.private.json").read_bytes()
    require(stamp(manifest) == binding["manifest"], "MANIFEST_SHA256")
    panel = json.loads(manifest)
    require((panel["function"], panel["operation"], panel["span"]) == ("q", "D", 1), "MANIFEST_SCIENCE_SCOPE")
    manifest_ns = time.perf_counter_ns()-started
    guard(deadlines)
    raw, read_ns, verify_ns = read_archive(selected["input"])
    started = time.perf_counter_ns()
    groups = offsets(raw, selected["array"], panel["sources"])
    offset_ns = time.perf_counter_ns()-started
    availability_ns = time.perf_counter_ns()-entered
    started = time.perf_counter_ns()
    timings, results = query(raw, groups, deadlines)
    batch_ns = time.perf_counter_ns()-started
    guard(deadlines)
    started = time.perf_counter_ns()
    result_hash = emit("result.private.json", {
        "scope": "FROZEN_GINE_Q_D_FULL_SPAN", "case": CASE,
        "candidate_manifest": binding["manifest"], "selected_ordinals": results
    })
    output_ns = time.perf_counter_ns()-started
    result_ready_ns = time.perf_counter_ns()-entered
    aggregates = {}
    for ep, group in timings.items():
        values = [row["lookup_and_selection_ns"] for row in group]
        aggregates[ep] = {"min": min(values), "median": statistics.median(values), "max": max(values)}
    emit("lookup_receipt.json", {
        "case": CASE, "request": "LOCAL_ONE_PROCESS_FULL_BOARD_ONE_PRIVATE_RESULT",
        "sessions": 1, "pilot_sources": 2, "formal_sources": COUNTS, "response_slots": 1024,
        "first_archive_read_ns": read_ns, "os_page_cache_state": "UNKNOWN", "seal_sha256_ns": verify_ns,
        "manifest_read_validation_ns": manifest_ns, "header_and_offsets_ns": offset_ns,
        "worker_to_archive_ready_ns": availability_ns, "board_queries_and_audit_ns": batch_ns,
        "private_output_write_sync_ns": output_ns, "worker_to_private_result_ready_ns": result_ready_ns,
        "resident_source_in_memory_ns": aggregates, "source_timings": timings, "private_result": result_hash,
        "counter":{"model_payload_reads":0, "native_head_forward":0, "input_AD":0, "label_reads":0,
                   "science_scoring":0, "archive_retries":0},
    })


def accept(scope, worker_exit):
    names = ("workload.json","binding.json","dispatch.jsonl","resources.jsonl","lookup_receipt.json",
             "result.private.json","supervisor_receipt.json")
    read = {name:(RUN / name).read_bytes() for name in names}
    require(sum(map(len, read.values())) <= 8*MIB and len(names)+3 <= 12, "ACCEPTANCE_BUDGET")
    receipt = json.loads(read["lookup_receipt.json"])
    result = json.loads(read["result.private.json"])
    events = [json.loads(line) for line in read["dispatch.jsonl"].splitlines()]
    archives = [e for e in events if e["event"] == "ARCHIVE_VERIFIED"]
    pairs = [e for e in events if e["event"] == "SOURCE_PAIR_COMPLETE"]
    expected_bytes = scope["input"]["bytes"]
    require(worker_exit == 0 and len(archives) == 1 and archives[0]["whole_reads"] == 1
            and archives[0]["physical_bytes"] == expected_bytes
            and archives[0]["logical_bytes"] == 2*expected_bytes
            and len(events) == 34 and [e["endpoint_source_ordinal"] for e in pairs] == list(range(1,17))
            and sum(e["response_slots"] for e in pairs) == receipt["response_slots"] == 1024,
            "ACCOUNTING")
    require(receipt["private_result"] == stamp(read["result.private.json"])
            and result["case"] == receipt["case"] == CASE,
            "RESULT_SEAL")
    for ep, n in COUNTS.items():
        group = result["selected_ordinals"][ep]
        require([row["source_ordinal"] for row in group] == list(range(1,n+1))
                and all(row["responses"] == 32 and 1 <= row["candidate_ordinal"] <= 32
                        and 1 <= row["max_abs_tie_count"] <= 32 for row in group), "RESULT_COUNTS")
    return {"status":"LOCAL_FR40_OPERATIONAL_SESSION_ACCEPTED",
            "physical_archive_bytes":expected_bytes, "identity_plus_use_logical_bytes":2*expected_bytes,
            "cumulative_acceptance_reads_bytes":sum(map(len,read.values())),
            "artifacts":{name:stamp(raw) for name,raw in read.items()}}


def supervise(entry):
    auth_raw = AUTH.read_bytes()
    auth = json.loads(auth_raw)
    controlled = {"executor": SELF, "plan": ROOT / "docs/A2本机FR40后续流程执行方案_v0.1.md",
                  "contract": PLAN, "candidate_acceptance": CANDIDATE / "candidate_acceptance.json"}
    inputs = {key:path.read_bytes() for key,path in controlled.items()}
    identity = {key:stamp(raw) for key,raw in inputs.items()}
    require(auth["controls"] == identity, "AUTHORITY_STALE")
    require(auth["status"] == "ONE_ARCHIVE_LOCAL_BOARD_RELEASED" and auth["workload"]["case"] == CASE,
            "AUTHORITY_SCOPE")
    plan = json.loads(inputs["contract"])
    scope = plan["exact_saved_lookup"]
    require(auth["archive"] == scope["input"] and scope["case"] == CASE, "AUTHORITY_ARCHIVE")
    candidate = json.loads(inputs["candidate_acceptance"])
    require(candidate["candidate_status"] == "FR40_CANDIDATE_QUALIFIED", "CANDIDATE_NOT_ACCEPTED")
    caps = scope["limits"]
    require(caps["physical_input_bytes"] == scope["input"]["bytes"]
            and caps["identity_plus_use_logical_bytes"] == 2*scope["input"]["bytes"]
            and caps["whole_reads_per_file"] == 1 and scope["projected_targets_max"] == 1024,
            "AUTHORITY_BUDGET")
    deadlines = {"wall":entry[0]+caps["wall_seconds"], "monotonic":entry[1]+caps["monotonic_seconds"]}
    guard(deadlines)
    RUN.mkdir(parents=True, exist_ok=False)
    emit("workload.json", auth["workload"])
    emit("binding.json", {"supervisor_pid":os.getpid(),"deadlines":deadlines,"clock_start_wall":entry[0],
                          "clock_start_monotonic":entry[1], "controls":identity,"authority":stamp(auth_raw),
                          "manifest":candidate["artifacts"]["candidate_manifest.private.json"],
                          "response_archive":scope,"runtime":{"python":platform.python_version(),
                          "interpreter":str(RUNTIME),"machine":platform.machine(),"psutil":psutil.__version__}})
    process = None
    failure = None
    try:
        resources(deadlines, os.getpid())
        process = subprocess.Popen([str(RUNTIME), "-I", "-B", str(SELF), "--worker"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while True:
            resources(deadlines, os.getpid(), process.pid)
            try:
                require(process.wait(timeout=min(5, deadlines["monotonic"]-time.monotonic())) == 0,
                        "WORKER_FAILED")
                break
            except subprocess.TimeoutExpired:
                continue
        run_wall_seconds = time.monotonic()-entry[1]
        resources(deadlines, os.getpid())
    except Exception as exc:
        run_wall_seconds = None
        failure = str(exc) if isinstance(exc, Stop) else type(exc).__name__
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        status = None if process is None else process.returncode
        emit("supervisor_receipt.json", {"worker_exit_code":status,"failure":failure,
                                        "entry_to_worker_exit_seconds":run_wall_seconds})
    if failure is not None:
        return 1
    try:
        accepted = accept(scope, status)
        guard(deadlines)
        emit("run_acceptance.json", accepted)
    except Exception as exc:
        emit("acceptance_failure.json", {"failure":str(exc) if isinstance(exc, Stop) else type(exc).__name__})
        return 1
    return 0


if __name__ == "__main__":
    entry = (time.time(), time.monotonic())
    if sys.argv[1:] == ["--worker"]:
        try:
            worker()
        except Exception as exc:
            emit("worker_failure.json", {"failure":str(exc) if isinstance(exc, Stop) else type(exc).__name__})
            sys.exit(1)
    else:
        sys.exit(supervise(entry))
