"""Compare sealed FR40 replay rankings with a geometry-only candidate ranking."""

import ast
from collections import Counter, defaultdict
import importlib.util
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


SELF = Path(__file__).resolve()
ROOT = SELF.parents[1]
PRIOR_CODE = SELF.with_name("run_manuscript_fr40_fixed_query_replay_v0_1.py")
spec = importlib.util.spec_from_file_location("fixed_query_replay", PRIOR_CODE)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)
stamp, encode, require, reason = replay.stamp, replay.encode, replay.require, replay.reason
CASES, ENDPOINTS, LIMITS = replay.CASES, replay.ENDPOINTS, replay.LIMITS
MIB, GIB = 2**20, 2**30
OUT = ROOT / "output/manuscript_midpoint_query_v0_1/FR40_geometry_control_v0_1/run_001"
AUTH = ROOT / "planning/manuscript_fr40_geometry_control_authority_v0_1.json"
PLAN = ROOT / "docs/FR40几何排序对照追加实验方案_v0.1.md"
REG = ROOT / "planning/manuscript_path_recheck_v0_1.json"
PRIOR_OUT = replay.OUT
MANIFEST = replay.MANIFEST
CANDIDATE_ACCEPTANCE = MANIFEST.with_name("candidate_acceptance.json")
VECTOR = struct.Struct("<256f")
INDEX = struct.Struct("<14q")
POLICIES = ("hash", "gradient", "geometry")
FULL_GROUPS = [f"{case}:{ep}:{i:02}" for case in CASES for i in range(1, 17) for ep in ENDPOINTS]


def emit(name, data, append=False):
    raw = encode(data)
    with (OUT / name).open("ab" if append else "xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return stamp(raw)


def dispatch(event, **fields):
    emit("dispatch.jsonl", {"event":event,"time_ns":time.time_ns(),**fields}, append=True)


def remaining(binding):
    left = min(binding["wall_deadline"]-time.time(), binding["monotonic_deadline"]-time.monotonic())
    require(left>0, "HARD_CLOCK")
    return left


def resources(binding):
    remaining(binding)
    owner = psutil.Process(os.getpid())
    rss = 0
    for process in [owner,*owner.children(recursive=True)]:
        try:
            rss += process.memory_info().rss
        except psutil.NoSuchProcess:
            continue
    available = psutil.virtual_memory().available
    disk = psutil.disk_usage(str(ROOT)).free
    emit("resources.jsonl", {"time_ns":time.time_ns(),"rss_bytes":rss,"available_bytes":available,
                            "disk_free_bytes":disk,"MPS_driver_bytes":0,"scope":"CPU_ONLY"}, append=True)
    require(rss<=36*GIB and available>=4*GIB and disk>=20*GIB, "HARD_RESOURCE")


def read_input(binding, name, shape, dtype, item_size):
    remaining(binding)
    item = binding["inputs"][name]
    dispatch("INPUT_RESERVED", id=name, physical_cap=item["bytes"], logical_cap=2*item["bytes"])
    actual = 0
    try:
        with Path(item["path"]).open("rb") as handle:
            start_stat = os.fstat(handle.fileno())
            require(start_stat.st_size==item["bytes"], "INPUT_BYTES")
            raw = handle.read(item["bytes"])
            actual = len(raw)
            end_stat = os.fstat(handle.fileno())
        require((start_stat.st_size,start_stat.st_mtime_ns,start_stat.st_ctime_ns)
                ==(end_stat.st_size,end_stat.st_mtime_ns,end_stat.st_ctime_ns), "INPUT_CHANGED")
        require(stamp(raw)=={key:item[key] for key in ("bytes","sha256")}, "INPUT_SEAL")
        require(raw[:8]==b"\x93NUMPY\x01\x00", "NPY_VERSION")
        offset = 10+struct.unpack_from("<H",raw,8)[0]
        header = ast.literal_eval(raw[10:offset].decode("ascii").strip())
        require(header=={"descr":dtype,"fortran_order":False,"shape":shape}
                and len(raw)==offset+math.prod(shape)*item_size, "NPY_HEADER")
    except Exception as exc:
        dispatch("INPUT_FAILED",id=name,physical_observed=actual,completion="UNKNOWN",failure=reason(exc))
        raise
    dispatch("INPUT_VERIFIED",id=name,physical_bytes=actual,logical_bytes=2*actual,whole_reads=1)
    return raw, offset


def join_panel(binding):
    manifest_raw = MANIFEST.read_bytes()
    require(stamp(manifest_raw)==binding["candidate_manifest"], "MANIFEST_SEAL")
    panel = json.loads(manifest_raw)
    require((panel["operation"],panel["span"],panel["function"])==("D",1,"q")
            and panel["GINE_seeds"]==[17,29,43,71,101]
            and panel["rank_rule"]["hash_domain"]=="DTI-MANUSCRIPT-Q-QUERY-v0.1", "MANIFEST_SCOPE")
    index, start = read_input(binding,"indices",(322659,14),"<i8",8)
    counts = Counter()
    rows_seen = set()
    joined = {}
    for source in panel["sources"]:
        ep = source["endpoint"]
        require(ep in ENDPOINTS, "ENDPOINT")
        counts[ep] += 1
        key = (ep,counts[ep])
        candidates = source["candidates"]
        require(len(candidates)==len({c["C_identity"] for c in candidates})==32, "DISTINCT_C")
        links = []
        source_cache = set()
        for candidate in candidates:
            row = candidate["operation_row"]
            require(type(row) is int and 0<=row<322659 and row not in rows_seen,"OPERATION_ROW")
            rows_seen.add(row)
            data = INDEX.unpack_from(index,start+INDEX.size*row)
            require(data[:3]==(source["source_id"],candidate["FIT_donor_row"],ENDPOINTS.index(ep))
                    and data[5]==candidate["C_identity"] and data[9]==candidate["C_cache_row"]
                    and len(set(data[3:7]))==4 and data[13]==1, "MANIFEST_INDEX_JOIN")
            require(51265<=data[7]<56962 and (0<=data[9]<46140 or 51265<=data[9]<56962),
                    "CACHE_ROLE")
            source_cache.add((data[3],data[7]))
            links.append((row,data[7],data[9]))
        require(len(source_cache)==1, "SOURCE_CORNER_IDENTITY")
        joined[key] = links
    require(counts=={"Kd":16,"Ki":16} and len(rows_seen)==1024, "CANDIDATE_COVERAGE")
    dispatch("INDEX_JOIN_COMPLETE",operation_rows=len(rows_seen),sources=32)
    return joined


def distance(a,b):
    return math.sqrt(math.fsum((x-y)**2 for x,y in zip(a,b,strict=True)))


def project_geometry(case, groups, cache, coordinates):
    raw, start = cache
    rows = {cache_row for links in groups.values() for _,a,c in links for cache_row in (a,c)}
    require(len(rows | coordinates.keys())<=1056, "CACHE_ROW_CAP")
    added = rows-coordinates.keys()
    dispatch("GEOMETRY_PROJECTION_RESERVED",case=case,drug_rows=len(added),distances=32*len(groups))
    for row in sorted(added):
        v = VECTOR.unpack_from(raw,start+4096*row)
        require(all(math.isfinite(x) for x in v), "NONFINITE_DRUG_COORDINATE")
        coordinates[row] = v
    lengths = {name:[distance(coordinates[a],coordinates[c]) for _,a,c in links]
               for name,links in groups.items()}
    require(all(math.isfinite(v) for sizes in lengths.values() for v in sizes), "NONFINITE_DISTANCE")
    dispatch("GEOMETRY_PROJECTION_COMPLETE",case=case,drug_rows=len(added),drug_coordinates=256*len(added),
             distances=32*len(groups))
    return lengths


def rank_geometry(groups):
    result = {}
    for name, values in groups.items():
        require(len(values)==32 and all(math.isfinite(x) and x>=0 for x in values), "GEOMETRY_ONLY_VIEW")
        result[name] = sorted(range(1,33),key=lambda i:(-values[i-1],i))[:8]
    return result


def invoke(mode, packet, binding):
    response = subprocess.run([sys.executable,"-I","-B",str(SELF),mode],input=encode(packet),
                              capture_output=True,timeout=remaining(binding),check=True)
    return json.loads(response.stdout)


def lock_orders(subset, binding):
    orders = invoke("--rank-geometry",subset,binding)
    require(list(orders)==list(subset) and all(len(xs)==len(set(xs))==8
            and all(type(i) is int and 1<=i<=32 for i in xs) for xs in orders.values()), "GEOMETRY_ORDER")
    dispatch("GEOMETRY_ORDERS_FIXED",groups=len(orders),commit=stamp(encode(orders)))
    return orders


def query_geometry(orders, linked, archive):
    dispatch("GEOMETRY_REQUEST_RESERVED",groups=len(orders),exact_targets=8*len(orders))
    out = {name:{"query_8":[{"candidate":i,"response":replay.scalar(archive,linked[name][i-1][0],3)}
                             for i in order],"query_4_prefix":4}
           for name,order in orders.items()}
    dispatch("GEOMETRY_REQUEST_COMPLETE",groups=len(out),exact_targets=8*len(out),logical_views=12*len(out))
    return out


def uniform_reference(responses,k):
    ordered = sorted(map(abs,responses))
    n = len(ordered)
    total = math.comb(n,k)
    largest = ordered[-1]
    ties = ordered.count(largest)
    expected_loss = math.fsum((largest-y)*math.comb(j-1,k-1)/total for j,y in enumerate(ordered,1) if j>=k)
    hit_probability = (total-math.comb(n-ties,k))/total
    return {"selection_loss":expected_loss,"hit":hit_probability}


def avg(values):
    values = list(values)
    return math.fsum(values)/len(values)


def group_summaries(rows, query_count):
    fields = ("hash","gradient","geometry","uniform")
    output = {}
    for ep in ENDPOINTS:
        by_case = {}
        for case in CASES:
            sample = [row for row in rows if row["group"].startswith(f"{case}:{ep}:")
                      and row["queries"]==query_count]
            require(len(sample)==16, "CASE_SOURCE_DENOMINATOR")
            by_case[case] = {
                **{p+"_loss":avg(row[p]["selection_loss"] for row in sample) for p in fields},
                **{p+"_hit_fraction":avg(row[p]["hit"] for row in sample) for p in fields},
                "geometry_minus_gradient_loss":avg(row["geometry_minus_gradient_loss"] for row in sample),
                "gradient_wins":sum(row["geometry_minus_gradient_loss"]>0 for row in sample),
                "gradient_ties":sum(row["geometry_minus_gradient_loss"]==0 for row in sample),
                "gradient_losses":sum(row["geometry_minus_gradient_loss"]<0 for row in sample),
                "gradient_wins_with_negative_skill":sum(row["geometry_minus_gradient_loss"]>0
                                                         and row["same_panel_skill"]<0 for row in sample),
            }
        metrics = [p+s for p in fields for s in ("_loss","_hit_fraction")]+["geometry_minus_gradient_loss"]
        tally = ("gradient_wins","gradient_ties","gradient_losses","gradient_wins_with_negative_skill")
        output[ep] = {"n_case_sources":80,
                      **{key:avg(g[key] for g in by_case.values()) for key in metrics},
                      **{key:sum(g[key] for g in by_case.values()) for key in tally},"by_case":by_case}
    return {"by_endpoint":output,
            "equal_endpoint_summary":{key:avg(output[ep][key] for ep in ENDPOINTS) for key in metrics}}


def approximation_summary(diagnostics):
    out = {}
    for ep in ENDPOINTS:
        by_case = {}
        for case in CASES:
            groups = [val for name,val in diagnostics.items() if name.startswith(f"{case}:{ep}:")]
            require(len(groups)==16, "APPROXIMATION_DENOMINATOR")
            by_case[case] = {k:avg(row[k] for row in groups) for k in ("E","B","Skill")}
            by_case[case]["negative_skill_sources"] = sum(row["Skill"]<0 for row in groups)
        out[ep] = {"n_case_sources":80,
                   **{k:avg(v[k] for v in by_case.values()) for k in ("E","B","Skill")},
                   "negative_skill_case_sources":sum(v["negative_skill_sources"] for v in by_case.values()),
                   "by_case":by_case}
    return out


def geometry_submission():
    path = OUT / "geometry_action_seal.json"
    require(path.is_file(), "SCORING_REQUIRES_GEOMETRY_SEAL")
    seal_raw = path.read_bytes()
    raw = (OUT / "geometry_actions.private.json").read_bytes()
    require(stamp(raw)==json.loads(seal_raw)["geometry_actions"], "GEOMETRY_SUBMISSION_SEAL")
    return json.loads(raw), stamp(seal_raw)


def read_old_submissions(binding):
    checked = {}
    for name,item in binding["prior_artifacts"].items():
        raw = (PRIOR_OUT/name).read_bytes()
        require(stamp(raw)==item, "PRIOR_REPLAY_SEAL")
        checked[name] = json.loads(raw)
    action_seal = checked["action_seal.json"]
    require(all(action_seal[p]==binding["prior_artifacts"][f"actions_{p}.private.json"]
                for p in replay.POLICIES), "PRIOR_ACTION_SEAL")
    return {p:checked[f"actions_{p}.private.json"] for p in replay.POLICIES}, checked["summary.json"]


def scorer():
    geometry, seal = geometry_submission()
    packet = json.load(sys.stdin)
    binding = json.loads((OUT/"binding.json").read_bytes())
    require(seal==packet["geometry_seal"] and os.getppid()==packet["broker_pid"]
            and psutil.Process(os.getppid()).ppid()==binding["supervisor_pid"], "SCORER_BINDING")
    prior, expected_summary = read_old_submissions(binding)
    actions = {**prior,"geometry":geometry}
    targets = packet["targets"]
    require(list(targets)==FULL_GROUPS and all(list(v)==FULL_GROUPS for v in actions.values()), "GROUP_ALIGNMENT")
    rows = []
    diagnostics = {}
    prior_rows = []
    for group,data in targets.items():
        responses, projections = data["responses"], data["projections"]
        require(len(responses)==len(projections)==32 and all(math.isfinite(v) for v in responses+projections),
                "HISTORICAL_SCORER_FIELDS")
        error = avg(abs(r-a) for r,a in zip(responses,projections,strict=True))
        zero = avg(abs(r) for r in responses)
        diagnostics[group] = {"E":error,"B":zero,"Skill":zero-error}
        for k in LIMITS:
            outcome = {p:replay.score_row(responses,actions[p][group],k) for p in POLICIES}
            delta = outcome["geometry"]["selection_loss"]-outcome["gradient"]["selection_loss"]
            rows.append({"group":group,"queries":k,**outcome,"uniform":uniform_reference(responses,k),
                         "geometry_minus_gradient_loss":delta,"same_panel_skill":zero-error})
            prior_rows.append({"group":group,"queries":k,**{p:outcome[p] for p in replay.POLICIES},
                               "paired_loss_improvement":outcome["hash"]["selection_loss"]
                               -outcome["gradient"]["selection_loss"]})
    require(replay.summarize(prior_rows)==expected_summary, "PRIOR_REPLAY_EXACT_CARRY")
    paired = emit("paired_results.private.json",{"fixed_query_rows":rows,"same_panel_by_group":diagnostics})
    summary = emit("summary.json",{
        "fixed_query":{str(k):group_summaries(rows,k) for k in LIMITS},
        "same_panel_approximation":approximation_summary(diagnostics),
        "prior_hash_and_gradient_summary_identical":True,
    })
    return {"scored_rows":len(rows),"scored_unique_case_sources":len(diagnostics),"paired":paired,"summary":summary}


def worker():
    binding = json.loads((OUT/"binding.json").read_bytes())
    require(os.getppid()==binding["supervisor_pid"],"WORKER_PARENT")
    joined = join_panel(binding)
    archives = {}
    geometry = {}
    links = {}
    for case in CASES:
        groups = {f"{case}:{ep}:{i:02}":joined[(ep,i)] for i in range(1,17) for ep in ENDPOINTS}
        links.update(groups)
        cache = read_input(binding,f"{case}.cache",(56962,1024),"<f4",4)
        coordinates = {}
        if case==CASES[0]:
            pilot_groups = dict(list(groups.items())[:2])
            pilot_orders = lock_orders(project_geometry(case,pilot_groups,cache,coordinates),binding)
        else:
            orders = lock_orders(project_geometry(case,groups,cache,coordinates),binding)
        archive = read_input(binding,f"{case}.old_response",replay.SHAPE,"<f8",8)
        archives[case] = archive
        if case==CASES[0]:
            pilot = query_geometry(pilot_orders,groups,archive)
            require(len(pilot)==2 and all(len(x["query_8"])==8 and x["query_4_prefix"]==4
                                         for x in pilot.values()),"PILOT_ACCOUNT")
            geometry.update(pilot)
            emit("pilot_receipt.json",{"status":"PILOT_ENGINEERING_ACCEPTED","case":case,
                                       "case_sources":2,"distances":64,"exact_targets":16,
                                       "logical_views":24,"included_in_formal":True})
            rest = dict(list(groups.items())[2:])
            orders = lock_orders(project_geometry(case,rest,cache,coordinates),binding)
        del cache, coordinates
        geometry.update(query_geometry(orders,groups,archive))
    require(list(geometry)==list(links)==FULL_GROUPS, "GEOMETRY_COMPLETE")
    action = emit("geometry_actions.private.json",geometry)
    seal = emit("geometry_action_seal.json",{"geometry_actions":action})
    dispatch("GEOMETRY_SUBMISSION_SEALED",seal=seal)
    dispatch("UNIFIED_SCORE_RESERVED",extra_targets=3840,historical_projections=5120)
    payload = {}
    for group,rows in links.items():
        archive = archives[group.split(":")[0]]
        already = {x["candidate"]:x["response"] for x in geometry[group]["query_8"]}
        payload[group] = {
            "responses":[already[i] if i in already else replay.scalar(archive,row,3)
                         for i,(row,_,_) in enumerate(rows,1)],
            "projections":[replay.scalar(archive,row,0) for row,_,_ in rows],
        }
    result = invoke("--score",{"targets":payload,"geometry_seal":seal,"broker_pid":os.getpid()},binding)
    dispatch("UNIFIED_SCORE_COMPLETE",**result)
    emit("execution_receipt.json",{
        "geometry_case_sources":160,"distances":5120,"geometry_exact_target_requests":1280,
        "geometry_logical_views":1920,"historical_hash_gradient_logical_views":3840,
        "geometry_submitted_values_reused_for_score":1280,"scored_extra_targets":3840,
        "decoded_historical_targets":5120,"decoded_historical_projections":5120,
        "new_model_payload_reads":0,"new_forward":0,"new_AD":0,"truth_reads":0,
        "unified_scoring_count":1,"scoring":result})


def accept(binding, code):
    names = ("binding.json","dispatch.jsonl","resources.jsonl","pilot_receipt.json","geometry_actions.private.json",
             "geometry_action_seal.json","paired_results.private.json","summary.json","execution_receipt.json",
             "supervisor_receipt.json")
    raw = {name:(OUT/name).read_bytes() for name in names}
    read_bytes = sum(map(len,raw.values()))
    caps = binding["limits"]
    require(read_bytes<=caps["cumulative_acceptance_read_MiB"]*MIB
            and read_bytes<=caps["artifact_MiB"]*MIB and len(names)+3<=caps["artifacts"], "ACCEPTANCE_CAP")
    events = list(map(json.loads,raw["dispatch.jsonl"].splitlines()))
    reads = [x for x in events if x["event"]=="INPUT_VERIFIED"]
    kinds = [x["event"] for x in events]
    receipt = json.loads(raw["execution_receipt.json"])
    scores = receipt["scoring"]
    geometry = [x for x in events if x["event"]=="GEOMETRY_PROJECTION_COMPLETE"]
    require(code==0 and len(reads)==11 and {x["id"] for x in reads}==set(binding["inputs"])
            and sum(x["physical_bytes"] for x in reads)==caps["physical_input_bytes"]
            and sum(x["logical_bytes"] for x in reads)==caps["identity_plus_use_logical_bytes"], "INPUT_BUDGET")
    require(sum(x["drug_rows"] for x in geometry)<=5280 and sum(x["drug_coordinates"] for x in geometry)<=1351680
            and sum(x["distances"] for x in geometry)==5120, "GEOMETRY_BUDGET")
    require(sum(x["exact_targets"] for x in events if x["event"]=="GEOMETRY_REQUEST_COMPLETE")==1280
            and kinds.count("UNIFIED_SCORE_COMPLETE")==kinds.count("GEOMETRY_SUBMISSION_SEALED")==1
            and kinds.index("GEOMETRY_SUBMISSION_SEALED")<kinds.index("UNIFIED_SCORE_RESERVED")
            <kinds.index("UNIFIED_SCORE_COMPLETE"), "SCORING_ORDER")
    require(scores["scored_rows"]==320 and scores["scored_unique_case_sources"]==160
            and stamp(raw["summary.json"])==scores["summary"] and stamp(raw["paired_results.private.json"])==scores["paired"]
            and stamp(raw["geometry_actions.private.json"])==json.loads(raw["geometry_action_seal.json"])["geometry_actions"],
            "RESULT_SEALS")
    remaining(binding)
    return {"status":"PRIVATE_FR40_GEOMETRY_CONTROL_ACCEPTED","acceptance_read_bytes":read_bytes,
            "verified_registered_inputs":len(reads),"score_rows":scores["scored_rows"],
            "physical_input_bytes":caps["physical_input_bytes"],"logical_input_bytes":caps["identity_plus_use_logical_bytes"],
            "projected_drug_rows":sum(x["drug_rows"] for x in geometry),
            "projected_drug_coordinates":sum(x["drug_coordinates"] for x in geometry),
            "artifacts":{name:stamp(data) for name,data in raw.items()}}


def supervise(entered):
    grant_raw = AUTH.read_bytes()
    grant = json.loads(grant_raw)
    controls = {"executor":SELF,"prior_replay_executor":PRIOR_CODE,"plan":PLAN,"registry":REG,
                "candidate_acceptance":CANDIDATE_ACCEPTANCE,"prior_closeout":PRIOR_OUT/"closeout.json"}
    control = {name:path.read_bytes() for name,path in controls.items()}
    identity = {k:stamp(v) for k,v in control.items()}
    require(identity==grant["controls"] and grant["status"]=="ONE_FR40_GEOMETRY_CONTROL_RELEASED","AUTHORITY_BINDING")
    historical = {x["id"]:x for x in json.loads(control["registry"])["future_input_descriptors_reference_only"]}
    names = ["indices"]+[f"{case}.{kind}" for case in CASES for kind in ("cache","old_response")]
    inputs = {name:historical[name] for name in names}
    require(inputs==grant["inputs"] and sum(x["bytes"] for x in inputs.values())==1822226256,"INPUT_DESCRIPTORS")
    prior = json.loads(control["prior_closeout"])
    expected = ("action_seal.json","summary.json","actions_hash.private.json","actions_gradient.private.json")
    require(prior["status"]=="PRIVATE_FR40_OFFLINE_REPLAY_ACCEPTED"
            and grant["prior_artifacts"]=={k:prior["artifacts"][k] for k in expected},"PRIOR_CLOSEOUT")
    candidate = json.loads(control["candidate_acceptance"])
    require(candidate["candidate_status"]=="FR40_CANDIDATE_QUALIFIED"
            and grant["candidate_manifest"]==candidate["artifacts"]["candidate_manifest.private.json"],
            "CANDIDATE_ACCEPTANCE")
    caps = grant["limits"]
    require(caps=={"physical_input_bytes":1822226256,"identity_plus_use_logical_bytes":3644452512,
                   "wall_seconds":1800,"monotonic_seconds":1800,"artifacts":16,"artifact_MiB":16,
                   "cumulative_acceptance_read_MiB":16},"EXECUTION_LIMITS")
    binding = {"supervisor_pid":os.getpid(),"authority":stamp(grant_raw),"controls":identity,
               "prior_artifacts":grant["prior_artifacts"],"candidate_manifest":grant["candidate_manifest"],
               "limits":caps,"inputs":inputs,"wall_deadline":entered[0]+1800,"monotonic_deadline":entered[1]+1800}
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
                pass
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
    import itertools
    n = [0.0]*32
    n[1] = n[5] = distance((0.0,0.0),(3.0,4.0))
    require(rank_geometry({"t":n})["t"]==[2,6,1,3,4,5,7,8] and distance((0.0,),(0.0,))==0.0,"TEST_GEOMETRY")
    y = [0.0,-0.5,-1.5,1.5,0.8,-0.3]
    for k in (2,4):
        maxima = [max(map(abs,z)) for z in itertools.combinations(y,k)]
        calc = uniform_reference(y,k)
        require(math.isclose(calc["selection_loss"],avg(max(map(abs,y))-v for v in maxima))
                and math.isclose(calc["hit"],avg(v==max(map(abs,y)) for v in maxima)), "TEST_UNIFORM_TIED")
    try:
        geometry_submission()
    except replay.Stop as exc:
        require(str(exc)=="SCORING_REQUIRES_GEOMETRY_SEAL","TEST_SCORER_GATE")
    else:
        raise replay.Stop("TEST_REQUIRES_PRISTINE_RUN_DIRECTORY")
    print("SYNTHETIC_GEOMETRY_UNIFORM_AND_SCORER_GATE_OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args==["--self-test"]:
        self_test()
    elif args==["--worker"]:
        try:
            worker()
        except Exception as exc:
            emit("worker_failure.json",{"failure":reason(exc)})
            sys.exit(1)
    elif args==["--rank-geometry"]:
        print(encode(rank_geometry(json.load(sys.stdin))).decode(),end="")
    elif args==["--score"]:
        print(encode(scorer()).decode(),end="")
    else:
        require(not args,"ENTRY_ARGS")
        sys.exit(supervise((time.time(),time.monotonic())))
