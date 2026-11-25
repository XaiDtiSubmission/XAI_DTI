"""Qualify a new FR40 source panel without accessing cached vectors or responses."""

from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time


SELF = Path(__file__).resolve()
ROOT = SELF.parents[1]
PRIOR_CODE = SELF.with_name("run_manuscript_midpoint_query_a2_fr40_metadata_v0_2.py")
spec = importlib.util.spec_from_file_location("fr40_metadata_protocol", PRIOR_CODE)
md = importlib.util.module_from_spec(spec)
spec.loader.exec_module(md)
require = md.require
OUT = ROOT / "output/manuscript_midpoint_query_v0_1/FR40_new_source_panel_v0_1/preparation_v0_1"
PRIOR_OUT = md.OUT
md.OUT = OUT
PLAN = ROOT / "planning/manuscript_fr40_new_source_panel_preparation_v0_1.json"
A2_REGISTRY = ROOT / "planning/manuscript_midpoint_query_a2_execution_contract_v0_2.json"
HISTORY = ROOT / "planning/manuscript_path_recheck_v0_1.json"
DESIGN = ROOT / "docs/FR40新源面板复核实验方案_v0.1.md"
OLD_RESULT = ROOT / "output/manuscript_midpoint_query_v0_1/FR40_geometry_control_v0_1/run_001/closeout.json"
INDEX = struct.Struct("<14q")
FIFTH = "prior_replay_manifest"


def preparation_failure(exc):
    return str(exc) if isinstance(exc, md.Stop) else type(exc).__name__


def verify_controls(plan):
    controls = {
        "executor":SELF,"previous_metadata_executor":PRIOR_CODE,"experiment_plan":DESIGN,
        "candidate_protocol":A2_REGISTRY,"historical_registry":HISTORY,
        "previous_candidate_acceptance":PRIOR_OUT/"candidate_acceptance.json",
        "previous_candidate_receipt":PRIOR_OUT/"candidate_receipt.json",
        "previous_geometry_closeout":OLD_RESULT,
    }
    digest = {k:md.stamp(v) for k,v in controls.items()}
    require(plan["controls"]==digest and plan["status"]=="PREPARATION_ONLY_RELEASED", "AUTHORITY_CONTROLS")
    registry = json.loads(A2_REGISTRY.read_bytes())["candidate_qualification"]
    accepted = json.loads(controls["previous_candidate_acceptance"].read_bytes())
    prior = json.loads(controls["previous_candidate_receipt"].read_bytes())
    geo = json.loads(OLD_RESULT.read_bytes())
    require(accepted["candidate_status"]==prior["status"]=="FR40_CANDIDATE_QUALIFIED"
            and geo["status"]=="PRIVATE_FR40_GEOMETRY_CONTROL_ACCEPTED", "ANCESTOR_ACCEPTANCE")
    seal = accepted["artifacts"]["candidate_manifest.private.json"]
    require(seal==prior["candidate_manifest_seal"], "PRIOR_MANIFEST_SEAL")
    old_manifest = {"id":FIFTH,"path":str((PRIOR_OUT/"candidate_manifest.private.json").relative_to(ROOT)),**seal}
    require(plan["metadata_inputs"]==[*registry["inputs"],old_manifest], "METADATA_INPUT_DESCRIPTORS")
    require(plan["selection"]=={k:registry[k] for k in ("GINE_seeds","distinct_C_per_source","sources","ranking_rule")},
            "FROZEN_SELECTION")
    upper = sum(x["bytes"] for x in plan["metadata_inputs"])
    caps = plan["preparation_caps"]
    require(upper==caps["physical_input_bytes"]==38094601
            and caps["identity_plus_use_logical_bytes"]==2*upper and caps["whole_reads_per_file"]==1
            and caps["wall_seconds"]==caps["monotonic_seconds"]==900, "PREPARATION_BUDGET")
    require(not plan["formal_run_authorized"], "SCIENTIFIC_AUTHORITY")
    descriptors = {x["id"]:x for x in json.loads(HISTORY.read_bytes())["future_input_descriptors_reference_only"]}
    future = [descriptors[f"formal_{s:03d}_G.{kind}"] for s in registry["GINE_seeds"] for kind in ("cache","old_response")]
    require(plan["future_inputs_reference_only"]==future and sum(x["bytes"] for x in future)==1786088320,
            "FUTURE_REFERENCE_SCOPE")
    return digest


def prior_exclusions(inputs, by_id, clock):
    prior = json.loads(md.input_metadata(inputs[FIFTH],clock))
    require((prior["operation"],prior["span"],prior["function"])==("D",1,"q")
            and prior["GINE_seeds"]==[17,29,43,71,101]
            and prior["rank_rule"]["hash_domain"]=="DTI-MANUSCRIPT-Q-QUERY-v0.1", "PRIOR_MANIFEST_SCOPE")
    seen = set()
    groups = set()
    endpoints = Counter()
    for source in prior["sources"]:
        sid = source["source_id"]
        require(sid in by_id and sid not in seen and md.identity(source)==md.identity(by_id[sid]), "PRIOR_SOURCE_JOIN")
        seen.add(sid)
        endpoints[source["endpoint"]] += 1
        groups.add(source["pair_group_id"])
    require(endpoints=={"Kd":16,"Ki":16} and len(seen)==32 and len(groups)<=32, "PRIOR_REPLAY_PANEL_SIZE")
    return groups


def certify_rows(raw, selected):
    start = 10 + struct.unpack_from("<H", raw, 8)[0]
    seen = set()
    for source in selected:
        a = set()
        for candidate in source["candidates"]:
            row = candidate["operation_row"]
            require(type(row) is int and 0<=row<322659 and row not in seen, "SELECTED_OPERATION_ROW")
            seen.add(row)
            data = INDEX.unpack_from(raw, start+INDEX.size*row)
            require(data[:3]==(source["source_id"],candidate["FIT_donor_row"],md.ENDPOINTS.index(source["endpoint"]))
                    and data[5]==candidate["C_identity"] and data[9]==candidate["C_cache_row"]
                    and data[13]==1, "SELECTED_OPERATION_JOIN")
            require(51265<=data[7]<56962 and (0<=data[9]<46140 or 51265<=data[9]<56962), "SELECTED_CACHE_ROLE")
            a.add((data[3],data[7]))
        require(len(a)==1 and len({c["C_identity"] for c in source["candidates"]})==32, "SELECTED_SOURCE_SUPPORT")
        source["A_identity"],source["A_cache_row"] = next(iter(a))
    require(len(seen)==1024, "SELECTED_MAPPING_SIZE")
    return len(seen)


def prepare(plan):
    binding = json.loads((OUT/"binding.json").read_bytes())
    require(os.getppid()==binding["supervisor_pid"], "WORKER_PARENT")
    clock = binding["clock"]
    inputs = {x["id"]:x for x in plan["metadata_inputs"]}
    by_id, old_path, path_recheck = md.load_identity_metadata(inputs,clock)
    old_replay = prior_exclusions(inputs,by_id,clock)
    require(not (old_replay & (old_path|path_recheck)), "ANCESTOR_SOURCE_PAIR_OVERLAP")
    raw = md.input_metadata(inputs["indices"],clock)
    links, link_counts = md.read_indices(raw,by_id,(322659,14),clock)
    ready, selected, aggregate = md.build_panel(plan["selection"],by_id,links,old_path|old_replay,path_recheck,clock)
    affected = {row["pair_group_id"] for row in selected}
    ancestors = {"original_path":old_path,"followup_path":path_recheck,"prior_fixed_query_replay":old_replay}
    overlaps = {name:len(affected & groups) for name,groups in ancestors.items()}
    require(not any(overlaps.values()), "NEW_PANEL_SOURCE_PAIR_OVERLAP")
    desc = None
    verified = 0
    if ready:
        require(Counter(s["endpoint"] for s in selected)=={"Kd":16,"Ki":16}, "SELECTED_ENDPOINTS")
        verified = certify_rows(raw,selected)
        md.guard_clock(clock)
        desc = md.emit("candidate_manifest.private.json", {
            "scope":"FINITE_EXPOSED_DEVELOPMENT_O_SRC","operation":"D","span":1,"function":"q",
            "GINE_seeds":plan["selection"]["GINE_seeds"],"rank_rule":plan["selection"]["ranking_rule"],
            "operation_index_fingerprint":{"bytes":inputs["indices"]["bytes"],"sha256":inputs["indices"]["sha256"]},
            "preparation":"THREE_PRIOR_SOURCE_PAIR_GROUP_EXCLUSIONS",
            "selected_source_pair_group_overlap":overlaps,"sources":selected
        })
    md.emit("candidate_receipt.json", {
        "status":"FR40_NEW_SOURCE_PANEL_METADATA_QUALIFIED" if ready else "FR40_NEW_SOURCE_PANEL_NOT_READY",
        "excluded_pair_groups":{name:len(group) for name,group in ancestors.items()},
        "combined_excluded_pair_groups":len(old_path|path_recheck|old_replay),
        "selected_pair_group_overlaps":overlaps,"by_endpoint":aggregate,
        "qualification_index_rows":sum(link_counts.values()),"selected_index_rows_certified":verified,
        "candidate_manifest":desc,"scientific_calls":{"frozen_cache_payload_reads":0,"response_array_reads":0,
        "checkpoint_reads":0,"labels":0,"forward":0,"input_AD":0,"scoring":0},
    })


def accept(plan, clock):
    names = ["binding.json","dispatch.jsonl","resources.jsonl","candidate_receipt.json"]
    blobs = {name:(OUT/name).read_bytes() for name in names}
    receipt = json.loads(blobs["candidate_receipt.json"])
    events = list(map(json.loads,blobs["dispatch.jsonl"].splitlines()))
    expected = {x["id"]:x for x in plan["metadata_inputs"]}
    reserved = [e for e in events if e["event"]=="RESERVED"]
    verified = [e for e in events if e["event"]=="READ_VERIFIED"]
    require(len(events)==10 and len(reserved)==len(verified)==5
            and {x["id"] for x in reserved}=={x["id"] for x in verified}==set(expected), "PREPARATION_READ_LEDGER")
    require(all(e["whole_reads"]==1 and e["sha256"]==expected[e["id"]]["sha256"]
                and e["physical_bytes"]==expected[e["id"]]["bytes"]
                and e["identity_plus_use_logical_bytes_charged"]==2*e["physical_bytes"] for e in verified),
            "PREPARATION_BYTES")
    require(receipt["qualification_index_rows"]==322659 and not any(receipt["scientific_calls"].values()),"PREPARATION_SCOPE")
    if receipt["status"]=="FR40_NEW_SOURCE_PANEL_METADATA_QUALIFIED":
        name = "candidate_manifest.private.json"
        blobs[name]=(OUT/name).read_bytes()
        require(receipt["candidate_manifest"]=={"bytes":len(blobs[name]),"sha256":md.digest(blobs[name])},
                "CANDIDATE_ACCEPTANCE_SEAL")
        manifest = json.loads(blobs[name])
        selected = manifest["sources"]
        require(Counter(x["endpoint"] for x in selected)=={"Kd":16,"Ki":16}
                and all(len(s["candidates"])==32 and 51265<=s["A_cache_row"]<56962 for s in selected)
                and not any(manifest["selected_source_pair_group_overlap"].values())
                and receipt["selected_index_rows_certified"]==1024, "CANDIDATE_ACCEPTANCE_COUNTS")
    else:
        require(receipt["status"]=="FR40_NEW_SOURCE_PANEL_NOT_READY" and receipt["candidate_manifest"] is None,
                "UNKNOWN_PREPARATION_STATUS")
    counts = sum(map(len,blobs.values()))
    caps = plan["preparation_caps"]
    require(counts<=caps["cumulative_acceptance_read_MiB"]*2**20
            and counts<=caps["artifact_MiB"]*2**20 and len(blobs)+3<=caps["artifacts"], "PREPARATION_ACCEPTANCE_CAP")
    md.guard_clock(clock)
    return {"status":receipt["status"],"acceptance_read_bytes":counts,"metadata_files_read":5,
            "physical_bytes":sum(e["physical_bytes"] for e in verified),
            "identity_plus_use_logical_bytes":sum(e["identity_plus_use_logical_bytes_charged"] for e in verified),
            "formal_run_authorized":False,
            "artifacts":{name:{"bytes":len(blob),"sha256":md.digest(blob)} for name,blob in blobs.items()}}


def supervise(plan, raw_plan, entered):
    controls=verify_controls(plan)
    clock = {"wall_start":entered[0],"monotonic_start":entered[1],
             "wall_deadline":entered[0]+900,"monotonic_deadline":entered[1]+900}
    md.guard_clock(clock)
    OUT.mkdir(parents=True,exist_ok=False)
    md.emit("binding.json",{"supervisor_pid":os.getpid(),"clock":clock,"controls":controls,
                            "preparation_registry_sha256":md.digest(raw_plan),"formal_run_authorized":False})
    worker = None
    failure = None
    status = "ENGINEERING_FAILED"
    try:
        md.resource_check([os.getpid()])
        worker = subprocess.Popen([sys.executable,"-I","-B",str(SELF),"--worker"],
                                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        while True:
            md.guard_clock(clock)
            md.resource_check([os.getpid(),worker.pid])
            try:
                require(worker.wait(timeout=min(5,clock["monotonic_deadline"]-time.monotonic()))==0,
                        "METADATA_WORKER_FAILED")
                break
            except subprocess.TimeoutExpired:
                continue
        md.resource_check([os.getpid()])
        accepted = accept(plan,clock)
        md.emit("preparation_acceptance.json",accepted)
        status = accepted["status"]
    except Exception as exc:
        failure = preparation_failure(exc)
    finally:
        if worker is not None and worker.poll() is None:
            worker.kill()
            worker.wait()
        md.emit("supervisor_receipt.json",{
            "status":status,"failure":failure,"worker_exit_code":None if worker is None else worker.returncode,
            "wall_elapsed_seconds":time.time()-entered[0],"monotonic_elapsed_seconds":time.monotonic()-entered[1]})
    return 0 if failure is None else 1


if __name__=="__main__":
    entered = (time.time(),time.monotonic())
    raw = PLAN.read_bytes()
    plan = json.loads(raw)
    if sys.argv[1:]==["--worker"]:
        try:
            prepare(plan)
        except Exception as exc:
            md.emit("worker_failure.json",{"failure":preparation_failure(exc)})
            sys.exit(1)
    else:
        require(not sys.argv[1:],"ENTRY_ARGUMENTS")
        sys.exit(supervise(plan,raw,entered))
