"""Replay two fixed-query policies using only the sealed historical FR40 fields."""

import ast
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time

import psutil


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
AUTH = ROOT / "planning/manuscript_fr40_fixed_query_replay_authority_v0_1.json"
REGISTRY = ROOT / "planning/manuscript_midpoint_query_a2_execution_contract_v0_2.json"
BASE = ROOT / "output/manuscript_midpoint_query_v0_1/A2/fr40_v0_2"
OUT = BASE / "offline_replay/run_001"
MANIFEST = BASE / "01_candidate_metadata/candidate_manifest.private.json"
CASES = tuple(f"formal_{seed:03d}_G" for seed in (17, 29, 43, 71, 101))
ENDPOINTS = ("Kd", "Ki")
POLICIES = ("hash", "gradient")
LIMITS = (4, 8)
SHAPE = (322659, 4, 12)
GIB = 2**30
MIB = 2**20


class Stop(RuntimeError):
    """Stop at a recorded control or numerical failure."""


def require(ok, reason):
    if not ok:
        raise Stop(reason)


def stamp(raw):
    return {"bytes":len(raw), "sha256":hashlib.sha256(raw).hexdigest()}


def encode(data):
    return (json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()


def emit(name, data, append=False):
    raw = encode(data)
    with (OUT / name).open("ab" if append else "xb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    return stamp(raw)


def dispatch(event, **fields):
    emit("dispatch.jsonl", {"event": event, "time_ns": time.time_ns(), **fields}, append=True)


def remaining(binding):
    left = min(binding["wall_deadline"]-time.time(), binding["monotonic_deadline"]-time.monotonic())
    require(left > 0, "HARD_CLOCK")
    return left


def resources(binding):
    remaining(binding)
    me = psutil.Process(os.getpid())
    rss = 0
    for process in [me, *me.children(recursive=True)]:
        try:
            rss += process.memory_info().rss
        except psutil.NoSuchProcess:
            continue
    available = psutil.virtual_memory().available
    disk = psutil.disk_usage(str(ROOT)).free
    emit("resources.jsonl", {"time_ns":time.time_ns(),"rss_bytes":rss,"MPS_driver_bytes":0,
                            "available_bytes":available,"disk_free_bytes":disk,"scope":"CPU_ONLY"}, append=True)
    require(rss <= 36*GIB and available >= 4*GIB and disk >= 20*GIB, "HARD_RESOURCE")


def read_archive(source, binding):
    remaining(binding)
    dispatch("ARCHIVE_RESERVED", id=source["id"], physical_cap=source["bytes"], logical_cap=2*source["bytes"])
    physical = 0
    try:
        with Path(source["path"]).open("rb") as handle:
            before = os.fstat(handle.fileno())
            require(before.st_size == source["bytes"], "ARCHIVE_SIZE")
            raw = handle.read(source["bytes"])
            physical = len(raw)
            after = os.fstat(handle.fileno())
        require((before.st_size,before.st_mtime_ns,before.st_ctime_ns)
                == (after.st_size,after.st_mtime_ns,after.st_ctime_ns), "ARCHIVE_CHANGED")
        require(stamp(raw) == {k:source[k] for k in ("bytes","sha256")}, "ARCHIVE_SEAL")
        require(raw[:8] == b"\x93NUMPY\x01\x00", "ARCHIVE_NPY_VERSION")
        start = 10 + struct.unpack_from("<H",raw,8)[0]
        header = ast.literal_eval(raw[10:start].decode("ascii").strip())
        require(header == {"descr":"<f8","fortran_order":False,"shape":SHAPE}
                and len(raw) == start+math.prod(SHAPE)*8, "ARCHIVE_NPY_HEADER")
    except Exception as exc:
        dispatch("ARCHIVE_FAILED", id=source["id"], physical_bytes_observed=physical,
                 logical_completion="UNKNOWN", reason=reason(exc))
        raise
    dispatch("ARCHIVE_VERIFIED", id=source["id"], physical_bytes=physical, logical_bytes=2*physical,
             whole_reads=1, sha256=source["sha256"])
    return raw, start


def load_board(binding):
    raw = MANIFEST.read_bytes()
    require(stamp(raw) == binding["candidate_manifest"], "CANDIDATE_SEAL")
    panel = json.loads(raw)
    require((panel["function"],panel["operation"],panel["span"]) == ("q","D",1)
            and panel["GINE_seeds"] == [17,29,43,71,101], "CANDIDATE_PANEL_SCOPE")
    require(panel["rank_rule"]["hash_domain"] == "DTI-MANUSCRIPT-Q-QUERY-v0.1", "CANDIDATE_RANK_DOMAIN")
    board = {ep: [] for ep in ENDPOINTS}
    seen = set()
    for source in panel["sources"]:
        candidates = source["candidates"]
        rows = [c["operation_row"] for c in candidates]
        require(len(rows) == len({c["C_identity"] for c in candidates}) == len(set(rows)) == 32
                and all(type(i) is int and 0 <= i < SHAPE[0] for i in rows) and not (seen & set(rows)),
                "CANDIDATE_ROWS")
        seen.update(rows)
        board[source["endpoint"]].append(rows)
    require(all(len(by_ep)==16 for by_ep in board.values()), "BOARD_SIZE")
    return board


def priorities(policy, rows):
    result = {}
    for key, entry in rows.items():
        if policy == "hash":
            require(entry == {"count":32}, "HASH_INPUT_SCOPE")
            result[key] = list(range(1,9))
        else:
            require(list(entry)==["projection"] and len(entry["projection"])==32
                    and all(math.isfinite(x) for x in entry["projection"]), "GRADIENT_INPUT_SCOPE")
            values = entry["projection"]
            result[key] = sorted(range(1,33), key=lambda i:(-abs(values[i-1]),i))[:8]
    return result


def invoke(arguments, data, binding):
    response = subprocess.run([sys.executable,"-I","-B",str(SELF),*arguments],
                              input=encode(data),capture_output=True,timeout=remaining(binding),check=True)
    return json.loads(response.stdout)


def scalar(archive, row, field):
    raw, start = archive
    value = struct.unpack_from("<d",raw,start+(48*row+field)*8)[0]
    require(math.isfinite(value), "NONFINITE_HISTORY_FIELD")
    return value


def run_policies(groups, archives, target_cache, actions, binding):
    # Each ranking child receives only its own policy view. Targets are queried after the ranks return.
    dispatch("PROJECTIONS_RESERVED", groups=len(groups), scalars=32*len(groups))
    views = {
        "hash": {group:{"count":32} for group in groups},
        "gradient": {group:{"projection":[scalar(archives[group.split(":")[0]],row,0) for row in rows]}
                     for group,rows in groups.items()},
    }
    dispatch("PROJECTIONS_COMPLETE", groups=len(groups), scalars=32*len(groups))
    for policy in POLICIES:
        dispatch("POLICY_RESERVED", policy=policy, groups=len(groups),
                 visible_target_slots=len(groups)*sum(LIMITS))
        orders = invoke(["--rank",policy],views[policy],binding)
        require(list(orders)==list(groups), "POLICY_GROUP_ORDER")
        for group, order in orders.items():
            require(len(order)==len(set(order))==8 and all(type(i) is int and 1<=i<=32 for i in order),
                    "POLICY_QUERY_SCOPE")
            case = group.split(":")[0]
            selected = []
            for ordinal in order:
                slot = (group,ordinal)
                if slot not in target_cache:
                    target_cache[slot] = scalar(archives[case],groups[group][ordinal-1],3)
                selected.append({"candidate":ordinal,"response":target_cache[slot]})
            actions[policy][group] = {"query_8":selected,"query_4_prefix":4}
        dispatch("POLICY_COMPLETE", policy=policy, groups=len(groups),
                 visible_target_slots=len(groups)*sum(LIMITS), actual_eight_slot_responses=len(groups)*8)


def score_row(targets, recorded, cap):
    require(recorded["query_4_prefix"]==4 and len(recorded["query_8"])==8, "SUBMITTED_PREFIX")
    queried = recorded["query_8"]
    ordinals = [entry["candidate"] for entry in queried]
    require(len(set(ordinals))==8 and all(type(i) is int and 1<=i<=32 for i in ordinals)
            and all(targets[entry["candidate"]-1]==entry["response"] for entry in queried),
            "SUBMITTED_RESPONSE_DISAGREEMENT")
    scores = [abs(value) for value in targets]
    all_best = max(scores)
    selected_best = max(scores[o-1] for o in ordinals[:cap])
    all_ties = [i for i,v in enumerate(scores,1) if v==all_best]
    selected_ties = sorted(o for o in ordinals[:cap] if scores[o-1]==selected_best)
    return {"selection_loss":all_best-selected_best,"best_found":selected_best,"hit":selected_best==all_best,
            "best_ordinal":selected_ties[0],"verified_best_tie_count":len(selected_ties),
            "all_best_ties":all_ties,"verified_count":cap}


def sealed_actions():
    seal_path = OUT / "action_seal.json"
    require(seal_path.is_file(), "SCORING_REQUIRES_BOTH_ACTION_SEALS")
    seal = json.loads(seal_path.read_bytes())
    require(set(seal)==set(POLICIES), "ACTION_SEAL_SCOPE")
    result = {}
    for policy in POLICIES:
        data = (OUT / f"actions_{policy}.private.json").read_bytes()
        require(stamp(data)==seal[policy], "ACTION_SEAL_CHANGED")
        result[policy] = json.loads(data)
    return result


def summarize(rows):
    summary = {}
    for cap in LIMITS:
        by_endpoint = {}
        for ep in ENDPOINTS:
            by_case = {}
            for case in CASES:
                prefix = f"{case}:{ep}:"
                group = [row for row in rows if row["group"].startswith(prefix) and row["queries"]==cap]
                require(len(group)==16, "AGGREGATION_SOURCE_COUNT")
                by_case[case] = {
                    "hash_loss":math.fsum(row["hash"]["selection_loss"] for row in group)/16,
                    "gradient_loss":math.fsum(row["gradient"]["selection_loss"] for row in group)/16,
                    "paired_loss_improvement":math.fsum(row["paired_loss_improvement"] for row in group)/16,
                    "hash_hit_fraction":sum(row["hash"]["hit"] for row in group)/16,
                    "gradient_hit_fraction":sum(row["gradient"]["hit"] for row in group)/16,
                    "paired_wins":sum(row["paired_loss_improvement"]>0.0 for row in group),
                    "paired_ties":sum(row["paired_loss_improvement"]==0.0 for row in group),
                    "paired_losses":sum(row["paired_loss_improvement"]<0.0 for row in group),
                }
            means = {key: math.fsum(row[key] for row in by_case.values())/5
                     for key in ("hash_loss","gradient_loss","paired_loss_improvement",
                                 "hash_hit_fraction","gradient_hit_fraction")}
            counts = {key:sum(row[key] for row in by_case.values()) for key in ("paired_wins","paired_ties","paired_losses")}
            by_endpoint[ep] = {"n_case_sources":80, **means, **counts, "by_case":by_case}
        equally_weighted = {key:math.fsum(by_endpoint[ep][key] for ep in ENDPOINTS)/2
                            for key in ("hash_loss","gradient_loss","paired_loss_improvement",
                                        "hash_hit_fraction","gradient_hit_fraction")}
        summary[str(cap)] = {"by_endpoint":by_endpoint,"equal_endpoint_summary":equally_weighted}
    return summary


def score():
    actions = sealed_actions()
    packet = json.load(sys.stdin)
    binding = json.loads((OUT / "binding.json").read_bytes())
    require(os.getppid()==packet["broker_pid"]
            and psutil.Process(os.getppid()).ppid()==binding["supervisor_pid"], "SCORER_PARENT")
    require(stamp((OUT / "action_seal.json").read_bytes())==packet["action_seal"], "SCORER_SEAL_BINDING")
    targets = packet["targets"]
    require(len(targets)==160 and all(set(actions[p])==set(targets) for p in POLICIES), "SCORING_SCOPE")
    rows = []
    for group, values in targets.items():
        require(len(values)==32 and all(math.isfinite(v) for v in values), "SCORING_TARGETS")
        for cap in LIMITS:
            outcomes = {policy:score_row(values,actions[policy][group],cap) for policy in POLICIES}
            improvement = outcomes["hash"]["selection_loss"]-outcomes["gradient"]["selection_loss"]
            rows.append({"group":group,"queries":cap,**outcomes,"paired_loss_improvement":improvement})
    paired = emit("paired_results.private.json",rows)
    summary = emit("summary.json",summarize(rows))
    return {"scored_paired_rows":len(rows),"paired_results":paired,"summary":summary}


def worker():
    binding = json.loads((OUT / "binding.json").read_bytes())
    require(os.getppid()==binding["supervisor_pid"], "WORKER_PARENT")
    board = load_board(binding)
    archives = {}
    actions = {policy:{} for policy in POLICIES}
    targets = {}
    all_groups = {}
    for case, source in zip(CASES,binding["inputs"],strict=True):
        archives[case] = read_archive(source,binding)
        group = {f"{case}:{ep}:{i:02}": board[ep][i-1] for i in range(1,17) for ep in ENDPOINTS}
        all_groups.update(group)
        if case == CASES[0]:
            pilot = dict(list(group.items())[:2])
            run_policies(pilot,archives,targets,actions,binding)
            require(all(len(actions[p])==2 for p in POLICIES)
                    and 16<=len(targets)<=32, "PILOT_QUERY_ACCOUNTING")
            emit("pilot_receipt.json",{"status":"PILOT_ENGINEERING_ACCEPTED","case_sources":2,
                                       "physical_exact_targets":len(targets),"used_projections":64,
                                       "strategy_target_views":48,"included_in_formal":True})
            group = dict(list(group.items())[2:])
        run_policies(group,archives,targets,actions,binding)
    require(len(all_groups)==160 and all(len(a)==160 for a in actions.values()), "FULL_POLICY_COUNTS")
    seals = {policy:emit(f"actions_{policy}.private.json",actions[policy]) for policy in POLICIES}
    action_seal = emit("action_seal.json",seals)
    dispatch("BOTH_ACTIONS_SEALED", action_seal=action_seal)
    before = len(targets)
    dispatch("SCORING_RESERVED", source_case_groups=160, supplementary_target_scalars=5120-before)
    complete = {}
    for group, rows in all_groups.items():
        case = group.split(":")[0]
        values = []
        for i,row in enumerate(rows,1):
            entry = (group,i)
            values.append(targets[entry] if entry in targets else scalar(archives[case],row,3))
        complete[group] = values
    scoring = invoke(["--score"],{"targets":complete,"action_seal":action_seal,"broker_pid":os.getpid()},binding)
    dispatch("SCORING_COMPLETE",**scoring)
    emit("execution_receipt.json",{
        "case_sources":160,"archive_whole_reads":len(archives),"decoded_projections":5120,
        "target_scalars_before_action_seal":before,"target_scalars_after_action_seal":5120-before,
        "decoded_exact_targets":5120,"per_policy_actual_eight_slot_responses":1280,
        "per_policy_four_prefix_views":640,"total_policy_target_views_across_budgets":3840,
        "new_model_forward":0,"new_AD":0,"true_label_reads":0,"model_payload_reads":0,
        "scoring":scoring,"action_seal":action_seal})


def accept(binding, worker_exit):
    names = ("binding.json","resources.jsonl","dispatch.jsonl","pilot_receipt.json",
             "actions_hash.private.json","actions_gradient.private.json","action_seal.json",
             "paired_results.private.json","summary.json","execution_receipt.json","supervisor_receipt.json")
    blobs = {name:(OUT / name).read_bytes() for name in names}
    caps = binding["limits"]
    count_bytes = sum(map(len,blobs.values()))
    require(count_bytes<=caps["cumulative_acceptance_read_MiB"]*MIB
            and count_bytes<=caps["artifact_MiB"]*MIB and len(names)+3<=caps["artifacts"], "ACCEPTANCE_BUDGET")
    events = [json.loads(line) for line in blobs["dispatch.jsonl"].splitlines()]
    reads = [e for e in events if e["event"]=="ARCHIVE_VERIFIED"]
    event_names = [e["event"] for e in events]
    stats = json.loads(blobs["execution_receipt.json"])
    s = stats["scoring"]
    require(worker_exit==0 and [e["id"] for e in reads]==[item["id"] for item in binding["inputs"]]
            and sum(e["physical_bytes"] for e in reads)==caps["physical_input_bytes"]
            and sum(e["logical_bytes"] for e in reads)==caps["identity_plus_use_logical_bytes"], "ARCHIVE_ACCOUNTING")
    require(sum(e["scalars"] for e in events if e["event"]=="PROJECTIONS_COMPLETE")==5120
            and sum(e["visible_target_slots"] for e in events if e["event"]=="POLICY_COMPLETE")==3840
            and event_names.count("BOTH_ACTIONS_SEALED")==event_names.count("SCORING_COMPLETE")==1
            and event_names.index("BOTH_ACTIONS_SEALED")<event_names.index("SCORING_RESERVED")
            <event_names.index("SCORING_COMPLETE"), "VISIBILITY_ACCOUNTING")
    and_seal = json.loads(blobs["action_seal.json"])
    require(all(stamp(blobs[f"actions_{p}.private.json"])==and_seal[p] for p in POLICIES)
            and s["scored_paired_rows"]==320 and stamp(blobs["summary.json"])==s["summary"]
            and stamp(blobs["paired_results.private.json"])==s["paired_results"]
            and stats["target_scalars_before_action_seal"]+stats["target_scalars_after_action_seal"]==5120,
            "SCIENTIFIC_ARTIFACT_SEALS")
    remaining(binding)
    return {"status":"PRIVATE_FR40_OFFLINE_REPLAY_ACCEPTED","acceptance_read_bytes":count_bytes,
            "worker_exit_code":worker_exit,"artifacts":{name:stamp(data) for name,data in blobs.items()}}


def reason(exc):
    if isinstance(exc,Stop):
        return str(exc)
    if isinstance(exc,subprocess.CalledProcessError):
        return f"CHILD_EXIT_{exc.returncode}"
    return type(exc).__name__


def supervise(entered):
    grant_raw = AUTH.read_bytes()
    grant = json.loads(grant_raw)
    controls = {label: stamp((ROOT / item["path"]).read_bytes()) for label,item in grant["controls"].items()}
    require(all(controls[label]=={k:desc[k] for k in ("bytes","sha256")}
                for label,desc in grant["controls"].items()),"CONTROL_DIGEST")
    require(grant["controls"]["executor"]["path"]==str(SELF.relative_to(ROOT))
            and grant["status"]=="ONE_OFFLINE_REPLAY_RELEASED", "REPLAY_AUTHORITY")
    contract = json.loads(REGISTRY.read_bytes())["optional_offline_replay"]
    require(grant["inputs"]==contract["inputs"] and grant["limits"]==contract["limits"]
            and [v["id"] for v in grant["inputs"]]==[f"{case}.old_response" for case in CASES]
            and contract["allowed_response_fields_zero_based"]=={"q_function":0,"D_projection":0,"D_response":3}
            and contract["target_lookups_per_strategy_case_source"]==[4,8], "INPUT_PROTOCOL")
    caps = grant["limits"]
    binding = {"supervisor_pid":os.getpid(),"controls":controls,"authority":stamp(grant_raw),
               "inputs":grant["inputs"],"candidate_manifest":grant["candidate_manifest"],"limits":caps,
               "wall_deadline":entered[0]+caps["wall_seconds"],"monotonic_deadline":entered[1]+caps["monotonic_seconds"]}
    remaining(binding)
    OUT.mkdir(parents=True,exist_ok=False)
    emit("binding.json",binding)
    process = None
    failure = None
    try:
        resources(binding)
        process = subprocess.Popen([sys.executable,"-I","-B",str(SELF),"--worker"],
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        while True:
            resources(binding)
            try:
                require(process.wait(timeout=min(5,remaining(binding)))==0,"BROKER_FAILED")
                break
            except subprocess.TimeoutExpired:
                continue
        resources(binding)
    except Exception as exc:
        failure = reason(exc)
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        status = None if process is None else process.returncode
        emit("supervisor_receipt.json",{"worker_exit_code":status,"failure":failure,
                                       "wall_elapsed_seconds":time.time()-entered[0],
                                       "monotonic_elapsed_seconds":time.monotonic()-entered[1]})
    if failure is not None:
        return 1
    try:
        emit("run_acceptance.json",accept(binding,status))
    except Exception as exc:
        emit("acceptance_failure.json",{"failure":reason(exc)})
        return 1
    return 0


def self_test():
    values = [0.0]*32
    values[1] = -3.0
    values[5] = 3.0
    wanted = [2,6,1,3,4,5,7,8]
    require(priorities("gradient",{"demo":{"projection":values}})["demo"]==wanted,"TEST_TIES")
    require(priorities("hash",{"demo":{"count":32}})["demo"]==list(range(1,9)),"TEST_HASH")
    queries = {"query_4_prefix":4,"query_8":[{"candidate":i,"response":values[i-1]} for i in wanted]}
    scored = score_row(values,queries,4)
    require(scored["hit"] and scored["selection_loss"]==0 and scored["all_best_ties"]==[2,6]
            and scored["best_ordinal"]==2 and scored["verified_best_tie_count"]==2,"TEST_SCORE")
    alternate = {"query_4_prefix":4,"query_8":[{"candidate":i,"response":values[i-1]} for i in range(7,15)]}
    scored = score_row(values,alternate,8)
    require(not scored["hit"] and scored["selection_loss"]==3 and scored["best_ordinal"]==7,"TEST_LOSS")
    try:
        sealed_actions()
    except Stop as exc:
        require(str(exc)=="SCORING_REQUIRES_BOTH_ACTION_SEALS","TEST_SCORER_GATE")
    else:
        raise Stop("TEST_REQUIRES_PRISTINE_RUN_PATH")
    print("SYNTHETIC_SCOPE_AND_SCORER_GATE_OK")


if __name__=="__main__":
    args = sys.argv[1:]
    if args==["--self-test"]:
        self_test()
    elif args==["--worker"]:
        try:
            worker()
        except Exception as exc:
            emit("worker_failure.json",{"failure":reason(exc)})
            sys.exit(1)
    elif args in (["--rank","hash"],["--rank","gradient"]):
        print(encode(priorities(args[1],json.load(sys.stdin))).decode(),end="")
    elif args==["--score"]:
        print(encode(score()).decode(),end="")
    else:
        require(not args,"ENTRY_ARGS")
        sys.exit(supervise((time.time(),time.monotonic())))
