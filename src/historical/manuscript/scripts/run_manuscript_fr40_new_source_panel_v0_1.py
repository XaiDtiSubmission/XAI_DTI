"""Run the three fixed-query FR40 rankings on the sealed new source panel."""

from collections import Counter
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import psutil


SELF = Path(__file__).resolve()
ROOT = SELF.parents[1]
GEO_CODE = SELF.with_name("run_manuscript_fr40_geometry_control_v0_1.py")
spec = importlib.util.spec_from_file_location("fr40_geometry_control", GEO_CODE)
geo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geo)
require, stamp, encode = geo.require, geo.stamp, geo.encode
CASES, ENDPOINTS, LIMITS, POLICIES = geo.CASES, geo.ENDPOINTS, geo.LIMITS, geo.POLICIES
BASE = ROOT / "output/manuscript_midpoint_query_v0_1/FR40_new_source_panel_v0_1"
PREP = BASE / "preparation_v0_1"
OUT = BASE / "run_001"
geo.OUT = OUT
AUTH = ROOT / "planning/manuscript_fr40_new_source_panel_execution_v0_1.json"
PREP_REG = ROOT / "planning/manuscript_fr40_new_source_panel_preparation_v0_1.json"
PLAN = ROOT / "docs/FR40新源面板复核实验方案_v0.1.md"
OLD_RESULT = ROOT / "output/manuscript_midpoint_query_v0_1/FR40_geometry_control_v0_1/run_001/closeout.json"


def invoke(arguments, packet, binding):
    response = subprocess.run([sys.executable,"-I","-B",str(SELF),*arguments],input=encode(packet),
                              capture_output=True,timeout=geo.remaining(binding),check=True)
    return json.loads(response.stdout)


def board(binding):
    raw = (PREP/"candidate_manifest.private.json").read_bytes()
    require(stamp(raw)==binding["candidate_manifest"], "NEW_PANEL_SEAL")
    panel = json.loads(raw)
    require((panel["function"],panel["operation"],panel["span"])==("q","D",1)
            and panel["GINE_seeds"]==[17,29,43,71,101] and not any(panel["selected_source_pair_group_overlap"].values())
            and panel["rank_rule"]["hash_domain"]=="DTI-MANUSCRIPT-Q-QUERY-v0.1", "PANEL_SCOPE")
    indices = set()
    linked = {}
    counts = Counter()
    for source in panel["sources"]:
        ep = source["endpoint"]
        require(ep in ENDPOINTS, "PANEL_ENDPOINT")
        counts[ep] += 1
        a = source["A_cache_row"]
        candidates = source["candidates"]
        require(51265<=a<56962 and len(candidates)==len({c["C_identity"] for c in candidates})==32, "PANEL_SOURCE")
        rows = []
        for c in candidates:
            op, end = c["operation_row"], c["C_cache_row"]
            require(type(op) is int and 0<=op<322659 and op not in indices
                    and (0<=end<46140 or 51265<=end<56962), "PANEL_ROW_MAPPING")
            indices.add(op)
            rows.append((op,a,end))
        linked[ep,counts[ep]] = rows
    require(counts=={"Kd":16,"Ki":16} and len(indices)==1024, "PANEL_SIZE")
    geo.dispatch("CERTIFIED_MAPPING_BOUND",operation_rows=1024,sources=32,index_archive_rereads=0)
    return linked


def rank_policy(policy, view):
    if policy=="geometry":
        return geo.rank_geometry(view)
    return geo.replay.priorities(policy,view)


def query_batch(case, groups, cache, coordinates, archive, projections, target_cache, submissions, binding):
    distances = geo.project_geometry(case,groups,cache,coordinates)
    geo.dispatch("HISTORICAL_PROJECTION_RESERVED",groups=len(groups),scalars=32*len(groups))
    batch = {name:[geo.replay.scalar(archive,row,0) for row,_,_ in links] for name,links in groups.items()}
    require(not set(batch)&projections.keys(), "PROJECTIONS_READ_TWICE")
    projections.update(batch)
    geo.dispatch("HISTORICAL_PROJECTION_COMPLETE",groups=len(groups),scalars=32*len(groups))
    views = {"hash":{name:{"count":32} for name in groups},
             "gradient":{name:{"projection":a} for name,a in batch.items()},"geometry":distances}
    plan = {}
    for p in POLICIES:
        order = invoke(["--rank",p],views[p],binding)
        require(list(order)==list(groups) and all(len(v)==len(set(v))==8
                and all(type(i) is int and 1<=i<=32 for i in v) for v in order.values()), "RANK_POLICY_OUTPUT")
        plan[p] = order
    geo.dispatch("THREE_POLICY_ORDERS_FIXED",groups=len(groups),commit=stamp(encode(plan)))
    start = len(target_cache)
    for p in POLICIES:
        geo.dispatch("TARGET_QUERY_RESERVED",policy=p,groups=len(groups),requested_targets=8*len(groups),
                     visible_target_slots=12*len(groups))
        decoded = 0
        for name,order in plan[p].items():
            require(name not in submissions[p], "DUPLICATE_ACTION_GROUP")
            responses = []
            for ordinal in order:
                key = (name,ordinal)
                if key not in target_cache:
                    target_cache[key] = geo.replay.scalar(archive,groups[name][ordinal-1][0],3)
                    decoded += 1
                responses.append({"candidate":ordinal,"response":target_cache[key]})
            submissions[p][name] = {"query_8":responses,"query_4_prefix":4}
        geo.dispatch("TARGET_QUERY_COMPLETE",policy=p,groups=len(groups),requested_targets=8*len(groups),
                     visible_target_slots=12*len(groups),new_target_decodes=decoded)
    return len(target_cache)-start


def sealed_submissions():
    path = OUT/"action_seal.json"
    require(path.is_file(), "SCORING_REQUIRES_THREE_ACTION_SEALS")
    raw = path.read_bytes()
    seal = json.loads(raw)
    require(set(seal)==set(POLICIES), "ACTION_SEAL_SCOPE")
    results = {}
    for p in POLICIES:
        blob = (OUT/f"actions_{p}.private.json").read_bytes()
        require(stamp(blob)==seal[p], "ACTION_SEAL_CHANGED")
        results[p] = json.loads(blob)
    return results,stamp(raw)


def score():
    actions,seal = sealed_submissions()
    packet = json.load(sys.stdin)
    binding = json.loads((OUT/"binding.json").read_bytes())
    require(seal==packet["action_seal"] and os.getppid()==packet["broker_pid"]
            and psutil.Process(os.getppid()).ppid()==binding["supervisor_pid"], "SCORER_BINDING")
    target = packet["targets"]
    require(list(target)==geo.FULL_GROUPS and all(list(x)==geo.FULL_GROUPS for x in actions.values()), "SCORER_BOARD")
    rows,diagnostics = [],{}
    for name,entry in target.items():
        responses,projections=entry["responses"],entry["projections"]
        require(len(responses)==len(projections)==32 and all(math.isfinite(v) for v in responses+projections),
                "SCORER_HISTORICAL_FIELDS")
        error=geo.avg(abs(r-a) for r,a in zip(responses,projections,strict=True))
        zero=geo.avg(abs(r) for r in responses)
        diagnostics[name]={"E":error,"B":zero,"Skill":zero-error}
        for k in LIMITS:
            outcome={p:geo.replay.score_row(responses,actions[p][name],k) for p in POLICIES}
            delta = outcome["geometry"]["selection_loss"]-outcome["gradient"]["selection_loss"]
            rows.append({"group":name,"queries":k,**outcome,"uniform":geo.uniform_reference(responses,k),
                         "geometry_minus_gradient_loss":delta,"same_panel_skill":zero-error})
    paired=geo.emit("paired_results.private.json",{"fixed_query_rows":rows,"same_panel_by_group":diagnostics})
    summary=geo.emit("summary.json",{"fixed_query":{str(k):geo.group_summaries(rows,k) for k in LIMITS},
                                     "same_panel_approximation":geo.approximation_summary(diagnostics)})
    return {"scored_rows":len(rows),"scored_unique_case_sources":len(diagnostics),"paired":paired,"summary":summary}


def worker():
    binding = json.loads((OUT/"binding.json").read_bytes())
    require(os.getppid()==binding["supervisor_pid"], "WORKER_PARENT")
    joined=board(binding)
    links,archives,projections,target_cache = {},{},{},{}
    actions={p:{} for p in POLICIES}
    for case in CASES:
        groups={f"{case}:{ep}:{i:02}":joined[(ep,i)] for i in range(1,17) for ep in ENDPOINTS}
        links.update(groups)
        cache=geo.read_input(binding,f"{case}.cache",(56962,1024),"<f4",4)
        archive=geo.read_input(binding,f"{case}.old_response",geo.replay.SHAPE,"<f8",8)
        archives[case]=archive
        coordinates={}
        if case==CASES[0]:
            pilot=dict(list(groups.items())[:2])
            n=query_batch(case,pilot,cache,coordinates,archive,projections,target_cache,actions,binding)
            require(16<=n<=48 and all(len(actions[p])==2 for p in POLICIES),"PILOT_QUERY_ACCOUNT")
            geo.emit("pilot_receipt.json",{"status":"PILOT_ENGINEERING_ACCEPTED","case":case,"case_sources":2,
                                          "candidate_slots":64,"distances":64,"historical_projections":64,
                                          "policy_visible_slots":72,"target_decodes":n,"included_in_formal":True})
            groups=dict(list(groups.items())[2:])
        query_batch(case,groups,cache,coordinates,archive,projections,target_cache,actions,binding)
        del cache,coordinates
    require(list(links)==list(projections)==geo.FULL_GROUPS and all(list(v)==geo.FULL_GROUPS for v in actions.values()),
            "SEALED_GEOMETRY_REPLAY_COVERAGE")
    seal=geo.emit("action_seal.json",{p:geo.emit(f"actions_{p}.private.json",actions[p]) for p in POLICIES})
    geo.dispatch("ALL_ACTIONS_SEALED",seal=seal)
    extra=5120-len(target_cache)
    geo.dispatch("UNIFIED_SCORE_RESERVED",extra_targets=extra)
    packet={}
    for name,rows in links.items():
        archive=archives[name.split(":")[0]]
        packet[name]={"responses":[target_cache[(name,i)] if (name,i) in target_cache else geo.replay.scalar(archive,row,3)
                                     for i,(row,_,_) in enumerate(rows,1)],"projections":projections[name]}
    result=invoke(["--score"],{"targets":packet,"action_seal":seal,"broker_pid":os.getpid()},binding)
    geo.dispatch("UNIFIED_SCORE_COMPLETE",**result)
    geo.emit("execution_receipt.json",{
        "case_sources":160,"distances":5120,"archives_read":10,"index_archive_rereads":0,
        "historical_projections_decoded":5120,"historical_targets_decoded":5120,
        "unique_targets_before_action_seal":len(target_cache),"additional_targets_after_seal":extra,
        "requests_per_strategy_eight_check_prefix":1280,"four_check_views_per_strategy":640,
        "total_logical_target_accesses":5760,"reused_policy_target_requests":3840-len(target_cache),
        "model_payload_reads":0,"new_model_forward":0,"new_AD":0,"truth_reads":0,"unified_scoring_count":1,
        "scoring":result,"action_seal":seal
    })


def accept(binding, code):
    names=["binding.json","resources.jsonl","dispatch.jsonl","pilot_receipt.json","action_seal.json",
           "paired_results.private.json","summary.json","execution_receipt.json","supervisor_receipt.json"]
    names += [f"actions_{p}.private.json" for p in POLICIES]
    raw={name:(OUT/name).read_bytes() for name in names}
    budget=binding["limits"]
    used=sum(map(len,raw.values()))
    require(len(raw)+3<=budget["artifacts"] and used<=budget["artifact_MiB"]*geo.MIB
            and used<=budget["cumulative_acceptance_read_MiB"]*geo.MIB, "ACCEPTANCE_CAP")
    events=list(map(json.loads,raw["dispatch.jsonl"].splitlines()))
    kinds=[x["event"] for x in events]
    reads=[x for x in events if x["event"]=="INPUT_VERIFIED"]
    r=json.loads(raw["execution_receipt.json"])
    g=[x for x in events if x["event"]=="GEOMETRY_PROJECTION_COMPLETE"]
    take=[x for x in events if x["event"]=="TARGET_QUERY_COMPLETE"]
    require(code==0 and len(reads)==10 and {x["id"] for x in reads}==set(binding["inputs"])
            and all(x["whole_reads"]==1 and x["physical_bytes"]==binding["inputs"][x["id"]]["bytes"] for x in reads)
            and sum(x["physical_bytes"] for x in reads)==budget["physical_input_bytes"]
            and sum(x["logical_bytes"] for x in reads)==budget["identity_plus_use_logical_bytes"], "READ_BUDGET")
    require(sum(x["drug_rows"] for x in g)<=5280 and sum(x["drug_coordinates"] for x in g)<=1351680
            and sum(x["distances"] for x in g)==5120
            and sum(x["scalars"] for x in events if x["event"]=="HISTORICAL_PROJECTION_COMPLETE")==5120,
            "PROJECTION_BUDGET")
    require(all(sum(x["requested_targets"] for x in take if x["policy"]==p)==1280 for p in POLICIES)
            and sum(x["visible_target_slots"] for x in take)==5760
            and sum(x["new_target_decodes"] for x in take)==r["unique_targets_before_action_seal"]
            and kinds.count("ALL_ACTIONS_SEALED")==kinds.count("UNIFIED_SCORE_COMPLETE")==1
            and kinds.index("ALL_ACTIONS_SEALED")<kinds.index("UNIFIED_SCORE_RESERVED")<kinds.index("UNIFIED_SCORE_COMPLETE")
            and r["unique_targets_before_action_seal"]+r["additional_targets_after_seal"]==5120,"QUERY_ACCOUNTING")
    seal=json.loads(raw["action_seal.json"])
    require(all(stamp(raw[f"actions_{p}.private.json"])==seal[p] for p in POLICIES)
            and r["scoring"]["scored_rows"]==320 and r["scoring"]["scored_unique_case_sources"]==160
            and stamp(raw["paired_results.private.json"])==r["scoring"]["paired"]
            and stamp(raw["summary.json"])==r["scoring"]["summary"], "RESULT_SEALS")
    geo.remaining(binding)
    return {"status":"PRIVATE_FR40_NEW_SOURCE_REPLICATION_ACCEPTED","acceptance_read_bytes":used,
            "physical_input_bytes":budget["physical_input_bytes"],"logical_input_bytes":budget["identity_plus_use_logical_bytes"],
            "projected_drug_rows":sum(x["drug_rows"] for x in g),"projected_drug_coordinates":sum(x["drug_coordinates"] for x in g),
            "worker_exit_code":code,"artifacts":{k:stamp(v) for k,v in raw.items()}}


def supervise(entered):
    authority=AUTH.read_bytes()
    grant=json.loads(authority)
    paths={"executor":SELF,"geometry_helpers":GEO_CODE,"fixed_query_helpers":geo.PRIOR_CODE,"plan":PLAN,
           "preparation_registry":PREP_REG,"preparation_acceptance":PREP/"preparation_acceptance.json",
           "old_geometry_closeout":OLD_RESULT}
    raw={k:p.read_bytes() for k,p in paths.items()}
    controls={k:stamp(v) for k,v in raw.items()}
    require(grant["status"]=="ONE_FR40_NEW_SOURCE_REPLICATION_RELEASED" and grant["controls"]==controls,"AUTHORITY")
    preparation=json.loads(raw["preparation_acceptance"])
    require(preparation["status"]=="FR40_NEW_SOURCE_PANEL_METADATA_QUALIFIED"
            and preparation["artifacts"]["candidate_manifest.private.json"]==grant["candidate_manifest"],"PREP_SEAL")
    require(json.loads(raw["old_geometry_closeout"])["status"]=="PRIVATE_FR40_GEOMETRY_CONTROL_ACCEPTED","OLD_PANEL_STATUS")
    inputs={v["id"]:v for v in json.loads(raw["preparation_registry"])["future_inputs_reference_only"]}
    require(inputs==grant["inputs"] and list(inputs)==[f"{case}.{kind}" for case in CASES for kind in ("cache","old_response")]
            and sum(x["bytes"] for x in inputs.values())==1786088320, "INPUT_SCOPE")
    caps=grant["limits"]
    require(caps=={"physical_input_bytes":1786088320,"identity_plus_use_logical_bytes":3572176640,
                   "wall_seconds":1800,"monotonic_seconds":1800,"artifacts":16,"artifact_MiB":16,
                   "cumulative_acceptance_read_MiB":16}, "CAPS")
    binding={"supervisor_pid":os.getpid(),"authority":stamp(authority),"controls":controls,
             "candidate_manifest":grant["candidate_manifest"],"limits":caps,"inputs":inputs,
             "wall_deadline":entered[0]+1800,"monotonic_deadline":entered[1]+1800}
    geo.remaining(binding)
    OUT.mkdir(parents=True,exist_ok=False)
    geo.emit("binding.json",binding)
    process=None
    failure=None
    try:
        geo.resources(binding)
        process=subprocess.Popen([sys.executable,"-I","-B",str(SELF),"--worker"],stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,start_new_session=True)
        while True:
            geo.resources(binding)
            try:
                require(process.wait(timeout=min(5,geo.remaining(binding)))==0,"BROKER_FAILED")
                break
            except subprocess.TimeoutExpired:
                pass
        geo.resources(binding)
    except Exception as exc:
        failure=geo.reason(exc)
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        code=None if process is None else process.returncode
        geo.emit("supervisor_receipt.json",{"worker_exit_code":code,"failure":failure,
                                          "wall_elapsed_seconds":time.time()-entered[0],
                                          "monotonic_elapsed_seconds":time.monotonic()-entered[1]})
    if failure is not None:
        return 1
    try:
        geo.emit("run_acceptance.json",accept(binding,code))
    except Exception as exc:
        geo.emit("acceptance_failure.json",{"failure":geo.reason(exc)})
        return 1
    return 0


def self_test():
    from itertools import combinations
    values=[0.0]*32
    values[1],values[5]=-5.0,5.0
    expect=[2,6,1,3,4,5,7,8]
    require(rank_policy("geometry",{"x":[abs(v) for v in values]})["x"]==expect
            and rank_policy("gradient",{"x":{"projection":values}})["x"]==expect
            and rank_policy("hash",{"x":{"count":32}})["x"]==list(range(1,9)), "SYNTHETIC_POLICY_AND_TIES")
    require(geo.distance((0.0,),(0.0,))==0.0 and geo.distance((0.0,0.0),(3.0,4.0))==5.0, "SYNTHETIC_DISTANCE")
    y=[0.0,-0.5,-1.5,1.5,0.8,-0.3]
    for k in (2,4):
        maxima=[max(map(abs,z)) for z in combinations(y,k)]
        calc=geo.uniform_reference(y,k)
        require(math.isclose(calc["selection_loss"],geo.avg(1.5-z for z in maxima))
                and math.isclose(calc["hit"],geo.avg(z==1.5 for z in maxima)), "SYNTHETIC_UNIFORM")
    try:
        sealed_submissions()
    except geo.replay.Stop as exc:
        require(str(exc)=="SCORING_REQUIRES_THREE_ACTION_SEALS","SYNTHETIC_SCORING_GATE")
    else:
        raise geo.replay.Stop("SELF_TEST_REQUIRES_PRISTINE_RUN_PATH")
    print("SYNTHETIC_NEW_PANEL_SCOPE_AND_SCORER_GATE_OK")


if __name__=="__main__":
    args=sys.argv[1:]
    if args==["--self-test"]:
        self_test()
    elif args==["--worker"]:
        try:
            worker()
        except Exception as exc:
            geo.emit("worker_failure.json",{"failure":geo.reason(exc)})
            sys.exit(1)
    elif len(args)==2 and args[0]=="--rank" and args[1] in POLICIES:
        print(encode(rank_policy(args[1],json.load(sys.stdin))).decode(),end="")
    elif args==["--score"]:
        print(encode(score()).decode(),end="")
    else:
        require(not args,"ENTRY_ARGS")
        sys.exit(supervise((time.time(),time.monotonic())))
