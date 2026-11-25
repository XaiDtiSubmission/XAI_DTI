"""Approved v0.2 calculations on future frozen predictions. No IO or analysis at import."""
from collections import defaultdict
import math
import numpy as np
from .spec import FORMAL_SEEDS, CONTRACT_ID


def authorize_analysis(authorization):
    if (authorization.get("approved") is not True
            or authorization.get("science_contract_id") != CONTRACT_ID
            or authorization.get("purpose") != "FROZEN_PREDICTION_ANALYSIS"
            or not authorization.get("direct_user_execution_authority")):
        raise PermissionError("FROZEN_PREDICTION_ANALYSIS_NOT_AUTHORIZED")


def aligned_losses(rows, S_cases, G_cases):
    ids = ("label_group_id","pair_group_id","endpoint")
    keys = [tuple(r[k] for k in ids) for r in rows]
    if (len(rows) != 5697 or len(set(keys)) != len(keys)
            or len({k[0] for k in keys}) != len(keys)
            or len({k[1:] for k in keys}) != len(keys)
            or any(r["v3_3_role"] != "MODEL_GATE" for r in rows)):
        raise ValueError("INCOMPLETE_GATE_IDENTITIES")
    if set(S_cases) != set(FORMAL_SEEDS) or set(G_cases) != set(FORMAL_SEEDS):
        raise ValueError("INCOMPLETE_FORMAL_SEEDS")
    endpoints = np.array([r["endpoint"] for r in rows])
    if {e:int(np.sum(endpoints == e)) for e in ("Kd","Ki")} != {"Kd":798,"Ki":4899}:
        raise ValueError("ENDPOINT_COVERAGE")
    y = np.asarray([r["y_true"] for r in rows],dtype=np.float64)
    for r in rows:
        if any(not isinstance(r.get(k),str) or not r[k]
               for k in ("compound_connectivity_key","target_sequence_key")):
            raise ValueError("MISSING_CLUSTER_KEY")
    arm_losses = []
    for cases in (S_cases,G_cases):
        values = []
        for seed in FORMAL_SEEDS:
            case = cases[seed]
            by_id = {tuple(r[k] for k in ids):r for r in case}
            if len(case) != len(keys) or len(by_id) != len(case) or set(by_id) != set(keys):
                raise ValueError("PREDICTION_ROW_COVERAGE")
            ordered = [by_id[k] for k in keys]
            truth = np.asarray([r["y_true"] for r in ordered],dtype=np.float64)
            prediction = np.asarray([r["y_pred"] for r in ordered],dtype=np.float64)
            if not np.array_equal(truth,y) or not np.isfinite(y).all() or not np.isfinite(prediction).all():
                raise ValueError("TRUTH_MISMATCH_OR_NONFINITE")
            values.append(np.abs(prediction-y))
        arm_losses.append(np.stack(values))
    return endpoints,arm_losses[0],arm_losses[1]


def cluster_component(scores, keys, N):
    by_key = defaultdict(list)
    for value,key in zip(scores,keys,strict=True):
        by_key[key].append(float(value))
    count = len(by_key)
    if count <= 1:
        raise ValueError("DEGENERATE_CLUSTER_PARTITION")
    factor = count/(count-1)*(N-1)/(N-2)
    value = factor*math.fsum(math.fsum(v)**2 for v in by_key.values())
    return count,factor,value


def primary(d, endpoints, compounds, targets):
    from scipy.stats import t as student_t
    means = {e:float(d[endpoints == e].mean()) for e in ("Kd","Ki")}
    D = (means["Kd"]+means["Ki"])/2
    score = np.empty(len(d),dtype=np.float64)
    for e in ("Kd","Ki"):
        mask = endpoints == e
        score[mask] = (d[mask]-means[e])/(2*int(mask.sum()))
    C,fc,vc = cluster_component(score,compounds,len(d))
    T,ft,vt = cluster_component(score,targets,len(d))
    I,fi,vi = cluster_component(score,list(zip(compounds,targets,strict=True)),len(d))
    V = vc+vt-vi
    report = {"D":D,"D_endpoints":means,"N":len(d),"C":C,"T":T,"I":I,
              "CR1":{"compound":fc,"target":ft,"intersection":fi},
              "variance_components":{"compound":vc,"target":vt,"intersection":vi},
              "V":V,"df":min(C,T)-1,"SE":None,"t":None,"p_nominal":None,
              "CI95":None,"decision":"NOT_TESTABLE",
              "scope":"EXPOSED_DEVELOPMENT_NOMINAL_ONLY"}
    if not math.isfinite(V) or V <= 0:
        report["reason"] = "NONPOSITIVE_OR_NONFINITE_CGM_VARIANCE"
        return report
    se = math.sqrt(V)
    stat = D/se
    p = float(2*student_t.sf(abs(stat),df=report["df"]))
    critical = float(student_t.ppf(.975,df=report["df"]))
    interval = [D-critical*se,D+critical*se]
    if not all(math.isfinite(x) for x in [se,stat,p,*interval]):
        report["reason"] = "NONFINITE_PRIMARY_STATISTIC"
        return report
    if p == 0:
        # No p=0 output; numerical reporting failure cannot silently create a decision.
        raise ArithmeticError("P_UNDERFLOW_REQUIRES_NUMERICAL_REPORTING_AUDIT")
    positive = D > 0 and p < .05
    negative = D < 0 and p < .05
    if positive != (interval[0] > 0) or negative != (interval[1] < 0):
        raise ArithmeticError("P_CI_DECISION_DISAGREEMENT_AUDIT")
    report.update(SE=se,t=stat,p_nominal=p,CI95=interval,
                  decision=("S_NOMINAL_ADVANTAGE_DEV" if positive else
                            "G_NOMINAL_ADVANTAGE_DEV" if negative else
                            "NO_NOMINAL_DIFFERENCE_DETECTED_DEV"))
    return report


def entity_bootstrap(d,endpoints,compounds,targets):
    ckeys = sorted(set(compounds),key=lambda s:s.encode("utf-8"))
    tkeys = sorted(set(targets),key=lambda s:s.encode("utf-8"))
    cindex,tindex = {k:i for i,k in enumerate(ckeys)},{k:i for i,k in enumerate(tkeys)}
    ci = np.asarray([cindex[k] for k in compounds],dtype=np.int64)
    ti = np.asarray([tindex[k] for k in targets],dtype=np.int64)
    rng = np.random.Generator(np.random.PCG64(220017))
    draws = []
    invalid = 0
    for _ in range(2000):
        cw = np.bincount(rng.integers(len(ckeys),size=len(ckeys)),minlength=len(ckeys))
        tw = np.bincount(rng.integers(len(tkeys),size=len(tkeys)),minlength=len(tkeys))
        weight = (cw[ci]*tw[ti]).astype(np.float64)
        values = []
        for e in ("Kd","Ki"):
            mask = endpoints == e
            denominator = float(weight[mask].sum())
            values.append(float(np.dot(weight[mask],d[mask])/denominator)
                          if denominator > 0 else float("nan"))
        value = (values[0]+values[1])/2
        if math.isfinite(value):
            draws.append(value)
        else:
            invalid += 1
    return {"status":"COMPLETE" if invalid == 0 else "INCOMPLETE",
            "planned":2000,"valid":len(draws),"invalid":invalid,
            "CI95":np.quantile(draws,[.025,.975],method="linear").tolist() if invalid == 0 else None,
            "p_value":None,"role":"SENSITIVITY_ONLY"}


def cluster_concentration(endpoints,keys):
    groups = defaultdict(list)
    weights = np.empty(len(keys),dtype=np.float64)
    for e in ("Kd","Ki"):
        mask = endpoints == e
        weights[mask] = 1/(2*int(mask.sum()))
    for i,key in enumerate(keys):
        groups[key].append(i)
    sizes = np.asarray([len(v) for v in groups.values()])
    return {"clusters":len(groups),"rows_min":int(sizes.min()),
            "rows_median":float(np.median(sizes)),"rows_max":int(sizes.max()),
            "max_endpoint_balanced_weight_fraction":
                max(float(weights[v].sum()) for v in groups.values())}


def analyze(rows,S_cases,G_cases,authorization):
    authorize_analysis(authorization)
    endpoints,S,G = aligned_losses(rows,S_cases,G_cases)
    difference = G-S
    d = difference.mean(axis=0)
    compounds = [r["compound_connectivity_key"] for r in rows]
    targets = [r["target_sequence_key"] for r in rows]
    result = primary(d,endpoints,compounds,targets)
    by_arm = {}
    for arm,losses in (("S",S),("G",G)):
        endpoint_maes = {e:losses[:,endpoints == e].mean(axis=1) for e in ("Kd","Ki")}
        balanced = (endpoint_maes["Kd"]+endpoint_maes["Ki"])/2
        by_arm[arm] = {"endpoint_MAE_by_seed":{e:v.tolist() for e,v in endpoint_maes.items()},
                       "EBMAE_by_seed":balanced.tolist(),"mean_EBMAE":float(balanced.mean())}
    seed_d = np.asarray(by_arm["G"]["EBMAE_by_seed"])-np.asarray(by_arm["S"]["EBMAE_by_seed"])
    mean,sd = float(seed_d.mean()),float(seed_d.std(ddof=1))
    if not math.isclose(mean,result["D"],rel_tol=1e-12,abs_tol=1e-12):
        raise ArithmeticError("AGGREGATION_ORDER_NUMERICAL_AUDIT")
    base = by_arm["G"]["mean_EBMAE"]
    half = 2.7764451051977987*sd/math.sqrt(5)
    result.update(arms=by_arm,D_by_seed=seed_d.tolist(),seed_SD=sd,
                  seed_t95_descriptive=[mean-half,mean+half],
                  relative_improvement_percent=100*result["D"]/base if base != 0 else None,
                  practical_margin=None,practical_significance="NOT_REGISTERED",
                  entity_bootstrap=entity_bootstrap(d,endpoints,compounds,targets),
                  concentration={"compound":cluster_concentration(endpoints,compounds),
                                 "target":cluster_concentration(endpoints,targets)},
                  independent_confirmation=False,joint_seed_entity_inference=False)
    return result
