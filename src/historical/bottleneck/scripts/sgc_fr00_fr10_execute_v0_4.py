"""FR00/FR10 one-use execution. Stdlib import only; no work at import."""
import argparse, collections, hashlib, importlib.metadata, io, json, math, os
import re, shutil, signal, stat, struct, subprocess, sys, time, traceback, zipfile
from pathlib import Path

ROOT = Path('historical_sources/DTI_XAI_Bottleneck')
P = ROOT/'protocol/smiles_gine_bottleneck_fr10_solver_repair_v0_1'
C = ROOT/'protocol/smiles_gine_bottleneck_fr00_fr10_v0_2'
OLD = ROOT/'protocol/smiles_gine_bottleneck_fr00_fr10_v0_1'
SCRIPT = ROOT/'scripts/sgc_fr00_fr10_execute_v0_4.py'
CID = 'SGC-FR00-FR10-v0.5-SOLVER-BUDGET-REPAIR'
IDS = ('label_group_id','pair_group_id','endpoint')
IDENTITY = set(IDS)|{'v3_3_role','smiles_sha256','target_sequence_key',
    'binding_row_index0','train_fit_row_index0','train_tune_row_index0','graph_record_index0'}
GIB = 2**30
NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')

class Stop(Exception): pass
def need(ok, message):
    if not ok: raise Stop(message)
def encoded(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n').encode()
def digest(raw): return hashlib.sha256(raw).hexdigest()
def fp(s):
    return {k:str(getattr(s,k)) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_nlink')}
def rowkey(row): return tuple(row[k] for k in IDS)
def utfkey(row): return tuple(v.encode('utf8') for v in rowkey(row))
def identity(pid):
    import psutil
    p=psutil.Process(pid)
    return {'pid':pid,'birth':p.create_time(),'command':p.cmdline(),'UID':p.uids().real,'cwd':p.cwd(),'PGID':os.getpgid(pid)}
def live(rec):
    import psutil
    try: return identity(rec['pid'])==rec and psutil.Process(rec['pid']).status()!=psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess,ProcessLookupError): return False
def put(out,name,value,replace=False):
    target=out/name; raw=encoded(value); temp=target.with_name(target.name+'.tmp') if replace else target
    need('/' not in name and not name.startswith('.'),'OUTPUT_NAME')
    with temp.open('xb') as f: f.write(raw);f.flush();os.fsync(f.fileno())
    if replace: os.replace(temp,target)
    return {'path':str(target),'bytes':len(raw),'sha256':digest(raw)}
def append(out,name,value):
    with (out/name).open('ab') as f: f.write(encoded(value));f.flush()
def descriptor(path):
    b=path.read_bytes()
    return {'path':str(path),'bytes':len(b),'sha256':digest(b)}
def error(exc):
    return {'code':str(exc) if isinstance(exc,Stop) else 'ENGINEERING_'+type(exc).__name__,
            'frames':[{'file':Path(t.filename).name,'line':t.lineno,'function':t.name} for t in traceback.extract_tb(exc.__traceback__)]}

class Guard:
    def __init__(self,out,claim):
        self.out,self.claim=out,claim;self.phase='BOOT';self.last=0.
        self.iteration_charge=claim.get('historical_iteration_charge',0)
    def check(self,force=False):
        need(time.time()<self.claim['wall_deadline'] and time.monotonic()<self.claim['monotonic_deadline'],'STAGE_DEADLINE')
        need(not (self.out/'safety_stop.json').exists(),'WATCHDOG_STOP')
        if force or time.monotonic()-self.last>=5:
            import psutil
            need(psutil.Process().memory_info().rss<=36*GIB,'RSS_LIMIT')
            need(psutil.virtual_memory().available>=4*GIB,'AVAILABLE_MEMORY_LIMIT')
            need(shutil.disk_usage(ROOT).free>=20*GIB,'DISK_LIMIT')
            files=[p for p in self.out.iterdir() if p.is_file()]
            need(len(files)<= (30 if self.claim['stage']=='FR00' else 100),'OUTPUT_FILE_LIMIT')
            need(sum(p.stat().st_size for p in files)<=268435456,'OUTPUT_BYTES_LIMIT')
            put(self.out,'worker_progress.json',{'phase':self.phase,'wall':time.time(),'monotonic':time.monotonic()},True)
            self.last=time.monotonic()

class Reader:
    def __init__(self,out,guard):
        self.out,self.guard=out,guard;self.seen=set();self.cache={};self.used=collections.Counter(guard.claim.get('historical_source_spending',{}))
        self.caps={'package':(50,524288),'binding':(4,82899),'control':(40,354748),
                   'identity':(2,30708666),'handoff':(1,105149),'prediction':(20,6859340),
                   'source':(2,10081639),'FR00_output':(30,268435456)}
        self.caps=guard.claim.get('cumulative_source_caps',self.caps)
    def read(self,rec,category):
        path=Path(rec['path']);token=str(path)
        if token in self.seen:
            need(token in self.cache and self.cache[token][0]==rec['sha256'],'INPUT_REPEAT_OR_CONFLICT')
            return self.cache[token][1]
        need(category in self.caps and path.is_absolute() and '..' not in path.parts,'UNLISTED_PATH')
        is_source=category=='source'
        need(path.is_relative_to(Path('historical_sources/DTI_XAI')) if is_source else path.is_relative_to(ROOT),'SOURCE_ROOT')
        self.guard.check();self.seen.add(token)
        self.used[category+'_files']+=1;self.used[category+'_bytes']+=rec['bytes']
        cf,cb=self.caps[category]
        need(self.used[category+'_files']<=cf and self.used[category+'_bytes']<=cb,'SOURCE_BUDGET_'+category)
        append(self.out,'source_and_call_ledger.jsonl',{'operation':'SOURCE_RESERVE','category':category,'descriptor':rec,'used':dict(self.used)})
        for q in (path,*path.parents): need(not q.is_symlink(),'INPUT_SYMLINK')
        before=path.lstat();need(stat.S_ISREG(before.st_mode) and before.st_size==rec['bytes'],'INPUT_SIZE_TYPE')
        expected=rec.get('fingerprint',rec.get('historical_fingerprint',{}))
        for k,v in expected.items():
            if is_source and k=='st_dev':
                need(str(before.st_dev)==str(v) or (str(v)=='16777234' and str(before.st_dev)=='16777231'),'UNAPPROVED_DEVICE_CHANGE')
            else: need(str(getattr(before,k))==str(v),'INPUT_IDENTITY_'+k)
        with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as stream:
            need(fp(os.fstat(stream.fileno()))==fp(before),'OPEN_RACE')
            raw=stream.read(rec['bytes']+1);after=os.fstat(stream.fileno())
        need(len(raw)==rec['bytes'] and fp(before)==fp(after)==fp(path.lstat()) and digest(raw)==rec['sha256'],'INPUT_SHA_OR_STABILITY')
        append(self.out,'source_and_call_ledger.jsonl',{'operation':'SOURCE_COMPLETE','category':category,'path':token,'bytes':len(raw),'sha256':digest(raw),'fingerprint':fp(before)})
        self.cache[token]=(rec['sha256'],raw)
        return raw

def project_json(text,allowed):
    """Parse structure and permitted scalar leaves; skip all other scalar values."""
    decoder=json.JSONDecoder();n=len(text)
    def ws(i):
        while i<n and text[i].isspace(): i+=1
        return i
    def skip_scalar(i):
        if text[i]=='"':
            j=i+1
            while j<n:
                if text[j]=='"': return j+1
                j+=2 if text[j]=='\\' else 1
            raise Stop('JSON_STRING')
        m=NUMBER.match(text,i)
        if m:return m.end()
        for token in ('true','false','null'):
            if text.startswith(token,i):return i+len(token)
        raise Stop('JSON_VALUE')
    def value(i,permit=False):
        i=ws(i);need(i<n,'JSON_END')
        if text[i]=='{':
            obj={};names=set();i=ws(i+1)
            while i<n and text[i]!='}':
                name,i=decoder.raw_decode(text,i);need(isinstance(name,str) and name not in names,'JSON_KEY');names.add(name)
                i=ws(i);need(text[i]==':','JSON_COLON')
                v,i=value(i+1,name in allowed)
                if v is not SKIP:obj[name]=v
                i=ws(i)
                if text[i]==',':i=ws(i+1)
                else:need(text[i]=='}','JSON_COMMA')
            need(i<n,'JSON_OBJECT_END');return obj,i+1
        if text[i]=='[':
            arr=[];i=ws(i+1)
            while i<n and text[i]!=']':
                v,i=value(i,permit)
                if v is not SKIP:arr.append(v)
                i=ws(i)
                if text[i]==',':i=ws(i+1)
                else:need(text[i]==']','JSON_ARRAY_COMMA')
            need(i<n,'JSON_ARRAY_END');return arr,i+1
        if permit:return decoder.raw_decode(text,i)
        return SKIP,skip_scalar(i)
    SKIP=object();v,i=value(0);need(ws(i)==n,'JSON_TRAILING');return v

def bound_setup(reader):
    lock=json.loads((P/'implementation_seal.json').read_text())
    controls={}
    for d in lock['artifacts']:
        b=reader.read(d,'package');controls[d['path']]=b
    plan=json.loads(controls[str(P/'execution_plan.json')])
    auth=json.loads(controls[str(P/'authorization.json')])
    need(auth['FR00_execution_authorized'] and auth['FR10_execution_authorized'] and not auth['FR30_authorized'],'AUTHORITY')
    need(sys.version.split()[0]==plan['runtime']['python'],'PYTHON_VERSION')
    for name,version in plan['runtime']['packages'].items():need(importlib.metadata.version(name)==version,'DEPENDENCY_VERSION_'+name)
    binpath=Path(plan['runtime']['highs_binary']);b=binpath.read_bytes()
    need(len(b)==plan['runtime']['highs_binary_bytes'] and digest(b)==plan['runtime']['highs_binary_sha256'],'HIGHS_BUILD')
    need(all(os.environ.get(k)==v for k,v in plan['runtime']['environment'].items()),'THREAD_ENVIRONMENT')
    # Package artifacts are small controls, loaded once and retained.
    newseal=json.loads(controls[str(C/'package_seal.json')])
    for d in newseal['artifacts']:controls[d['path']]=reader.read(d,'package')
    plan['_bound_spec']=json.loads(controls[str(C/'metadata_and_calibration_spec.json')])
    contract=json.loads(controls[str(C/'contract.json')]);need(contract['contract_id']=='SGC-FR00-FR10-v0.2-V08-FROZEN-PREDICTION-REUSE','CONTRACT_ID')
    addendum=json.loads(controls[str(P/'numeric_addendum_v0.3.json')]);need(addendum['contract_id']=='SGC-FR00-FR10-v0.3-NUMERIC-REPAIR-ONCE','ADDENDUM_ID')
    repair=json.loads(controls[str(P/'solver_budget_addendum.json')]);need(repair['contract_id']==CID,'SOLVER_BUDGET_REPAIR_ID')
    plan['_bound_spec']['FR10']['LP']['maxiter']=300000
    plan['_bound_spec']['FR10']['LP']['budget_amendment']='Explicit v0.5 iteration/time budget, unchanged numerical protocol v0.3'
    plan['_bound_spec']['FR10']['LP']['tie']['preservation']='sum(r)<=n*v0; conservative exact-primary numerical addendum v0.3'
    plan['_bound_spec']['FR10']['LP']['matrix']='Sparse float64; loss-face expressed as sum(r)<=n*v0, all input/coefficient scales unchanged'
    oldseal=json.loads(reader.read(contract['parent_package_seal'],'binding'))
    old={}
    for name in ('input_allowlist.json','asset_bindings.json','preparation_sources.json'):
        rec=next(d for d in oldseal['artifacts'] if d['path']==str(OLD/name))
        old[name]=json.loads(reader.read(rec,'binding'))
    allow=old['input_allowlist.json'];assets=old['asset_bindings.json']
    closed=json.loads(controls[str(P/'closed_inputs.json')])
    need(closed['prediction_sources']==allow['FR10']['prediction_archives'] and
         closed['truth_sources']==allow['FR10']['truth_files'] and closed['cases']==assets['cases'] and
         closed['metadata']==allow['FR00']['identity_metadata'],'EXPANDED_INPUT_BINDING')
    ext={}
    for d in old['preparation_sources.json']['records']:
        ext[d['path']]=reader.read(d,'control')
    previous=plan['continuation']['previous_controls']
    prior_seal=json.loads(reader.read(previous['run_seal'],'prior_FR10_control'))
    need(prior_seal['status']=='STOPPED' and prior_seal['contract_id']=='SGC-FR00-FR10-v0.3-NUMERIC-REPAIR-ONCE','PRIOR_NOT_STOPPED')
    prior_records={d['path']:d for d in prior_seal['artifacts']}
    for name,rec in previous.items():
        if name!='run_seal':
            need(rec==prior_records.get(rec['path']),'PRIOR_OUTPUT_DESCRIPTOR_CONFLICT')
    wait=json.loads(reader.read(previous['supervisor_exit_receipt'],'prior_FR10_control'))
    stopped=json.loads(reader.read(previous['execution_receipt'],'prior_FR10_control'))
    need(stopped['status']=='STOPPED' and stopped.get('reason',{}).get('code')=='SOLVER_NOT_OPTIMAL','PRIOR_DIAGNOSED_FAILURE')
    need(not wait['stage_complete'] and wait['actual_worker_wait_exit']==1 and wait['actual_watchdog_wait_exit']==0,'PRIOR_ACTUAL_EXIT')
    diagnostic=plan['continuation']['diagnostic_controls']
    ds=json.loads(reader.read(diagnostic['run_seal'],'prior_FR10_control'))
    need(ds['status']=='COMPLETE' and ds['contract_id']=='SGC-FR10-SOLVER-DIAG-v0.1','DIAGNOSTIC_SEAL')
    dr={v['path']:v for v in ds['artifacts']}
    for name,rec in diagnostic.items():
        if name!='run_seal':need(rec==dr.get(rec['path']),'DIAGNOSTIC_DESCRIPTOR')
    dd=json.loads(reader.read(diagnostic['diagnostic_receipt'],'prior_FR10_control'))
    dw=json.loads(reader.read(diagnostic['supervisor_exit_receipt'],'prior_FR10_control'))
    de=json.loads(reader.read(diagnostic['execution_receipt'],'prior_FR10_control'))
    dc=json.loads(reader.read(diagnostic['call_budget_receipt'],'prior_FR10_control'))
    need(dd['res_status']==1 and dd['res_nit']==100000 and 'Iteration limit reached' in dd['res_message'],'DIAGNOSIS_NOT_ITERATION_LIMIT')
    need(dd['GATE_decoded']==0 and not dd['calibration_accepted'] and dw['actual_worker_wait_exit']==dw['actual_watchdog_wait_exit']==0,'DIAGNOSTIC_ACCEPTANCE')
    need(dc['actual']==plan['recovery']['historical_calls'] and de['source_spending']==plan['recovery']['historical_source_spending'],'HISTORICAL_BUDGET_CARRY')
    log=[json.loads(line) for line in reader.read(previous['source_and_call_ledger'],'prior_FR10_control').decode().splitlines()]
    need(not any(v.get('operation')=='decoded_members' and v.get('member','').startswith('MODEL_GATE') for v in log),'PRIOR_GATE_EXPOSED')
    need(not any(v.get('operation')=='SOURCE_RESERVE' and v.get('descriptor',{}).get('id')=='Y-GATE' for v in log),'PRIOR_GATE_SOURCE_OPENED')
    inherited_log=[json.loads(line) for line in reader.read(previous['calibration_transaction_ledger'],'prior_FR10_control').decode().splitlines()]
    actual_slots=[v['slot'] for v in inherited_log if v.get('event')=='CALIBRATOR_SLOT_SEAL']
    resume=json.loads(controls[str(P/'inherited_calibrators.json')])
    need(actual_slots==resume['slots'] and len(actual_slots)<40,'INHERITED_SLOT_PROVENANCE')
    return plan,allow,assets,ext

def artifact_records(tree):
    result=[]
    if isinstance(tree,dict):
        if {'path','bytes','sha256'}<=set(tree):result.append(tree)
        for v in tree.values():result.extend(artifact_records(v))
    elif isinstance(tree,list):
        for v in tree:result.extend(artifact_records(v))
    return result

def load_identity(reader,allow):
    roles={'MODEL_FIT':[],'MODEL_SELECT':[],'MODEL_GATE':[]};seen=set();pair_roles={}
    for d in allow['FR00']['identity_metadata']:
        raw=reader.read(d,'identity');lines=raw.decode('utf8').splitlines();need(len(lines)==d['rows'],'IDENTITY_ROW_COUNT')
        for line in lines:
            row=project_json(line,IDENTITY);need(set(d['required_fields'])<=set(row),'IDENTITY_FIELDS')
            need(all(type(row[k]) is str and row[k] for k in d['required_fields']),'IDENTITY_STRING')
            role=row['v3_3_role'];need(role in roles and ((d['id']=='M-GATE')==(role=='MODEL_GATE')),'ROLE_SOURCE')
            need(row['endpoint'] in ('Kd','Ki') and rowkey(row) not in seen,'ENDPOINT_OR_DUPLICATE')
            for k in ('smiles_sha256','target_sequence_key'):need(re.fullmatch('[0-9a-f]{64}',row[k]) is not None,'SOURCE_HASH')
            pk=row['pair_group_id'];need(pair_roles.get(pk,role)==role,'CROSS_ROLE_PAIR');pair_roles[pk]=role
            seen.add(rowkey(row));roles[role].append(row)
        del lines
    need({k:len(v) for k,v in roles.items()}=={'MODEL_FIT':46140,'MODEL_SELECT':5125,'MODEL_GATE':5697},'ROLE_COUNTS')
    roles['MODEL_FIT'].sort(key=utfkey);roles['MODEL_SELECT'].sort(key=utfkey)
    need(all(len({rowkey(r) for r in rows})==len(rows) for rows in roles.values()),'ROW_ORDER_DUPLICATE')
    return roles

def fr00(out,reader,guard,allow,assets,ext):
    import numpy as np
    guard.phase='FR00_METADATA_IDENTITY'
    roles=load_identity(reader,allow)
    d=allow['FR00']['handoff_control'];raw=reader.read(d,'handoff')
    manifest=project_json(raw.decode('utf8'),set(d['allowed_leaf_fields']))
    records=artifact_records(manifest);by={}
    for rec in records:
        path=Path(rec['path']);path=path if path.is_absolute() else ROOT/path
        by.setdefault(str(path),[]).append(rec)
    inherited_receipt=json.loads(ext[str(ROOT/'protocol/smiles_gine_bottleneck_h3_r1_freeze_handoff_v0_1/preparation_receipt.json')])
    need(inherited_receipt['core_asset_files']==40 and inherited_receipt['model_configurations']==30,'INHERITED_HANDOFF_COUNTS')
    corroborated=[]
    for case in assets['cases']:
        for name in ('baseline_predictions','frozen_cache_reference_only','high_predictions','high_head_reference_only'):
            rec=case[name];matches=by.get(rec['path'],[])
            if not matches and name in ('baseline_predictions','high_predictions'):
                sealrec=case['old_case_seal' if name=='baseline_predictions' else 'high_case_seal']
                matches=by.get(sealrec['path'],[])
                need(any(v['sha256']==sealrec['sha256'] and v['bytes']==sealrec['bytes'] for v in matches),'HANDOFF_CASE_SEAL_LINK_'+case['case_id'])
            else:
                need(any(v['sha256']==rec['sha256'] and v['bytes']==rec['bytes'] for v in matches),'HANDOFF_ASSET_LINK_'+case['case_id']+'_'+name)
            corroborated.append({'case_id':case['case_id'],'kind':name,'path':rec['path'],'sha256':rec['sha256']})
        for sealname,fields in [('old_case_seal',('baseline_predictions','frozen_cache_reference_only')),('high_case_seal',('high_predictions','high_head_reference_only'))]:
            seal=json.loads(ext[case[sealname]['path']]);rr=artifact_records(seal)
            for name in fields:
                rec=case[name]
                need(any(str(ROOT/v['path'])==rec['path'] and v['sha256']==rec['sha256'] and v['bytes']==rec['bytes'] for v in rr),'CASE_SELF_SEAL_LINK_'+case['case_id']+'_'+name)
    need(len(corroborated)==40,'FORTY_ASSET_LINKS')
    order=put(out,'identity_order_binding.json',{'roles':roles,'FIT_order':'canonical UTF8 triple','GATE_order':'frozen B0 JSONL order','source_identity':'O_SRC_METADATA_LINKED'})
    put(out,'input_identity_receipt.json',{'status':'QUALIFIED_SCALAR_IDENTITY_ORDER','role_counts':{k:len(v) for k,v in roles.items()},'endpoint_counts':{k:dict(collections.Counter(r['endpoint'] for r in v)) for k,v in roles.items()},'row_order':order,'payload_stat_hash_opens':0})
    put(out,'asset_projection_receipt.json',{'status':'METADATA_LINKED','corroborated_required_assets':corroborated,'inherited_core_asset_files':40,'inherited_model_configurations':30,'count_evidence':'Unchanged sealed handoff preparation receipt, not a new model reconstruction','manifest_root_structure':{k:type(v).__name__ for k,v in manifest.items()},'encoded_identity':'NOT_ASSESSED_NOT_REQUIRED_FOR_SOURCE_SCOPE','payload_target_opens':0})
    guard.phase='FR00_COMPLETE_CENSUS'
    fit,gate=roles['MODEL_FIT'],roles['MODEL_GATE'];edges=set((r['smiles_sha256'],r['target_sequence_key'],r['endpoint']) for r in fit+gate)
    drugs=sorted({r['smiles_sha256'] for r in fit+gate});proteins=sorted({r['target_sequence_key'] for r in fit+gate})
    di={v:i for i,v in enumerate(drugs)};pi={v:i for i,v in enumerate(proteins)}
    fd=np.array([di[r['smiles_sha256']] for r in fit]);fp_=np.array([pi[r['target_sequence_key']] for r in fit])
    fe=np.array([r['endpoint'] for r in fit]);reuse=np.zeros(len(fit),dtype=np.int64);weights=np.zeros(len(fit),dtype=np.float64)
    drug_weight=collections.Counter();protein_weight=collections.Counter();source_counts=[];eligibility=[];allcounts=collections.Counter();endpointcounts={}
    visits=0
    for ordinal,i in enumerate(sorted(range(len(gate)),key=lambda j:utfkey(gate[j]))):
        src=gate[i];D,P_,e=src['smiles_sha256'],src['target_sequence_key'],src['endpoint'];same=fe==e
        nondeg=same&(fd!=di[D])&(fp_!=pi[P_]);cross_d={di[d] for d,p,ep in edges if p==P_ and ep==e};cross_p={pi[p] for d,p,ep in edges if d==D and ep==e}
        ok=nondeg&np.isin(fd,list(cross_d))&np.isin(fp_,list(cross_p))
        idx=np.flatnonzero(ok);n=int(len(idx));nd=int(nondeg.sum());deg=int(same.sum())-nd
        visits+=len(fit);need(visits<=262859580,'CENSUS_PAIR_BUDGET')
        count={'gate_row_index0':i,'endpoint':e,'eligible_O_reference_rows':n,'C_candidate_rows':nd-n,'degenerate_rows':deg,'U_candidate_rows':0}
        source_counts.append(count);allcounts.update(O_quadruples=n,C_quadruples=nd-n,degenerate_quadruples=deg,source_rows=1,O_source_rows=int(n>0))
        ec=endpointcounts.setdefault(e,collections.Counter());ec.update(O_quadruples=n,C_quadruples=nd-n,degenerate_quadruples=deg,source_rows=1,O_source_rows=int(n>0))
        if n:
            reuse[idx]+=1;weights[idx]+=1.0/n
            for j in idx:drug_weight[fit[int(j)]['smiles_sha256']]+=1.0/n;protein_weight[fit[int(j)]['target_sequence_key']]+=1.0/n
        eligibility.append({'gate_row_index0':i,'FIT_reference_rows':idx.tolist(),'each_row_weight':1.0/n if n else None})
        if ordinal%50==0:guard.check()
    def composition(rows):
        dc=collections.Counter(v['smiles_sha256'] for v in rows);pc=collections.Counter(v['target_sequence_key'] for v in rows);n=len(rows)
        return {'rows':n,'drug_source_count':len(dc),'protein_source_count':len(pc),'drug_frequency':dict(dc),'protein_frequency':dict(pc),
                'drug_max_row_share':max(dc.values(),default=0)/n if n else None,'protein_max_row_share':max(pc.values(),default=0)/n if n else None}
    eligible={c['gate_row_index0'] for c in source_counts if c['eligible_O_reference_rows']>0}
    comp={e:{'all_GATE':composition([v for v in gate if v['endpoint']==e]),'O_SRC_GATE':composition([v for j,v in enumerate(gate) if j in eligible and v['endpoint']==e]),'FIT':composition([v for v in fit if v['endpoint']==e])} for e in ('Kd','Ki')}
    put(out,'source_support_counts.json',{'O_label':'O_SRC_METADATA_LINKED','totals':dict(allcounts),'by_endpoint':{e:dict(v) for e,v in endpointcounts.items()},'per_source':source_counts,'logical_visits':visits,'complete':True,'truncated':False})
    put(out,'coverage_composition.json',{'composition':comp,'common_analysis_domain':None,'main_sensitivity_schemes':'NOT_FROZEN','no_concentration_PASS':True,'encoded_identity_not_proven':True,'source_assay_family_independence':'UNRESOLVED'})
    put(out,'reference_donor_reuse.json',{'FIT_reference_row_reuse':reuse.tolist(),'FIT_reference_row_cumulative_normalized_weight':weights.tolist(),'donor_equals_reference_row':True,'unique_used_reference_source_edges':len({(fit[i]['smiles_sha256'],fit[i]['target_sequence_key'],fit[i]['endpoint']) for i in np.flatnonzero(reuse)}),'drug_weight':dict(drug_weight),'protein_weight':dict(protein_weight),'max_reference_reuse':int(reuse.max(initial=0)),'max_reference_cumulative_weight':float(weights.max(initial=0))})
    put(out,'census_rule_and_weights.json',{'name':'R_i_census','rule':'Uniform over eligible FIT reference rows, multiplicity retained','eligibility':eligibility,'R_i_analysis':'NOT_FROZEN_NOT_AUTHORIZED','head_queries':0})
    put(out,'branch_readiness.json',{'FR10_scalar_identity':'QUALIFIED','O_by_endpoint':{e:'AVAILABLE_METADATA_ONLY' if endpointcounts[e]['O_source_rows'] else 'NOT_READY_ZERO_O' for e in ('Kd','Ki')},'FR30_release':False,'coverage_or_precision_PASS':False,'scientific_PASS':False})
    guard.check(True)
    return {'status':'COMPLETE_FR00_METADATA_CENSUS','source_rows':5697,'FIT_reference_rows':46140,'support':{e:dict(v) for e,v in endpointcounts.items()},'identity_order':order,'model_calls':0,'original_project_reads':0,'scientific_PASS':False}

class Projection:
    """Read exact allowed parquet chunks from one verified opaque input buffer."""
    def __init__(self,raw,allowed,n):
        import pyarrow.parquet as pq
        need(raw[:4]==raw[-4:]==b'PAR1','PARQUET_MAGIC')
        footer=struct.unpack('<I',raw[-8:-4])[0];start=len(raw)-8-footer
        need(4<=start<len(raw)-8,'PARQUET_FOOTER')
        meta=pq.read_metadata(io.BytesIO(b'PAR1'+raw[start:]))
        need(meta.num_rows==n and set(allowed)<=set(meta.schema.names),'PARQUET_SCHEMA_ROWS')
        chunks=[]
        for gi in range(meta.num_row_groups):
            group=meta.row_group(gi)
            for ci in range(group.num_columns):
                col=group.column(ci);offsets=[v for v in (col.dictionary_page_offset,col.data_page_offset) if v is not None and v>0]
                need(bool(offsets),'PARQUET_OFFSET');lo=min(offsets);hi=lo+col.total_compressed_size
                need(4<=lo<hi<=start,'PARQUET_COLUMN_RANGE');chunks.append((col.path_in_schema,lo,hi))
        for name,lo,hi in chunks:
            if name in allowed:need(all(other==name or max(lo,a)>=min(hi,b) for other,a,b in chunks),'PARQUET_COLUMN_OVERLAP')
        self.mask=bytearray(len(raw));self.mask[:4]=b'PAR1';self.mask[start:]=raw[start:]
        self.raw,self.allowed,self.n,self.chunks,self.done=raw,set(allowed),n,chunks,set()
    def read(self,columns):
        import pyarrow.parquet as pq
        need(set(columns)<=self.allowed and not self.done.intersection(columns),'COLUMN_AUTHORITY_OR_REPEAT')
        for name,lo,hi in self.chunks:
            if name in columns:self.mask[lo:hi]=self.raw[lo:hi]
        table=pq.ParquetFile(io.BytesIO(self.mask)).read(columns=columns,use_threads=False)
        need(table.num_rows==self.n,'PARQUET_COLUMN_ROWS');self.done.update(columns)
        return {k:table[k].to_pylist() for k in columns}

class Calls:
    def __init__(self,out,guard):
        self.out=out;self.used=collections.Counter(guard.claim.get('historical_calls',{}));guard.calls=self
        self.caps={'calibration_slots':41,'solver_calls':240,'knot_groups':20,
                   'decoded_members':40,'decoded_prediction_scalars':1036740,
                   'FIT_validation_slots':41,'FIT_validation_scalars':929256,
                   'GATE_calibration_scalars':113940,'GATE_risk_scalars':227880,'finite_report':1}
        self.caps=guard.claim['cumulative_call_caps']
        append(out,'source_and_call_ledger.jsonl',{'operation':'RESERVE_REMAINING_SLOTS_WITH_HISTORY','caps':self.caps,'historical':dict(self.used),'no_refund_retry':True})
    def spend(self,name,n=1,**detail):
        self.used[name]+=n;need(name in self.caps and self.used[name]<=self.caps[name],'CALL_BUDGET_'+name)
        append(self.out,'source_and_call_ledger.jsonl',{'operation':name,'n':n,'used':dict(self.used),**detail})
    def snapshot(self):return {'reserved':self.caps,'actual':dict(self.used),'unused_reservations_not_refunded':True,'model_calls':0,'CI_p_bootstrap':0}

def decode_member(raw,rec,name,calls):
    import numpy as np
    need(name in rec['decode_members'],'MEMBER_AUTHORITY')
    spec=rec['decode_members'][name]
    calls.spend('decoded_members',path=rec['path'],member=name)
    calls.spend('decoded_prediction_scalars',math.prod(spec['shape']))
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=z.namelist();need(len(names)==len(set(names)) and set(names)==set(rec['decode_members'])|set(rec['skip_members']),'NPZ_MEMBER_SCHEMA')
        need(z.getinfo(name).file_size<=math.prod(spec['shape'])*4+1024,'NPY_SIZE_BOUND')
        b=z.read(name)
    a=np.load(io.BytesIO(b),allow_pickle=False)
    need(a.shape==tuple(spec['shape']) and a.dtype==np.dtype('float32') and bool(np.isfinite(a).all()),'PREDICTION_SHAPE_DTYPE_FINITE')
    return a.astype(np.float64)

def calibrate(out,guard,calls,t,y,case,e,family,affine=None):
    import numpy as np
    import scipy.sparse as sp
    from scipy.optimize import linprog,OptimizeWarning
    import warnings
    calls.spend('calibration_slots',case=case,endpoint=e,family=family)
    n=len(t);constant=bool(np.all(t==t[0]));knots=[];reuse=None
    if family=='piecewise':
        calls.spend('knot_groups')
        if not constant:
            values=np.sort(t)
            knots=sorted(set(float(values[(n*q+3)//4-1]) for q in (1,2,3))-{float(values[0]),float(values[-1])})
    constant_b=float(np.sort(y)[(n-1)//2]) if constant else None
    primary=None;solver_records=[]
    if constant:
        coef=np.array([0.,constant_b]);knots=[];mode='CONSTANT_LOWER_MEDIAN'
    elif family=='piecewise' and not knots:
        need(affine is not None,'AFFINE_REUSE_UNSEALED')
        coef=np.array(affine['coefficients'],dtype=np.float64);primary=affine['primary_loss'];reuse=affine['slot_sha256'];mode='REUSE_SEALED_AFFINE'
    else:
        mode='SOLVED_FIXED_LP'
        X=np.column_stack([t,np.ones(n)]+[np.maximum(t-k,0.) for k in knots]);p=X.shape[1]
        I=sp.eye(n,format='csc');Xs=sp.csc_matrix(X)
        A=sp.vstack([sp.hstack([Xs,-I]),sp.hstack([-Xs,-I])],format='csc');bb=np.concatenate([y,-y])
        if knots:
            mono=np.zeros((len(knots)+1,p+n),dtype=np.float64)
            for j in range(len(knots)+1):mono[j,0]=-1.;mono[j,2:2+j]=-1.
            A=sp.vstack([A,sp.csc_matrix(mono)],format='csc');bb=np.concatenate([bb,np.zeros(len(knots)+1)])
        bounds=[(0.,None),(None,None)]+[(None,None)]*len(knots)+[(0.,None)]*n
        obj=np.r_[np.zeros(p),np.full(n,1./n)]
        E=None;eb=None;fixed=[]
        for step in range(p+1):
            guard.phase='FR10_'+case+'_'+e+'_'+family+'_LP_'+str(step);guard.check()
            need(guard.iteration_charge+300000<=guard.claim['iteration_charge_max'],'ITERATION_BUDGET_RESERVATION')
            calls.spend('solver_calls',case=case,endpoint=e,family=family,tie_step=step)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                res=linprog(obj,A_ub=A,b_ub=bb,A_eq=E,b_eq=eb,bounds=bounds,method='highs-ds',
                    options={'presolve':True,'primal_feasibility_tolerance':1e-8,'dual_feasibility_tolerance':1e-8,
                             'maxiter':300000,'simplex_dual_edge_weight_strategy':'steepest-devex','threads':1})
            for warning in caught:
                need(issubclass(warning.category,OptimizeWarning) and 'threads' in str(warning.message) and 'passed to HiGHS verbatim' in str(warning.message),'UNEXPECTED_SOLVER_WARNING')
            nit=getattr(res,'nit',None)
            charged=int(nit) if nit is not None and 0<=int(nit)<=300000 else 300000
            guard.iteration_charge+=charged
            append(out,'solver_diagnostics.jsonl',{'case':case,'endpoint':e,'family':family,'step':step,
                   'success':bool(res.success),'status':int(res.status),'message':str(res.message),'nit':nit,
                   'cumulative_iteration_charge':guard.iteration_charge,'per_call_maxiter':300000})
            need(res.success and res.status==0 and res.x is not None,'SOLVER_NOT_OPTIMAL')
            need(bool(np.isfinite(res.x).all()) and res.nit<=300000,'SOLVER_NONFINITE_ITERATION')
            need(float(np.min(bb-A@res.x))>=-1e-8,'SOLVER_PRIMAL_FEASIBILITY')
            if E is not None:need(float(np.max(np.abs(E@res.x-eb)))<=1e-8,'SOLVER_TIE_EQUALITY')
            need(res.x[0]>=-1e-8 and float(np.min(res.x[p:]))>=-1e-8,'SOLVER_BOUNDS')
            if step==0:
                primary=float(res.fun)
                face=sp.csc_matrix(np.r_[np.zeros(p),np.ones(n)].reshape(1,-1))
                A=sp.vstack([A,face],format='csc');bb=np.r_[bb,n*primary]
            else:
                fixed.append((step-1,float(res.x[step-1])))
                E=sp.csc_matrix(([1.]*len(fixed),(range(len(fixed)),[j for j,v in fixed])),shape=(len(fixed),p+n))
                eb=np.array([v for j,v in fixed],dtype=np.float64)
            solver_records.append({'step':step,'optimal':True,'objective':float(res.fun),'nit':int(res.nit),'numeric_tolerance':1e-8})
            append(out,'calibration_transaction_ledger.jsonl',{'case':case,'endpoint':e,'family':family,'solver':solver_records[-1]})
            if step<p:
                obj=np.zeros(p+n,dtype=np.float64);obj[step]=1.
        coef=res.x[:p].copy()
    # Exactly one final FIT calibrated prediction/loss pass per reserved slot.
    calls.spend('FIT_validation_slots')
    calls.spend('FIT_validation_scalars',n)
    pred=coef[0]*t+coef[1]
    for theta,k in zip(coef[2:],knots):pred+=theta*np.maximum(t-k,0.)
    need(bool(np.isfinite(pred).all()),'FIT_VALIDATION_FINITE')
    mae=float(np.mean(np.abs(y-pred),dtype=np.float64))
    if primary is not None:append(out,'calibration_transaction_ledger.jsonl',{'event':'FINAL_FIT_NUMERIC_DIAGNOSTIC','case':case,'endpoint':e,'family':family,'primary':primary,'actual_MAE':mae,'difference':None if primary is None else mae-primary,'tolerance':1e-8,'coefficients':coef.tolist(),'knots':knots})
    if primary is not None:need(abs(mae-primary)<=1e-8,'FINAL_FIT_OBJECTIVE_PRESERVATION')
    need(bool(np.all(np.cumsum(np.r_[coef[0],coef[2:]])>=-1e-8)),'FINAL_MONOTONE_SLOPES')
    record={'case_id':case,'endpoint':e,'family':family,'mode':mode,'knots':knots,'coefficients':coef.tolist(),'primary_loss':primary,'FIT_final_MAE':mae,'FIT_rows':n,'LP_calls':len(solver_records),'solver_records':solver_records,'reuse_slot_sha256':reuse,'dtype':'float64'}
    record['slot_sha256']=digest(encoded(record))
    append(out,'calibration_transaction_ledger.jsonl',{'event':'CALIBRATOR_SLOT_SEAL','slot':record})
    return record

def apply_calibrator(t,record):
    import numpy as np
    coef=record['coefficients'];pred=coef[0]*t+coef[1]
    for theta,k in zip(coef[2:],record['knots']):pred+=theta*np.maximum(t-k,0.)
    need(bool(np.isfinite(pred).all()),'GATE_CALIBRATION_FINITE')
    return pred

def fr10(out,reader,guard,allow,assets,ext,plan):
    import numpy as np
    prior=Path(plan['stages']['FR00'])
    admission=json.loads(reader.read(guard.claim['FR00_admission_binding'],'package'))
    s=json.loads(reader.read(admission['run_seal'],'FR00_output'))
    need(s['status']=='COMPLETE','FR00_FINAL_SEAL_NOT_COMPLETE')
    receipt_rec=next(d for d in s['artifacts'] if d['path']==str(prior/'execution_receipt.json'))
    receipt=json.loads(reader.read(receipt_rec,'FR00_output'))
    need(receipt['status']=='COMPLETE_FR00_METADATA_CENSUS','FR00_NOT_ACCEPTED')
    order_rec=next(d for d in s['artifacts'] if d['path']==str(prior/'identity_order_binding.json'))
    order=json.loads(reader.read(order_rec,'FR00_output'));roles=order['roles']
    fit,gate=roles['MODEL_FIT'],roles['MODEL_GATE'];calls=Calls(out,guard)
    # Join only frozen identity triples; FIT labels of non-FIT rows are immediately discarded.
    guard.phase='FR10_FIT_PROJECTION'
    d=allow['FR10']['truth_files'][0];raw=reader.read(d,'source')
    proj=Projection(raw,d['columns'],51265);ids=proj.read(list(IDS));keys=list(zip(*(ids[k] for k in IDS)));del ids
    rolemap={rowkey(v):role for role,rows in roles.items() for v in rows if role!='MODEL_GATE'}
    need(len(keys)==len(set(keys))==51265 and set(keys)==set(rolemap),'FIT_SOURCE_IDENTITY_JOIN')
    column=proj.read(['p_affinity_median'])['p_affinity_median']
    mapping={}
    for i,k in enumerate(keys):
        if rolemap[k]!='MODEL_FIT':
            column[i]=None
            continue
        v=column[i];need(type(v) in (int,float) and math.isfinite(v),'FIT_LABEL_FINITE')
        mapping[k]=float(v)
    del column,proj,raw,keys,rolemap
    y=np.array([mapping[rowkey(v)] for v in fit],dtype=np.float64);del mapping
    ends=np.array([v['endpoint'] for v in fit]);put(out,'FIT_projection_receipt.json',{'FIT_rows_used':46140,'SELECT_physical_labels_decoded_and_discarded':5125,'columns':d['columns'],'SELECT_values_logged_or_used':0})
    resume_doc=json.loads(reader.read(plan['continuation']['inherited_calibrators'],'package'))
    inherited={}
    for record in resume_doc['slots']:
        check=dict(record);expected=check.pop('slot_sha256');need(digest(encoded(check))==expected,'INHERITED_SLOT_SHA')
        token=(record['case_id'],record['endpoint'],record['family']);need(token not in inherited,'INHERITED_SLOT_DUPLICATE')
        need(record['FIT_rows']==(6456 if record['endpoint']=='Kd' else 39684),'INHERITED_SLOT_ROWS')
        if record['primary_loss'] is not None:need(abs(record['FIT_final_MAE']-record['primary_loss'])<=1e-8,'INHERITED_NUMERIC_VALIDATION')
        inherited[token]=record
    need(len(inherited)==plan['continuation']['inherited_slots'],'INHERITED_SLOT_COUNT')
    buffers={};cal=[];members={};bycase={}
    for rec in allow['FR10']['prediction_archives']:
        if rec['purpose']=='h0':
            raw=reader.read(rec,'prediction');buffers[rec['case_id']]=(rec,raw)
            name=rec['case_id'];bycase[name]=rec
            if any((name,e,family) not in inherited for e in ('Kd','Ki') for family in ('affine','piecewise')):
                members[name]=decode_member(raw,rec,'MODEL_FIT_old.npy',calls)
    guard.phase='FR10_FIT_CALIBRATION'
    for case in assets['cases']:
        name=case['case_id'];t=members.pop(name,None)
        for e in ('Kd','Ki'):
            mask=ends==e;tt=None if t is None else t[mask];yy=y[mask]
            aff=inherited.get((name,e,'affine'))
            if aff is None:aff=calibrate(out,guard,calls,tt,yy,name,e,'affine')
            pw=inherited.get((name,e,'piecewise'))
            if pw is None:pw=calibrate(out,guard,calls,tt,yy,name,e,'piecewise',aff)
            cal.extend([aff,pw])
    need(len(cal)==40 and calls.used['FIT_validation_scalars']==calls.caps['FIT_validation_scalars'],'FORTY_CALIBRATORS')
    settings=put(out,'frozen_calibration_settings.json',{'spec':plan['_bound_spec']['FR10'],'runtime':plan['runtime'],'practical_margin':None})
    parameters=put(out,'calibrators.json',{'slots':cal,'slot_count':40,'scientific_PASS':False})
    validation=put(out,'FIT_final_validation_receipt.json',{'endpoint_slots':40,'logical_full_case_family_vectors':20,'new_endpoint_slots':plan['continuation']['new_FIT_validation_slots'],'cumulative_attempted_validation_slots':calls.used['FIT_validation_slots'],'scalar_predictions_errors_current':plan['continuation']['new_FIT_validation_scalars'],'scalar_predictions_errors_cumulative':calls.used['FIT_validation_scalars'],'historical_inherited_slots':len(inherited),'all_numeric_checks_passed':True,'numeric_tolerance':1e-8,'practical_margin':None})
    cal_seal=put(out,'calibrator_seal.json',{'artifacts':[settings,parameters,validation],'all_40_slots_complete':True,'before_GATE_decode':True,'no_more_fit':True})
    guard.phase='FR10_GATE_PROJECTION'
    d=allow['FR10']['truth_files'][1];raw=reader.read(d,'source')
    proj=Projection(raw,d['columns'],5697);ids=proj.read(list(IDS));keys=list(zip(*(ids[k] for k in IDS)));del ids
    need(len(keys)==len(set(keys))==5697 and set(keys)=={rowkey(v) for v in gate},'GATE_IDENTITY_JOIN')
    vals=proj.read(['y_true'])['y_true'];need(all(type(v) in (int,float) and math.isfinite(v) for v in vals),'GATE_TRUTH_FINITE')
    mapping=dict(zip(keys,vals));truth=np.array([mapping[rowkey(v)] for v in gate],dtype=np.float64)
    del vals,proj,raw,keys,mapping
    ge=np.array([v['endpoint'] for v in gate]);put(out,'GATE_projection_receipt.json',{'rows':5697,'columns':d['columns'],'M1_y_pred_decoded':False,'calibrator_seal':cal_seal})
    calmap={(c['case_id'],c['endpoint'],c['family']):c for c in cal};risks={}
    for case in assets['cases']:
        name=case['case_id'];rec,raw=buffers.pop(name);old=decode_member(raw,rec,'MODEL_GATE_old.npy',calls)
        highrec=next(v for v in allow['FR10']['prediction_archives'] if v['case_id']==name and v['purpose']!='h0')
        new=decode_member(reader.read(highrec,'prediction'),highrec,'MODEL_GATE_new.npy',calls)
        predictions={'h0':old,'h1':new}
        for family,key in [('affine','h_aff'),('piecewise','h_1d')]:
            pred=np.empty(5697,dtype=np.float64)
            for e in ('Kd','Ki'):
                mask=ge==e;pred[mask]=apply_calibrator(old[mask],calmap[(name,e,family)])
            calls.spend('GATE_calibration_scalars',5697);predictions[key]=pred
        case_risks={}
        for key,pred in predictions.items():
            calls.spend('GATE_risk_scalars',5697);err=np.abs(pred-truth);ms={e:float(err[ge==e].mean()) for e in ('Kd','Ki')}
            case_risks[key]={'MAE':ms,'EBMAE':(ms['Kd']+ms['Ki'])/2}
        case_risks['J_aff']=case_risks['h_aff']['EBMAE']-case_risks['h1']['EBMAE']
        case_risks['J_1d']=case_risks['h_1d']['EBMAE']-case_risks['h1']['EBMAE']
        case_risks['endpoint_J']={e:{k:case_risks[h]['MAE'][e]-case_risks['h1']['MAE'][e] for k,h in [('aff','h_aff'),('1d','h_1d')]} for e in ('Kd','Ki')}
        risks[name]=case_risks;guard.check()
    calls.spend('finite_report')
    summary={}
    for arm in ('S','G'):
        rr=[risks[c['case_id']] for c in assets['cases'] if c['arm']==arm]
        summary[arm]={'risks':{h:float(np.mean([v[h]['EBMAE'] for v in rr])) for h in ('h0','h_aff','h_1d','h1')},
                      'J_aff':float(np.mean([v['J_aff'] for v in rr])),'J_1d':float(np.mean([v['J_1d'] for v in rr])),
                      'all_seed_J_aff_positive':all(v['J_aff']>0 for v in rr),'all_seed_J_1d_positive':all(v['J_1d']>0 for v in rr),
                      'all_seed_endpoint_J_aff_positive':all(v['endpoint_J'][e]['aff']>0 for v in rr for e in ('Kd','Ki')),
                      'all_seed_endpoint_J_1d_positive':all(v['endpoint_J'][e]['1d']>0 for v in rr for e in ('Kd','Ki'))}
    put(out,'per_case_endpoint_risks.json',{'cases':risks,'no_prediction_ensemble':True,'MODEL_GATE':'EXPOSED_DEVELOPMENT_ONLY'})
    put(out,'finite_panel_comparisons.json',{'summary':summary,'J_G_minus_J_S':{k:summary['G'][k]-summary['S'][k] for k in ('J_aff','J_1d')},'primary':'J_G_aff','practical_margin':None,'CI':None,'p':None,'bootstrap':0,'scientific_PASS':False})
    put(out,'readiness_and_scope.json',{'status':'COMPLETE_DESCRIPTIVE_ONLY','FR30_release':False,'FR30_status':'NOT_ADJUDICATED','superiority_equivalence_PASS':False,'MODEL_GATE':'EXPOSED_DEVELOPMENT_ONLY','source_assay_family_independence':'UNRESOLVED','old_H1_H2_H3_R1_NP01_seals_unchanged':True})
    report=['# FR10有限开发面板结果','',
            '## Material Passport','',
            '- origin_skill: academic-research-suite / experiment-agent',
            '- verification_status: ANALYZED_SINGLE_REGISTERED_EXECUTION',
            '- scope: 已暴露MODEL_GATE，完整有限面板；非独立确认，无总体推断。',
            '- scientific_PASS: false；FR30: NOT_ADJUDICATED。',
            '',
            '| 管线 | h0 | h_aff | h_1d | h1 | J_aff | J_1d |','|---|---:|---:|---:|---:|---:|---:|']
    for arm,v in summary.items():report.append('| '+arm+' | '+' | '.join(f'{v["risks"][h]:.9f}' for h in ('h0','h_aff','h_1d','h1'))+f' | {v["J_aff"]:.9f} | {v["J_1d"]:.9f} |')
    report+=['','J为校准函数风险减去h1风险；正值表示该面板观察到h1误差更低。实用界值为null，不裁决实用优越、等效或校准充分，不追认历史病因或H3 PASS。逐seed、逐端点及退化槽位见JSON；不删除负方向。','',
             '本次只比较两条固定FIT流程所得校准器。FIT训练内校准、已知开发结果启发与来源依赖保留。无模型forward、梯度、训练或统计推断。']
    with (out/'report.md').open('x') as f:f.write('\n'.join(report)+'\n')
    guard.check(True)
    return {'status':'COMPLETE_FR10_DESCRIPTIVE_ONLY','cases':10,'calibration_slots':40,'calls':calls.snapshot(),'summary':summary,'FR30_release':False,'scientific_PASS':False}

def worker(stage,out):
    claim=json.loads((out/'attempt_claim.json').read_text());guard=Guard(out,claim)
    deadline=time.monotonic()+15.
    while not (out/'watchdog_identity.json').exists():
        need(time.monotonic()<deadline,'WATCHDOG_NOT_STARTED');time.sleep(.05)
    reader=Reader(out,guard);status=1
    try:
        plan,allow,assets,ext=bound_setup(reader)
        need(stage in ('FR00','FR10'),'STAGE')
        result=fr00(out,reader,guard,allow,assets,ext) if stage=='FR00' else fr10(out,reader,guard,allow,assets,ext,plan)
        result.update(contract_id=CID,source_spending=dict(reader.used),wall_seconds=time.time()-claim['wall_start'],
                      monotonic_seconds=time.monotonic()-claim['monotonic_start'],exit_code='PENDING_SUPERVISOR_WAIT',
                      model_calls=0,training=0,CI_p_bootstrap=0)
        result['cumulative_iteration_charge']=guard.iteration_charge
        put(out,'execution_receipt.json',result);status=0
    except BaseException as exc:
        result={'status':'STOPPED','contract_id':CID,'stage':stage,'reason':error(exc),'phase':guard.phase,
                'source_spending':dict(reader.used),'wall_seconds':time.time()-claim['wall_start'],
                'monotonic_seconds':time.monotonic()-claim['monotonic_start'],'exit_code':'PENDING_SUPERVISOR_WAIT',
                'model_calls':0,'training':0,'CI_p_bootstrap':0,'scientific_PASS':False,'FR30_release':False}
        result['cumulative_iteration_charge']=guard.iteration_charge
        if hasattr(guard,'calls'):result['calls']=guard.calls.snapshot()
        put(out,'execution_receipt.json',result);print(json.dumps(result),flush=True)
    return status

def watchdog(out):
    import psutil
    claim=json.loads((out/'attempt_claim.json').read_text())
    rec=json.loads((out/'worker_identity.json').read_text());owner=claim['supervisor']
    samples=0
    while True:
        owned_alive=live(rec);owner_alive=live(owner);samples+=1
        identities=[owner,json.loads((out/'watchdog_identity.json').read_text())]+([rec] if owned_alive else [])
        rss=0
        for item in identities:
            if live(item):rss+=psutil.Process(item['pid']).memory_info().rss
        available=psutil.virtual_memory().available;disk=shutil.disk_usage(ROOT).free
        timeout=time.time()>=claim['wall_deadline'] or time.monotonic()>=claim['monotonic_deadline']
        breach=rss>36*GIB or available<4*GIB or disk<20*GIB
        append(out,'watchdog_resources.jsonl',{'wall':time.time(),'monotonic':time.monotonic(),'owned_total_RSS_bytes':rss,
              'available_bytes':available,'disk_free_bytes':disk,'owned_MPS_driver_bytes':0,'MPS_initialized':False,
              'footprint':None,'footprint_missing_advisory_only':True,'timeout':timeout,'resource_breach':breach,
              'owner_alive_identity_match':owner_alive,'worker_alive_identity_match':owned_alive})
        if (timeout or breach or not owner_alive) and owned_alive:
            put(out,'safety_stop.json',{'timeout':timeout,'resource_breach':breach,'owner_lost':not owner_alive,'worker':rec})
            if live(rec):os.killpg(rec['PGID'],signal.SIGTERM)
            return 99
        if not owned_alive or (out/'watchdog_stop.json').exists():return 0
        time.sleep(1.)

def supervise(stage):
    need(stage=='FR10','SOLVER_BUDGET_REPAIR_FR10_ONLY_NO_FR00_RERUN')
    plan=json.loads((P/'execution_plan.json').read_text());out=Path(plan['stages'][stage])
    need(time.time()<plan['recovery']['wall_deadline'] and time.monotonic()<plan['recovery']['monotonic_deadline'],'REPAIR_DEADLINE_EXPIRED')
    out.mkdir(parents=True,exist_ok=False)
    duration=plan['wall_monotonic_seconds'][stage];wall=plan['recovery']['original_wall_start'];mono=plan['recovery']['original_monotonic_start']
    claim={'contract_id':CID,'stage':stage,'wall_start':wall,'monotonic_start':mono,
           'wall_deadline':wall+duration,'monotonic_deadline':mono+duration,
           'supervisor':identity(os.getpid()),'attempt':3,'retries':2,'retry_scope':'DIAGNOSED_ITERATION_BUDGET_REPAIR_NO_SUCCESSFUL_SLOT_RERUN','historical_source_spending':plan['recovery']['historical_source_spending'],'historical_calls':plan['recovery']['historical_calls'],'cumulative_source_caps':plan['cumulative_source_caps'],'cumulative_call_caps':plan['call_caps'],'FR30_release':False}
    claim['historical_iteration_charge']=plan['continuation']['historical_iteration_charge']
    claim['iteration_charge_max']=plan['continuation']['cumulative_iteration_charge_max']
    if stage=='FR10':claim['FR00_admission_binding']=descriptor(P/'FR10_admission_binding.json')
    put(out,'attempt_claim.json',claim)
    env=dict(os.environ);env.update(plan['runtime']['environment']);streams=[]
    for name in ('worker','watchdog'):streams.append((out/(name+'.log')).open('xb'))
    workerproc=subprocess.Popen([plan['interpreter'],'-I','-B',str(SCRIPT),'worker',stage,str(out)],cwd=ROOT,env=env,
                                start_new_session=True,stdout=streams[0],stderr=subprocess.STDOUT)
    put(out,'worker_identity.json',identity(workerproc.pid))
    watchproc=subprocess.Popen([plan['interpreter'],'-I','-B',str(SCRIPT),'watchdog',stage,str(out)],cwd=ROOT,env=env,
                               start_new_session=True,stdout=streams[1],stderr=subprocess.STDOUT)
    put(out,'watchdog_identity.json',identity(watchproc.pid))
    while workerproc.poll() is None:
        if watchproc.poll() is not None and watchproc.returncode!=0:
            rec=json.loads((out/'worker_identity.json').read_text())
            if live(rec):os.killpg(rec['PGID'],signal.SIGTERM)
        time.sleep(.25)
    exitcode=workerproc.wait()
    put(out,'watchdog_stop.json',{'actual_worker_wait_exit':exitcode})
    watch_exit=watchproc.wait(timeout=15)
    for f in streams:f.close()
    if not (out/'execution_receipt.json').exists():
        put(out,'execution_receipt.json',{'status':'STOPPED_WITHOUT_WORKER_RECEIPT','stage':stage,'worker_exit':exitcode,
              'reason':'SEE_WATCHDOG_OR_WORKER_LOG','scientific_PASS':False,'FR30_release':False})
    receipt=json.loads((out/'execution_receipt.json').read_text())
    complete=exitcode==0 and watch_exit==0 and receipt['status'].startswith('COMPLETE_')
    put(out,'supervisor_exit_receipt.json',{'actual_worker_wait_exit':exitcode,'actual_watchdog_wait_exit':watch_exit,
         'stage_complete':complete,'supervisor':identity(os.getpid()),'time':time.time(),'scientific_PASS':False})
    artifacts=[descriptor(p) for p in sorted(out.iterdir()) if p.is_file()]
    need(len(artifacts)+1<=(30 if stage=='FR00' else 100) and sum(v['bytes'] for v in artifacts)<268435456,'FINAL_OUTPUT_CAP')
    put(out,'run_seal.json',{'contract_id':CID,'stage':stage,'status':'COMPLETE' if complete else 'STOPPED',
         'artifacts':artifacts,'self_excluded':True,'scientific_PASS':False,'FR30_release':False})
    print(json.dumps({'stage':stage,'complete':complete,'worker_exit':exitcode,'watchdog_exit':watch_exit,'output':str(out)}),flush=True)
    return 0 if complete else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['supervise','worker','watchdog'])
    parser.add_argument('stage',choices=['FR00','FR10']);parser.add_argument('output',nargs='?')
    args=parser.parse_args()
    sys.exit(supervise(args.stage) if args.mode=='supervise' else worker(args.stage,Path(args.output)) if args.mode=='worker' else watchdog(Path(args.output)))
