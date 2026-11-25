"""A1 recovery entry point for an explicitly authorized repeat input read."""

import argparse
import collections
import contextlib
import datetime
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import traceback
from pathlib import Path

import psutil


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "planning/manuscript_midpoint_query_v0_1.json"
STAGE = ROOT / "output/manuscript_midpoint_query_v0_1/A1"
ORIGINAL = STAGE / "run_001"
OUTPUT = STAGE / "run_002"
RECOVERY_AUTH = STAGE / "recovery_authority_v0_1.json"
RUNTIME = ROOT / "research/mechanism_v0_2/runtime_v0_1/bin/python"
ENDPOINTS = ("Kd", "Ki")
TARGETS = (0.5, 1.0)
OPS = ("D", "P", "DP")
FUN = ("q", "h0", "h_aff", "h1")
REF = ("FIT_ROW_UNIFORM", "FIT_PROTEIN_INPUT_UNIFORM")
SEEDS = (17, 29, 43, 71, 101)
CASE = {f"formal_{seed:03d}_{arm}": (arm, seed) for arm in ("S", "G") for seed in SEEDS}
TOKEN = re.compile(r'\s*("(?:[^"\\]|\\.)*"|[-+0-9.eE]+|true|false|null|[{}\[\],:])')
SPACE = re.compile(r"\s*")
GIB = 2**30


class ContractError(RuntimeError):
    """A contract check failed."""


def require(condition, reason):
    if not condition:
        raise ContractError(reason)


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(data):
    return json.dumps(data, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def descriptor(path):
    raw = path.read_bytes()
    return {"path": str(path.relative_to(ROOT)), "bytes": len(raw), "sha256": digest(raw)}


def file_info(raw):
    return {"bytes": len(raw), "sha256": digest(raw)}


def guard(clock):
    require(time.time() < clock["wall_deadline"] and time.monotonic() < clock["monotonic_deadline"],
            "A1_HARD_TIME_LIMIT")


def resources(pids):
    rss = 0
    for pid in set(pids):
        try:
            rss += psutil.Process(pid).memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return {"rss_bytes": rss, "available_bytes": psutil.virtual_memory().available,
            "disk_free_bytes": psutil.disk_usage(str(ROOT)).free}


def check_resources(snapshot):
    require(snapshot["rss_bytes"] <= 36 * GIB, "A1_RSS_LIMIT")
    require(snapshot["available_bytes"] >= 4 * GIB, "A1_AVAILABLE_MEMORY_LIMIT")
    require(snapshot["disk_free_bytes"] >= 20 * GIB, "A1_DISK_FREE_LIMIT")


def write_exclusive(name, raw):
    with (OUTPUT / name).open("xb") as file:
        require(file.write(raw) == len(raw), "SHORT_OUTPUT_WRITE")
        file.flush()
        os.fsync(file.fileno())
    return file_info(raw)


def event(file, state, **details):
    raw = encoded({"at_utc": stamp(), "state": state, **details})
    require(file.write(raw) == len(raw), "SHORT_DISPATCH_WRITE")
    file.flush()
    os.fsync(file.fileno())


class Journal:
    def __init__(self, file, limits, clock):
        self.file = file
        self.limits = limits
        self.clock = clock
        self.reserved = collections.Counter()
        self.completed = collections.Counter()

    @contextlib.contextmanager
    def dispatch(self, name, **budget):
        guard(self.clock)
        require(all(self.reserved[k] + v <= self.limits[k] for k, v in budget.items()), "A1_DISPATCH_CAP")
        event(self.file, "RESERVED", phase=name, counts=budget)
        self.reserved.update(budget)
        yield
        event(self.file, "COMPLETED", phase=name, counts=budget)
        self.completed.update(budget)


def value_end(text, start):
    stack = []
    i = start
    while True:
        match = TOKEN.match(text, i)
        require(match is not None, "JSON_VALUE_TOKEN")
        item = match[1]
        i = match.end()
        if item in ("{", "["):
            stack.append("}" if item == "{" else "]")
        elif item in ("}", "]"):
            require(bool(stack) and stack.pop() == item, "JSON_CONTAINER")
            if not stack:
                return i
        elif not stack:
            require(item not in (":", ","), "JSON_EXPECTED_VALUE")
            return i


def members(text, start, mode):
    opening, closing = ("{", "}") if mode == "object" else ("[", "]")
    i = SPACE.match(text, start).end()
    require(text[i] == opening, "JSON_EXPECTED_CONTAINER")
    i = SPACE.match(text, i + 1).end()
    known = set()
    while text[i] != closing:
        key = None
        if mode == "object":
            key, i = json.JSONDecoder().raw_decode(text, i)
            require(isinstance(key, str) and key not in known, "JSON_OBJECT_KEY")
            known.add(key)
            i = SPACE.match(text, i).end()
            require(text[i] == ":", "JSON_EXPECTED_COLON")
            i = SPACE.match(text, i + 1).end()
        end = value_end(text, i)
        yield key, (i, end)
        i = SPACE.match(text, end).end()
        if text[i] == closing:
            return
        require(text[i] == ",", "JSON_EXPECTED_COMMA")
        i = SPACE.match(text, i + 1).end()
        require(text[i] != closing, "JSON_TRAILING_COMMA")


def array(text, span, size):
    items = [limits for _, limits in members(text, span[0], "array")]
    require(len(items) == size, "SAVED_ARRAY_SHAPE")
    return items


def field(text, fields, key):
    a, b = fields[key]
    return json.loads(text[a:b])


def native_float(text, span):
    a, b = span
    require(bool(re.fullmatch(r"[-+0-9.eE]+", text[a:b])), "NONNUMERIC_SAVED_RESPONSE")
    value = float(text[a:b])
    require(math.isfinite(value), "NONFINITE_SAVED_RESPONSE")
    return value


def take_panel(raw):
    text = raw.decode()
    top = dict(members(text, 0, "object"))
    require(field(text, top, "endpoints") == list(ENDPOINTS)
            and field(text, top, "operations") == list(OPS)
            and field(text, top, "functions") == list(FUN)
            and field(text, top, "references") == list(REF)
            and field(text, top, "spans") == [1/32, 1/16, 1/8, 1/4, 1/2, 1], "SEALED_PANEL_AXES")
    cases = field(text, top, "case_order")
    require(len(cases) == len(CASE) and set(cases) == set(CASE), "SEALED_CASE_ORDER")
    panel = {}
    for span in array(text, top["sources"], 128):
        record = dict(members(text, span[0], "object"))
        si = field(text, record, "source_index")
        ep = field(text, record, "endpoint")
        require(type(si) is int and 0 <= si < 128 and si not in panel and ep in ENDPOINTS, "SOURCE_INDEX_OR_ENDPOINT")
        weights = []
        for donor in array(text, record["links"], 2):
            link = dict(members(text, donor[0], "object"))
            weights.append(field(text, link, "reference_weights"))
        require(all(len(x) == 2 and x[0] == .5 and type(x[1]) is float
                    and math.isfinite(x[1]) and 0 < x[1] < 1 for x in weights)
                and math.isclose(weights[0][1] + weights[1][1], 1, rel_tol=0, abs_tol=2e-15), "DONOR_WEIGHT_CLOSURE")
        panel[si] = (ep, weights)
    require(collections.Counter(e for e, _ in panel.values()) == {"Kd": 64, "Ki": 64}, "PANEL_ENDPOINT_COUNTS")
    first = {ep: min(i for i, (e, _) in panel.items() if e == ep) for ep in ENDPOINTS}
    return panel, cases, first


def project_responses(line):
    """Project only the join key and the approved five A/r positions per path."""
    text = line.decode()
    fields = dict(members(text, 0, "object"))
    case, source, endpoint, pilot = [field(text, fields, key) for key in ("case_id", "source_index", "endpoint", "pilot")]
    for donor, donor_span in enumerate(array(text, fields["A_r"], 2)):
        for oi, op_span in enumerate(array(text, donor_span, 3)):
            wanted = {}
            for index, span in enumerate(array(text, op_span, 6)):
                for fi, fn_span in enumerate(array(text, span, 4)):
                    quantities = array(text, fn_span, 2)
                    if index in (3, 4, 5):
                        wanted[index, fi, 1] = native_float(text, quantities[1])
                    if index in (4, 5):
                        wanted[index, fi, 0] = native_float(text, quantities[0])
            yield case, source, endpoint, pilot, donor, OPS[oi], wanted


def within(actual, expected):
    return abs(actual - expected) <= 1e-12 + 1e-12 * max(abs(actual), abs(expected))


def reduction_key(data):
    return tuple(data[k] for k in ("arm", "seed_layer", "endpoint_layer", "reference_scheme", "operation",
                                  "lambda", "function_id"))


def read_old(raw):
    summary = {}
    for n, line in enumerate(raw.decode().splitlines(), 1):
        fields = dict(members(line, 0, "object"))
        if field(line, fields, "lambda") not in TARGETS:
            continue
        keys = ("arm", "seed_layer", "endpoint_layer", "reference_scheme", "operation", "lambda", "function_id",
                "E", "B", "Skill", "Kd_or_Ki_sources_per_case", "links_per_endpoint_per_case", "seed_count",
                "endpoint_count")
        row = {k: field(line, fields, k) for k in keys}
        key = reduction_key(row)
        require(key not in summary and all(math.isfinite(row[k]) for k in ("E", "B", "Skill")), "HISTORICAL_SEALED_SUMMARY")
        summary[key] = (n, row)
    require(len(summary) == 1728, "HISTORICAL_SUMMARY_ROW_COUNT")
    return summary


def read_input(name, desc, directory, journal):
    path = directory / name
    size = desc["bytes"]
    with journal.dispatch("READ_" + name, physical_input_bytes=size, logical_input_bytes=2*size):
        with path.open("rb") as file:
            before = os.fstat(file.fileno())
            require(before.st_size == size, "INPUT_DESCRIPTOR_SIZE")
            raw = file.read(size)
            after = os.fstat(file.fileno())
        require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "INPUT_CHANGED_DURING_READ")
        require(len(raw) == size and digest(raw) == desc["sha256"], "INPUT_IDENTITY")
    return raw


def consume(lines, panel, case_set, first, pilot, seen, slots):
    for line in lines:
        entries = project_responses(line)
        for i, (case, source, ep, old_pilot, donor, op, values) in enumerate(entries):
            require(case in case_set and source in panel and panel[source][0] == ep
                    and (source in first) == pilot and old_pilot is pilot, "CASE_SOURCE_JOIN")
            if i == 0:
                require((case, source) not in seen, "DUPLICATE_CASE_SOURCE")
                seen.add((case, source))
            for fi, fun in enumerate(FUN):
                if pilot:
                    require(values[4, fi, 0] == values[5, fi, 0] / 2, "PILOT_SOURCE_PROJECTION_IDENTITY")
                for s, target in enumerate(TARGETS, start=4):
                    r = values[s, fi, 1]
                    g = values[s, fi, 0]
                    m = 2 * values[s - 1, fi, 1]
                    require(math.isfinite(m), "NONFINITE_MIDPOINT_ESTIMATE")
                    errors = (abs(r), abs(r - g), abs(r - m))
                    require(all(math.isfinite(v) for v in errors), "NONFINITE_ERROR")
                    for pi, ref in enumerate(REF):
                        key = (case, ep, ref, op, target, fun)
                        weight = panel[source][1][donor][pi]
                        slots[key].append(tuple(weight * value / 64 for value in errors))


def reduce(slots, historical):
    primitive = {}
    for key, values in slots.items():
        require(len(values) == 128, "REFERENCE_LINK_COUNT")
        primitive[key] = tuple(math.fsum(column) for column in zip(*values, strict=True))
    summary, primary = [], []
    residuals = {"E_gradient": 0., "B": 0., "Skill_gradient": 0.}
    for key, (previous_line, old) in historical.items():
        arm, seed, endpoint, ref, op, target, fun = key
        members = [case for case, (a, s) in CASE.items() if a == arm and (s == seed or seed == "MEAN5")]
        eps = ENDPOINTS if endpoint == "KD_KI_EQUAL" else (endpoint,)
        require(old["Kd_or_Ki_sources_per_case"] == 64 and old["links_per_endpoint_per_case"] == 128
                and len(members) == old["seed_count"] and len(eps) == old["endpoint_count"], "SAVED_DENOMINATORS")
        numbers = [primitive[case, ep, ref, op, target, fun] for case in members for ep in eps]
        b, g, m = (statistics.fmean(column) for column in zip(*numbers, strict=True))
        comparable = {"E_gradient": (g, "E"), "B": (b, "B"), "Skill_gradient": (b-g, "Skill")}
        for field, (actual, old_field) in comparable.items():
            previous = old[old_field]
            require(within(actual, previous), f"HISTORICAL_CARRY_SLOT:{previous_line}:{field}")
            residuals[field] = max(residuals[field], abs(actual - previous))
        row = {k: value for k, value in old.items() if k not in ("E", "B", "Skill")}
        row.update(
            E_zero=b, E_gradient=g, E_midpoint=m, B=b, Skill_zero=0.,
            Skill_gradient=b-g, Skill_midpoint=b-m, E_gradient_minus_E_midpoint=g-m,
            saved_span_summary_line=previous_line,
        )
        for name in ("Skill_gradient", "Skill_midpoint", "E_gradient_minus_E_midpoint"):
            row[name + "_sign"] = "POSITIVE" if row[name] > 0 else "NEGATIVE" if row[name] < 0 else "ZERO"
        summary.append(row)
        if (arm, seed, ref, op, fun) == ("G", "MEAN5", REF[0], "D", "q"):
            primary.append({"summary_line": len(summary), **row})
    require(len(summary) == 1728 and len(primary) == 6, "A1_OUTPUT_ROW_COUNTS")
    return summary, primary, residuals


def work(plan, binding, file):
    clock = binding["clock"]
    journal = Journal(file, plan["A1"]["proposed_caps"], clock)
    require(os.getppid() == binding["supervisor_pid"], "SUPERVISOR_IDENTITY")
    release, original_binding = recovery_release(plan)
    require(descriptor(RECOVERY_AUTH) == binding["recovery_authority"]
            and release["user_authority_quote"] == binding["authority"]
            and clock == original_binding["clock"], "RECOVERY_AUTHORITY_OR_CLOCK_CHANGED")
    for name, path in (("executor", Path(__file__).resolve()), ("registry", REGISTRY), ("contract", ROOT / plan["contract"])):
        require(descriptor(path) == binding[name], "BOUND_CODE_OR_CONTRACT_CHANGED")
    controls = plan["reviewed_controls"]
    seal_path = ROOT / controls["path_run_seal"]["path"]
    seal_raw = seal_path.read_bytes()
    require(file_info(seal_raw) == {k: controls["path_run_seal"][k] for k in ("bytes", "sha256")}, "ORIGINAL_SEAL_BYTES")
    seal = json.loads(seal_raw)
    require(seal["status"] == "PRIVATE_FINITE_DEVELOPMENT_ACCEPTED", "ORIGINAL_ACCEPTANCE")
    approved = {name: plan["sealed_recheck_files"][name] for name in plan["A1"]["scientific_infile_allowlist"]}
    require(approved.keys() == {"panel_and_references.json", "source_results.jsonl", "span_summary.jsonl"}
            and all(seal["artifacts"][name] == rec for name, rec in approved.items()), "EXACT_SOURCE_ALLOWLIST")
    require(plan["A1"]["A_r_numeric_slots_zero_indexed"] == [[4, 0], [5, 0], [3, 1], [4, 1], [5, 1]]
            and plan["design"]["reference_order"] == list(REF), "REGISTERED_PROJECTIONS")
    sources, cases, first = take_panel(read_input(
        "panel_and_references.json", approved["panel_and_references.json"], seal_path.parent, journal))
    source_lines = read_input("source_results.jsonl", approved["source_results.jsonl"],
                              seal_path.parent, journal).splitlines()
    require(len(source_lines) == 1280, "SAVED_SOURCE_ROW_COUNT")
    historical = read_old(read_input("span_summary.jsonl", approved["span_summary.jsonl"], seal_path.parent, journal))
    seen = set()
    slots = collections.defaultdict(list)
    with journal.dispatch("PILOT", case_source_records=20, projected_numeric_scalars=2400,
                          comparison_slots=960, weighted_comparison_slots=1920):
        consume(source_lines[:20], sources, cases, set(first.values()), True, seen, slots)
        require(seen == {(case, i) for case in cases for i in first.values()}, "PILOT_SCOPE")
    pilot = {"status": "ENGINEERING_QUALIFIED", "source_index_by_endpoint": first, "case_source_records": 20,
             "linked_comparison_slots": 960, "projection_identity": "A_HALF_EQUALS_RN64(A_FULL/2)",
             "pilot_commits_reused": True, "outcome_dependent_gate": False}
    pilot_info = write_exclusive("pilot_receipt.json", encoded(pilot))
    print("A1 pilot qualified (20 case-source records)", flush=True)
    check_resources(resources([os.getpid(), os.getppid()]))
    with journal.dispatch("MAIN", case_source_records=1260, projected_numeric_scalars=151200,
                          comparison_slots=60480, weighted_comparison_slots=120960):
        consume(source_lines[20:], sources, cases, set(first.values()), False, seen, slots)
        require(len(seen) == len(sources)*len(cases) and sum(map(len, slots.values())) == 122880, "COMPLETE_SCOPE")
    with journal.dispatch("REDUCE_AND_CARRY", summary_rows=1728):
        summary, primary, residuals = reduce(slots, historical)
    guard(clock)
    required = {key: n for key, n in journal.limits.items() if key in {
        "physical_input_bytes", "logical_input_bytes", "case_source_records", "projected_numeric_scalars",
        "comparison_slots", "weighted_comparison_slots", "summary_rows",
    }}
    require(dict(journal.completed) == required and journal.completed == journal.reserved, "A1_BUDGET_CLOSURE")
    generated = {
        "pilot_receipt.json": pilot_info,
        "span_summary.jsonl": write_exclusive("span_summary.jsonl", b"".join(encoded(row) for row in summary)),
        "primary_results.json": write_exclusive("primary_results.json", encoded({
            "scope": "EXPOSED_DEVELOPMENT_PATHS", "sources": len(sources), "links": len(sources)*2,
            "unit": "pAffinity", "primary": [row for row in primary if row["endpoint_layer"] in ENDPOINTS],
            "equal_endpoint_context": [row for row in primary if row["endpoint_layer"] == "KD_KI_EQUAL"],
        })),
    }
    write_exclusive("execution_receipt.json", encoded({
        "status": "READY_FOR_PRIVATE_ACCEPTANCE", "approved_inputs_read_once": approved,
        "input_projection": plan["A1"]["A_r_numeric_slots_zero_indexed"],
        "target_midpoints": plan["A1"]["midpoint_for_target"],
        "historical_carry": {"original_file": str(seal_path.parent.relative_to(ROOT) / "span_summary.jsonl"),
                             "matched_rows": len(historical), "matched_fields": len(historical)*3,
                             "absolute_plus_relative_tolerance_each": 1e-12,
                             "max_abs_residual": residuals},
        "completed_counts": dict(journal.completed), "reserved_counts": dict(journal.reserved),
        "unknown": None, "artifacts": generated,
        "calls_zero": {k: 0 for k in ("new_native_head_forward", "input_AD", "checkpoint_decode",
                                     "parameter_decode", "calibration_refit", "encoder", "training",
                                     "label_truth_scoring", "new_FD", "population_inference")},
    }))
    guard(clock)


def worker(plan):
    try:
        binding = json.loads((OUTPUT / "binding.json").read_bytes())
        with (OUTPUT / "dispatch.jsonl").open("xb") as file:
            work(plan, binding, file)
    except Exception as error:
        reason = str(error) if isinstance(error, ContractError) else type(error).__name__
        site = traceback.extract_tb(error.__traceback__)[-1]
        write_exclusive("failure.json", encoded({"reason": reason, "at_utc": stamp(),
                                                  "error_function": site.name, "error_line": site.lineno}))
        print("A1 stopped:", reason, flush=True)
        return 1
    return 0


def acceptance(caps, clock, exit_value):
    hashes = {}
    summaries = {}
    pending = {}
    accounted = collections.Counter()
    read_bytes = 0
    check_resources(resources([os.getpid()]))
    for path in sorted(OUTPUT.iterdir()):
        guard(clock)
        require(path.is_file() and not path.is_symlink(), "OUTPUT_INVENTORY_TYPE")
        require(len(hashes) + 3 <= caps["outputs_files"], "OUTPUT_COUNT_CAP")
        size = path.stat().st_size
        require(read_bytes + size <= min(caps["acceptance_read_bytes"], caps["outputs_bytes"] - 65536),
                "OUTPUT_OR_ACCEPTANCE_CAP")
        raw = path.read_bytes()
        require(len(raw) == size, "OUTPUT_CHANGED_DURING_ACCEPTANCE")
        read_bytes += size
        hashes[path.name] = file_info(raw)
        if path.name == "dispatch.jsonl":
            for line in raw.splitlines():
                entry = json.loads(line)
                phase, counts = entry["phase"], entry["counts"]
                if entry["state"] == "RESERVED":
                    require(not pending, "DUPLICATE_RESERVATION")
                    pending[phase] = counts
                else:
                    require(entry["state"] == "COMPLETED" and pending.pop(phase) == counts, "UNEXPECTED_COMMIT")
                    accounted.update(counts)
        elif path.name in ("execution_receipt.json", "pilot_receipt.json", "failure.json"):
            summaries[path.name] = json.loads(raw)
        elif path.name == "span_summary.jsonl":
            summaries["span_rows"] = len(raw.splitlines())
    if exit_value == 0:
        executed = summaries["execution_receipt.json"]
        require(not pending and dict(accounted) == executed["completed_counts"] and
                executed["status"] == "READY_FOR_PRIVATE_ACCEPTANCE" and executed["unknown"] is None and
                summaries["span_rows"] == 1728 and summaries["pilot_receipt.json"]["status"] == "ENGINEERING_QUALIFIED",
                "CLOSING_LEDGER_OR_ROWS")
        require(all(hashes[k] == v for k, v in executed["artifacts"].items()), "ACCEPTANCE_OUTPUT_HASH")
    return hashes, dict(accounted), pending, read_bytes, summaries.get("failure.json")


def recovery_release(plan):
    require(RECOVERY_AUTH.is_file(), "EXPLICIT_A1_REREAD_AUTHORITY_REQUIRED")
    release = json.loads(RECOVERY_AUTH.read_bytes())
    original = json.loads((ORIGINAL / "binding.json").read_bytes())
    stopped = json.loads((ORIGINAL / "closing_receipt.json").read_bytes())
    require(release["status"] == "EXPLICITLY_AUTHORIZED"
            and bool(release["user_authority_quote"].strip())
            and release["original_failure_receipt"] == descriptor(ORIGINAL / "closing_receipt.json"),
            "UNBOUND_RECOVERY_AUTHORITY")
    approved = plan["A1"]["scientific_infile_allowlist"]
    require(release["authorized_additional_reads"] == {key: 1 for key in approved}
            and release["authorized_additional_physical_input_bytes"] == plan["A1"]["proposed_caps"]["physical_input_bytes"]
            and release["authorized_additional_logical_input_bytes"] == plan["A1"]["proposed_caps"]["logical_input_bytes"]
            and release["authorized_additional_pilot_reservation"] == stopped["unknown_dispatch"]["PILOT"]
            and stopped["status"] == "STOPPED_NOT_ACCEPTED", "UNBOUND_ADDITIONAL_REREAD_OR_PILOT_BUDGET")
    require(Path(original["executor"]["path"]).name == "run_manuscript_midpoint_query_a1_v0_1.py"
            and original["executor"] == descriptor(ROOT / original["executor"]["path"]), "ORIGINAL_EXECUTOR_IDENTITY")
    guard(original["clock"])
    return release, original


def supervise(plan):
    require(Path(os.path.abspath(sys.executable)) == RUNTIME and sys.flags.isolated and sys.flags.dont_write_bytecode,
            "USE_PROJECT_RUNTIME_ISOLATED")
    release, original = recovery_release(plan)
    wall, mono = time.time(), time.monotonic()
    caps = plan["A1"]["proposed_caps"]
    clock = original["clock"]
    check_resources(resources([os.getpid()]))
    OUTPUT.mkdir(parents=True, exist_ok=False)
    argv = [str(RUNTIME), "-I", "-B", str(Path(__file__).resolve()), "--worker"]
    binding = {"authority": release["user_authority_quote"], "A1": "SAVED_RESPONSE_RECOVERY_AUTHORIZED",
               "new_model_execution": False, "clock": clock, "at_utc": stamp(), "supervisor_pid": os.getpid(),
               "registry": descriptor(REGISTRY), "contract": descriptor(ROOT / plan["contract"]),
               "executor": descriptor(Path(__file__).resolve()), "argv": argv, "python_version": sys.version.split()[0],
               "device": "CPU", "resource_monitor_seconds": 5,
               "recovery_authority": descriptor(RECOVERY_AUTH),
               "original_failure_receipt": release["original_failure_receipt"]}
    guard(clock)
    write_exclusive("binding.json", encoded(binding))
    child = None
    stop = None
    samples = 0
    maxima = collections.Counter()
    minima = {}
    try:
        child = subprocess.Popen(argv, cwd=ROOT)
        with (OUTPUT / "resources.jsonl").open("xb") as file:
            while True:
                state = child.poll()
                pids = [os.getpid(), child.pid] if state is None else [os.getpid()]
                rr = resources(pids)
                info = None
                if state is None:
                    try:
                        p = psutil.Process(child.pid)
                        info = {"pid": child.pid, "command": p.cmdline(), "create_time": p.create_time(), "state": p.status()}
                        require(info["command"] == argv and info["state"] not in ("stopped", "tracing-stop"),
                                "WORKER_IDENTITY_OR_SUSPENSION")
                    except psutil.NoSuchProcess:
                        require(child.poll() is not None, "WORKER_DISAPPEARED")
                event(file, "RESOURCE", snapshot=rr, worker=info, observed_exit=state, mps_driver_bytes=0,
                      mps_basis="CPU-only saved arithmetic; no MPS driver initialized")
                samples += 1
                maxima["rss_bytes"] = max(maxima["rss_bytes"], rr["rss_bytes"])
                for k in ("available_bytes", "disk_free_bytes"):
                    minima[k] = min(minima.get(k, rr[k]), rr[k])
                check_resources(rr)
                guard(clock)
                if state is not None:
                    break
                try:
                    child.wait(timeout=min(5., clock["monotonic_deadline"] - time.monotonic(),
                                           clock["wall_deadline"] - time.time()))
                except subprocess.TimeoutExpired:
                    pass
    except BaseException as error:
        stop = str(error) if isinstance(error, ContractError) else type(error).__name__
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    exit_value = child.wait() if child is not None else None
    audit_error = None
    hashes, counts, pending, read_bytes, failure = {}, {}, None, 0, None
    try:
        hashes, counts, pending, read_bytes, failure = acceptance(caps, clock, exit_value)
        guard(clock)
    except Exception as error:
        audit_error = str(error) if isinstance(error, ContractError) else type(error).__name__
    accepted = exit_value == 0 and stop is None and audit_error is None
    closing = {"status": "PRIVATE_FINITE_DEVELOPMENT_ACCEPTED" if accepted else "STOPPED_NOT_ACCEPTED",
               "worker_exit_code": exit_value, "supervisor_stop": stop, "acceptance_error": audit_error, "failure": failure,
               "confirmed_completed_counts": counts, "unknown_dispatch": pending or None if audit_error is None else "UNKNOWN_AUDIT_NOT_CLOSED",
               "formal_wall_seconds": time.time() - clock["wall_started"],
               "formal_monotonic_seconds": time.monotonic() - clock["monotonic_started"],
               "recovery_process_wall_seconds": time.time() - wall, "recovery_process_monotonic_seconds": time.monotonic() - mono,
               "sampled_resource_extrema": {"max": dict(maxima), "min": minima}, "resource_samples": samples,
               "acceptance_bytes_read": read_bytes, "at_utc": stamp()}
    closing_info = write_exclusive("closing_receipt.json", encoded(closing))
    if accepted:
        guard(clock)
        write_exclusive("run_seal.json", encoded({"status": closing["status"],
                                                  "artifacts": {**hashes, "closing_receipt.json": closing_info}}))
        print("A1 private acceptance closed (1,728 summary rows)", flush=True)
        return 0
    print("A1 not accepted; see closing_receipt.json", flush=True)
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    plan = json.loads(REGISTRY.read_bytes())
    sys.exit(worker(plan) if args.worker else supervise(plan))
