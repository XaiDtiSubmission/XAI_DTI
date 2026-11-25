"""One-use FR20 v0.4 executor. No scientific work at import; stdlib and NumPy only."""
import argparse,collections,hashlib,importlib.metadata,io,json,math,os,re,shutil,signal,stat,subprocess,sys,time,traceback,zipfile
import fcntl
from pathlib import Path
ROOT=Path('historical_sources/DTI_XAI_Bottleneck')
PKG=ROOT/'protocol/smiles_gine_bottleneck_fr20_v0_4'
SCRIPT=ROOT/'scripts/sgc_fr20_execute_v0_4.py'
L0=ROOT/'audit/smiles_gine_bottleneck_fr20_engineering_recovery_v0_3/run_001'
L1=ROOT/'results/smiles_gine_bottleneck_fr20_engineering_recovery_v0_3/run_001'
GIB=2**30
BASE_FILE_COUNT=0;BASE_BYTES=0
NUM=re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
IDS=('label_group_id','pair_group_id','endpoint')
class Stop(Exception):pass
def need(x,msg):
    if not x:raise Stop(msg)
def enc(v):return (json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n').encode()
def sha(v):return hashlib.sha256(v).hexdigest()
def fp(s):return {k:str(getattr(s,k)) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_nlink')}
def desc(p):
    flush_outputs()
    r=p.read_bytes();return {'path':str(p),'bytes':len(r),'sha256':sha(r)}
def ownfiles():return [p for d in (L0,L1) if d.exists() for p in d.iterdir() if p.is_file()]
BUFFERS={}; BUFFER_ROWS=collections.Counter()
def flush_outputs():
    if not BUFFERS:return
    with (L0/'.output_write_lock').open('ab') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        files=ownfiles();new=[p for p in BUFFERS if not p.exists()]
        need(len(files)+len(new)+BASE_FILE_COUNT<=80 and sum(v.stat().st_size for v in files)+sum(len(v) for v in BUFFERS.values())+BASE_BYTES<=536870912,'OUTPUT_BUDGET')
        for p,raw in list(BUFFERS.items()):
            with p.open('ab') as f:f.write(raw);f.flush()
        BUFFERS.clear();BUFFER_ROWS.clear()
        fcntl.flock(lock,fcntl.LOCK_UN)
def rawput(out,name,raw,replace=False):
    flush_outputs()
    p=out/name;need('/' not in name,'OUTPUT_NAME')
    files=ownfiles();old=p.stat().st_size if p.exists() else 0
    need(len(files)+(0 if p.exists() else 1)+BASE_FILE_COUNT<=80 and sum(v.stat().st_size for v in files)-old+len(raw)+BASE_BYTES<=536870912,'OUTPUT_BUDGET')
    q=p.with_name(p.name+'.tmp') if replace else p
    with q.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    if replace:os.replace(q,p)
    return {'path':str(p),'bytes':len(raw),'sha256':sha(raw)}
def put(out,name,obj,replace=False):return rawput(out,name,enc(obj),replace)
def append(out,name,obj):
    p=out/name;BUFFERS.setdefault(p,bytearray()).extend(enc(obj));BUFFER_ROWS[p]+=1
    if BUFFER_ROWS[p]>=8192:flush_outputs()
def ident(pid):
    text=subprocess.check_output(['/bin/ps','-ww','-p',str(pid),'-o','pid=,uid=,pgid=,lstart=,command='],text=True).strip()
    v=text.split(None,8);need(len(v)==9,'PROCESS_IDENTITY')
    cwd=subprocess.check_output(['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],text=True)
    paths=[x[1:] for x in cwd.splitlines() if x.startswith('n')]
    need(len(paths)==1,'PROCESS_CWD')
    return {'pid':int(v[0]),'UID':int(v[1]),'PGID':int(v[2]),'birth':' '.join(v[3:8]),'command':v[8],'cwd':paths[0]}
def live(v):
    try:return ident(v['pid'])==v
    except (OSError,subprocess.CalledProcessError,Stop):return False
def resources(pids):
    text=subprocess.check_output(['/usr/bin/vm_stat'],text=True);page=int(re.search(r'page size of (\d+) bytes',text).group(1))
    def pages(name):
        return int(re.search(r'^'+re.escape(name)+r':\s+(\d+)',text,re.M).group(1))
    available=(pages('Pages free')+pages('Pages inactive'))*page;rss=0
    for pid in set(pids):
        try:rss+=int(subprocess.check_output(['/bin/ps','-p',str(pid),'-o','rss='],text=True).strip())*1024
        except (ValueError,subprocess.CalledProcessError):pass
    return {'RSS_bytes':rss,'available_bytes':available,'available_method':'Darwin free+inactive pages','disk_free_bytes':shutil.disk_usage(ROOT).free,'MPS_initialized':False,'worker_MPS_driver_bytes':0,'wall':time.time(),'monotonic':time.monotonic()}
def resource_ok(v,claim):
    need(v['RSS_bytes']<=36*GIB,'RSS_LIMIT');need(v['available_bytes']>=4*GIB,'AVAILABLE_MEMORY_LIMIT');need(v['disk_free_bytes']>=20*GIB,'DISK_LIMIT')
    need(v['wall']<claim['wall_deadline'] and v['monotonic']<claim['monotonic_deadline'],'TOTAL_DEADLINE')
class Guard:
    def __init__(self,claim):self.claim=claim;self.last=0;self.phase='BOOT'
    def check(self,force=False):
        need(time.time()<self.claim['wall_deadline'] and time.monotonic()<self.claim['monotonic_deadline'],'TOTAL_DEADLINE')
        need(not (L0/'safety_stop.json').exists(),'WATCHDOG_STOP')
        if force or time.monotonic()-self.last>=5:
            v=resources([os.getpid(),self.claim['supervisor']['pid']]);resource_ok(v,self.claim)
            put(L0,'worker_progress.json',{'phase':self.phase,**v},True);self.last=time.monotonic()
class Reader:
    def __init__(self,guard,binding_raw):
        self.g=guard;self.cache={};self.binding=json.loads(binding_raw);self.recovery=self.binding['engineering_handoff'];self.used=collections.Counter(self.recovery['prior_source_spending']);self.used['engineering_package_files']+=1;self.used['engineering_package_bytes']+=len(binding_raw)
        self.caps={'package':(16,524288),'engineering_package':(48,1572864),'control':(140,821396),'metadata':(2,28753366),'parameters':(1,35397),'prediction':(20,6859340),'prediction_replay':(1,457282),'inherited_metadata':(8,40818593)}
        preload=self.binding['preloaded_control'];record=preload['descriptor'];body=preload['raw_utf8'].encode();need(len(body)==record['bytes'] and sha(body)==record['sha256'],'PRELOADED_CONTROL_SHA');self.cache[record['path']]=(record['sha256'],body)
        append(L0,'source_and_call_ledger.jsonl',{'event':'BINDING_SINGLE_PHYSICAL_READ_BY_SUPERVISOR','files':1,'bytes':len(binding_raw)})
    def read(self,rec,cat):
        cat='engineering_package' if cat=='package' else cat
        if cat=='prediction' and rec['path']==self.recovery['prediction_replay_descriptor']['path']:cat='prediction_replay'
        p=Path(rec['path']);key=str(p)
        if key in self.cache:
            h,raw=self.cache[key];need(h==rec['sha256'],'SOURCE_CONFLICT');return raw
        need(p.is_absolute() and p.is_relative_to(ROOT) and '..' not in p.parts,'SOURCE_ROOT')
        for q in (p,*p.parents):need(not q.is_symlink(),'INPUT_SYMLINK')
        self.g.check();self.used[cat+'_files']+=1;self.used[cat+'_bytes']+=rec['bytes'];nf,nb=self.caps[cat]
        need(self.used[cat+'_files']<=nf and self.used[cat+'_bytes']<=nb,'SOURCE_BUDGET_'+cat)
        append(L0,'source_and_call_ledger.jsonl',{'event':'RESERVE','category':cat,'path':key,'used':dict(self.used)})
        before=p.lstat();need(stat.S_ISREG(before.st_mode) and before.st_size==rec['bytes'],'INPUT_SIZE_TYPE')
        for k,v in rec.get('fingerprint',{}).items():need(str(getattr(before,k))==str(v),'INTEGRITY_FINGERPRINT_'+k)
        with os.fdopen(os.open(p,os.O_RDONLY|os.O_NOFOLLOW),'rb') as stream:
            need(fp(os.fstat(stream.fileno()))==fp(before),'INPUT_OPEN_RACE');raw=stream.read(rec['bytes']+1);after=os.fstat(stream.fileno())
        need(len(raw)==rec['bytes'] and sha(raw)==rec['sha256'] and fp(before)==fp(after)==fp(p.lstat()),'INTEGRITY_SHA_OR_STABILITY')
        self.cache[key]=(rec['sha256'],raw);append(L0,'source_and_call_ledger.jsonl',{'event':'READ_COMPLETE','category':cat,'path':key,'bytes':len(raw),'sha256':sha(raw),'fingerprint':fp(before)})
        return raw
# Schema-selecting parser: disallowed branches are lexically skipped without decoding values.
def projected(raw,schema):
    t=raw.decode('utf8');n=len(t);dec=json.JSONDecoder()
    def ws(i):
        while i<n and t[i].isspace():i+=1
        return i
    def end(i):
        i=ws(i);need(i<n,'JSON_END')
        if t[i]=='"':
            j=i+1
            while j<n:
                if t[j]=='"':return j+1
                j+=2 if t[j]=='\\' else 1
            raise Stop('JSON_STRING')
        if t[i] in '[{':
            close=']' if t[i]=='[' else '}';j=ws(i+1)
            while j<n and t[j]!=close:
                if t[j] in ',:':j=ws(j+1)
                else:j=ws(end(j))
            need(j<n,'JSON_CONTAINER');return j+1
        m=NUM.match(t,i)
        if m:return m.end()
        for token in ('true','false','null'):
            if t.startswith(token,i):return i+len(token)
        raise Stop('JSON_SYNTAX')
    def value(i,spec):
        i=ws(i)
        if spec is False:return None,end(i)
        if spec is True:return dec.raw_decode(t,i)
        if isinstance(spec,dict):
            need(t[i]=='{','JSON_OBJECT');obj={};seen=set();i=ws(i+1)
            while t[i]!='}':
                k,i=dec.raw_decode(t,i);need(isinstance(k,str) and k not in seen,'JSON_KEY');seen.add(k);i=ws(i);need(t[i]==':','JSON_COLON')
                sub=spec.get(k,spec.get('*',False));v,i=value(i+1,sub)
                if sub is not False:obj[k]=v
                i=ws(i)
                if t[i]==',':i=ws(i+1)
                else:need(t[i]=='}','JSON_COMMA')
            return obj,i+1
        if isinstance(spec,list):
            need(t[i]=='[','JSON_ARRAY');arr=[];i=ws(i+1)
            while t[i]!=']':
                v,i=value(i,spec[0]);arr.append(v);i=ws(i)
                if t[i]==',':i=ws(i+1)
                else:need(t[i]==']','JSON_ARRAY_COMMA')
            return arr,i+1
        raise Stop('PROJECTION_SCHEMA')
    v,i=value(0,schema);need(ws(i)==n,'JSON_TRAILING');return v
DREC={k:True for k in ('path','bytes','sha256')};DREC['fingerprint']={'*':True}
def control_schema(rec):
    key=rec['id']
    if key.endswith('.old_case_seal') or key.endswith('.high_case_seal'):return {'case_id':True,'contract_id':True,'status':True,'self_excluded':True,'artifacts':[DREC]}
    if key.endswith('.parity_receipt.json'):return {'case':{k:True for k in ('case_id','id','name','seed','arm')},'parity':{'MODEL_FIT':{'*':True},'MODEL_GATE':{'*':True}},'encoder_sha256':True,'encoder_update_calls':True,'cached_shape':[True],'source_context':True,'original_and_cache_replay_each_bitwise':True}
    if key.endswith('.fit_started.json'):
        s={k:True for k in ('case_id','seed','LR','head_initial_sha256','reference_LR','new_fit','retry')};s['cache']=DREC;return s
    if key.endswith('.execution_receipt.json') and key.startswith('formal_'):
        s={k:True for k in ('status','case_id','seed','arm','LR','head_initial_sha256','all150_H3_order_hashes_matched','GATE_selection','GATE_metrics_computed','encoder_updates','encoder_forward','retries')};s['checkpoint']=DREC;return s
    if key=='FR10.accepted_runtime_metadata':return {'runtime':{'python':True,'packages':{'numpy':True},'environment':{'*':True}}}
    if key=='FR00.asset_projection_receipt.json':return {'status':True,'corroborated_required_assets':[{k:True for k in ('case_id','kind','path','sha256')}],'inherited_core_asset_files':True,'inherited_model_configurations':True,'encoded_identity':True,'payload_target_opens':True}
    # All remaining JSON entries are descriptor/status controls, never parameters/predictions.
    return {'status':True,'contract_id':True,'self_excluded':True,'artifacts':[DREC],'FR30_release':True,'scientific_PASS':True,'model_calls':True}
def rowkey(row):return tuple(row[k] for k in IDS)
def inputkey(row):return (row['smiles_sha256'],row['target_sequence_key'],row['endpoint'])
def links(seal,rec):
    for v in seal.get('artifacts',[]):
        p=Path(v['path']);p=p if p.is_absolute() else ROOT/p
        if str(p)==rec['path'] and v['bytes']==rec['bytes'] and v['sha256']==rec['sha256']:return True
    return False
class Calls:
    def __init__(self,budget):self.cap=budget['future_calls'];self.used=collections.Counter()
    def add(self,key,n=1):
        need(key in self.cap and n>=0,'UNREGISTERED_CALL_'+key);need(self.used[key]+n<=self.cap[key],'CALL_BUDGET_'+key);self.used[key]+=n
def closure(reader,g,allow,assets):
    g.phase='L0_CONTROL_LINEAGE';ext={}
    for rec in allow['controls']:
        raw=reader.read(rec,'control')
        if rec['path'].endswith('.json'):ext[rec['id']]=projected(raw,control_schema(rec))
    lineage=[]
    projection=ext['FR00.asset_projection_receipt.json']
    need(projection.get('inherited_core_asset_files')==40 and projection.get('payload_target_opens')==0,'NOT_READY_SOURCE_PROOF_FR00_LINKS')
    corroborated={(v['case_id'],v['kind']):(v['path'],v['sha256']) for v in projection['corroborated_required_assets']}
    for case in assets['cases']:
        cid=case['case_id'];g.phase='L0_CONTROL_LINEAGE_'+cid;old=ext[cid+'.old_case_seal'];high=ext[cid+'.high_case_seal'];par=ext[cid+'.old_case_seal.parity_receipt.json'];start=ext[cid+'.high_case_seal.fit_started.json'];receipt=ext[cid+'.high_case_seal.execution_receipt.json']
        need(old.get('case_id')==cid and high.get('case_id')==cid,'NOT_READY_SOURCE_PROOF_CASE')
        need(str(old.get('status','')).startswith('COMPLETE') and str(high.get('status','')).startswith('COMPLETE'),'NOT_READY_SOURCE_PROOF_TERMINAL')
        for kind,field in [('baseline_predictions','h0_predictions'),('frozen_cache_reference_only','cache_identity_reference_only'),('high_predictions','h1_predictions'),('high_head_reference_only','head_identity_reference_only')]:
            need(corroborated.get((cid,kind))==(case[field]['path'],case[field]['sha256']),'INTEGRITY_OR_LINEAGE_STOP_FR00_ASSET')
        for seal,rec in [(old,case['h0_predictions']),(old,case['cache_identity_reference_only']),(high,case['h1_predictions']),(high,case['head_identity_reference_only'])]:need(links(seal,rec),'INTEGRITY_OR_LINEAGE_STOP_ARTIFACT')
        need(par.get('original_and_cache_replay_each_bitwise') is True and par.get('encoder_update_calls')==0 and par.get('cached_shape')==[56962,1024],'NOT_READY_SOURCE_PROOF_PARITY')
        need(par.get('source_context')=='original role canonical batch32','NOT_READY_SOURCE_PROOF_CONTEXT')
        for role,count in [('MODEL_FIT',46140),('MODEL_GATE',5697)]:
            need(par['parity'][role].get('original_saved_bitwise_scalars')==count and par['parity'][role].get('cache_head_bitwise_scalars')==count,'NOT_READY_SOURCE_PROOF_ROLE_PARITY')
        need(start.get('case_id')==cid and start.get('seed')==case['seed'] and start.get('LR')==0.0006 and start.get('cache',{}).get('sha256')==case['cache_identity_reference_only']['sha256'],'INTEGRITY_OR_LINEAGE_STOP_FIT')
        need(receipt.get('case_id')==cid and receipt.get('LR')==0.0006 and receipt.get('all150_H3_order_hashes_matched') is True and receipt.get('GATE_selection') is False and receipt.get('GATE_metrics_computed') is False and receipt.get('encoder_updates')==0 and receipt.get('encoder_forward')==0,'NOT_READY_SOURCE_PROOF_EXECUTION')
        need(receipt.get('checkpoint',{}).get('sha256')==case['head_identity_reference_only']['sha256'],'INTEGRITY_OR_LINEAGE_STOP_HEAD')
        lineage.append({'case_id':cid,'historical_retries_observed':receipt.get('retries'),'cache_sha256_reference_only':case['cache_identity_reference_only']['sha256'],'head_sha256_reference_only':case['head_identity_reference_only']['sha256'],'role_order_and_historical_parity':'ACCEPTED_SOURCE_CONTROL','h1_LR':0.0006,'tensor_opens':0})
    runtime=ext['FR10.accepted_runtime_metadata']['runtime'];need(runtime['python']=='3.12.7' and runtime['packages']['numpy']=='1.26.4','RUNTIME_SOURCE')
    put(L0,'source_lineage_receipt.json',{'cases':lineage,'n_cases':10,'scope':'historical observed-score tables, not latent interchange','labels_or_tensor_opens':0})
    g.phase='L0_IDENTITY_AND_REFERENCES'
    ids={k:True for k in (*IDS,'v3_3_role','smiles_sha256','target_sequence_key')}
    order=projected(reader.read(allow['identity_metadata'][0],'metadata'),{'roles':{'MODEL_FIT':[ids],'MODEL_GATE':[ids]},'FIT_order':True,'GATE_order':True,'source_identity':True})
    roles=order['roles'];fit,gate=roles['MODEL_FIT'],roles['MODEL_GATE'];need(len(fit)==46140 and len(gate)==5697,'FULL_ROLE_COUNTS')
    for role,rows in roles.items():
        need(len({rowkey(r) for r in rows})==len(rows),'DUPLICATE_RECORD_ID')
        need(all(all(isinstance(r[k],str) and r[k] for k in (*IDS,'smiles_sha256','target_sequence_key')) and r['endpoint'] in ('Kd','Ki') for r in rows),'IDENTITY_FIELDS')
        need(all(r.get('v3_3_role')==role for r in rows),'ROLE_IDENTITY')
    need([rowkey(r) for r in fit]==sorted([rowkey(r) for r in fit],key=lambda v:tuple(x.encode('utf8') for x in v)),'FIT_ORDER')
    need(collections.Counter(r['endpoint'] for r in gate)=={'Kd':798,'Ki':4899},'GATE_ENDPOINT_COUNTS')
    ref=projected(reader.read(allow['identity_metadata'][1],'metadata'),{'name':True,'rule':True,'eligibility':[{'gate_row_index0':True,'FIT_reference_rows':[True],'each_row_weight':True}],'R_i_analysis':True,'head_queries':True})
    need(ref['name']=='R_i_census' and ref['head_queries']==0,'CENSUS_SOURCE')
    aliases=collections.defaultdict(list)
    for role,rows in roles.items():
        for i,row in enumerate(rows):aliases[inputkey(row)].append((role,i,rowkey(row)))
    sources=[];needed=set();counts=collections.Counter();nlinks=collections.Counter();seen=set()
    for item in ref['eligibility']:
        i=item['gate_row_index0'];need(type(i) is int and 0<=i<5697 and i not in seen,'GATE_INDEX');seen.add(i);refs=item['FIT_reference_rows']
        need(all(type(j) is int and 0<=j<46140 for j in refs) and refs==sorted(set(refs)),'REFERENCE_ORDER')
        if not refs:continue
        src=gate[i];e=src['endpoint'];n=len(refs);groups=collections.Counter(fit[j]['target_sequence_key'] for j in refs);K=len(groups);den=[K*groups[fit[j]['target_sequence_key']] for j in refs];corners=[]
        need(abs(math.fsum(1/v for v in den)-1)<=1e-8 and abs(item['each_row_weight']-1/n)<=1e-8,'WEIGHT_NORMALIZATION')
        for j in refs:
            donor=fit[j];D,P=src['smiles_sha256'],src['target_sequence_key'];D0,P0=donor['smiles_sha256'],donor['target_sequence_key']
            keys=[(D,P,e),(D,P0,e),(D0,P,e),(D0,P0,e)]
            need(donor['endpoint']==e and D!=D0 and P!=P0 and len(set(keys))==4 and all(k in aliases for k in keys),'FULL_NONDEGENERATE_O_CORNERS')
            needed.update(keys);corners.append(keys)
        sources.append({'source_id':i,'row':src,'refs':refs,'denominators':[n]*n,'sensitive_denominators':den,'corners':corners,'n_donor_proteins':K,'identical_weights':all(v==n for v in den)})
        counts[e]+=1;nlinks[e]+=n;g.check()
    need(len(seen)==5697 and counts=={'Kd':206,'Ki':2036} and nlinks=={'Kd':8150,'Ki':314509},'FULL_FROZEN_DOMAIN')
    sources.sort(key=lambda v:tuple(x.encode('utf8') for x in rowkey(v['row'])));keys=sorted(needed,key=lambda k:tuple(v.encode('utf8') for v in k));ki={k:i for i,k in enumerate(keys)}
    need(len(keys)<=51837,'NEEDED_KEY_COUNT')
    for src in sources:
        src['corner_ids']=[[ki[k] for k in v] for v in src['corners']]
        append(L0,'panel_manifest.jsonl',{'source_id':src['source_id'],**src['row'],'n_reference_rows':len(src['refs']),'n_donor_proteins':src['n_donor_proteins'],'identical_weights':src['identical_weights'],'case_ids':[c['case_id'] for c in assets['cases']],'corner_alias_map_id':sha(enc(src['corner_ids']))})
        for j,d0,d1,corner in zip(src['refs'],src['denominators'],src['sensitive_denominators'],src['corner_ids']):
            append(L0,'reference_weights.jsonl',[src['source_id'],j,d0,d1,1/d0,1/d1]);append(L0,'corner_map.jsonl',[src['source_id'],j,*corner])
        g.check()
    for k in keys:append(L0,'needed_corner_alias_manifest.jsonl',{'key_id':ki[k],'smiles_sha256':k[0],'target_sequence_key':k[1],'endpoint':k[2],'aliases':[{'role':v[0],'row_index0':v[1],'record_id':list(v[2])} for v in aliases[k]]})
    put(L0,'coverage_and_reuse.json',{'source_counts':dict(counts),'reference_links':dict(nlinks),'needed_keys':len(keys),'needed_alias_records':sum(len(aliases[k]) for k in keys),'identical_weight_sources':sum(s['identical_weights'] for s in sources),'source_composition':{e:{'drug_count':len({s['row']['smiles_sha256'] for s in sources if s['row']['endpoint']==e}),'protein_count':len({s['row']['target_sequence_key'] for s in sources if s['row']['endpoint']==e})} for e in counts},'domain':'O_SRC_METADATA_LINKED','encoded_distinctness':False,'power_or_representativeness_PASS':False})
    put(L0,'metadata_entry_receipt.json',{'status':'COMPLETE_FULL_METADATA_CANDIDATE_CLOSURE','all10_cases':True,'source_rows':2242,'reference_links':322659,'needed_score_qualification':'NOT_ASSESSED','old_census_reruns':0})
    names=['panel_manifest.jsonl','reference_weights.jsonl','corner_map.jsonl','needed_corner_alias_manifest.jsonl','source_lineage_receipt.json','coverage_and_reuse.json','metadata_entry_receipt.json']
    seal=put(L0,'metadata_seal.json',{'status':'COMPLETE_METADATA_ONLY','artifacts':[desc(L0/n) for n in names],'self_excluded':True,'score_qualification':False})
    return sources,keys,aliases,roles,seal

def l1_scores(reader,g,allow,assets,calls,keys,aliases):
    import numpy as np
    g.phase='L1_ALL10_NEEDED_ALIAS_QUALIFICATION';tables=[];receipts=[]
    need({v['purpose'] for v in allow['predictions']}=={'h0','h1 LR0.0006'},'FROZEN_PURPOSE_IDS')
    predictions={(v['case_id'],'h0' if v['purpose']=='h0' else 'h1'):v for v in allow['predictions']}
    for case in assets['cases']:
        data={}
        for purpose in ('h0','h1'):
            rec=predictions[case['case_id'],purpose];raw=reader.read(rec,'prediction');arrays={}
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for name,spec in rec['decode_members'].items():
                    calls.add('decoded_prediction_members');arr=np.lib.format.read_array(io.BytesIO(archive.read(name)),allow_pickle=False)
                    calls.add('decoded_prediction_scalars',arr.size)
                    if rec['path']==reader.recovery['prediction_replay_descriptor']['path']:
                        calls.add('engineering_redecoded_members');calls.add('engineering_redecoded_scalars',arr.size)
                    need(arr.shape==tuple(spec['shape']) and arr.dtype==np.float32 and bool(np.isfinite(arr).all()),'SCORE_SCHEMA_FINITE')
                    arrays['MODEL_FIT' if name.startswith('MODEL_FIT_') else 'MODEL_GATE']=arr
            values=np.empty(len(keys),dtype=np.float64)
            for kidx,key in enumerate(keys):
                group=aliases[key];calls.add('alias_validation_scalar_visits_max',len(group))
                v=[arrays[role][i] for role,i,_ in group]
                if any(x!=v[0] for x in v[1:]):
                    evidence={'status':'SOURCE_SCORE_NOT_IDENTIFIABLE','case_id':case['case_id'],'function':purpose,'key_id':kidx,'input_key':list(key),'aliases':[{'role':role,'row_index0':i,'record_id':list(ident_),'saved_float32_score':float(arrays[role][i])} for role,i,ident_ in group],'qualified_cases_before_stop':receipts,'q_or_calibration_computed':False,'reason':'Valid bound saved scores disagree for required exact input key in historical contexts; no averaging, tolerance, representative or forward fallback.'}
                    put(L1,'needed_score_identifiability_receipt.json',evidence);raise Stop('SOURCE_SCORE_NOT_IDENTIFIABLE')
                values[kidx]=float(v[0])
                if kidx%2048==0:g.check()
            data[purpose]=values;del arrays
        receipts.append({'case_id':case['case_id'],'h0_h1_necessary_aliases_exact':True,'needed_keys':len(keys),'needed_aliases':sum(len(aliases[k]) for k in keys)})
        tables.append(data);g.check()
    put(L1,'needed_score_identifiability_receipt.json',{'status':'ALL10_REQUIRED_SCORE_MAPS_IDENTIFIABLE','cases':receipts,'alias_tolerance':0,'signed_zero_equal':True,'q_computed_before_qualification':False})
    return tables
def parameters(reader,allow):
    spec={k:True for k in ('case_id','endpoint','family','mode','slot_sha256','FIT_rows')}
    spec.update(knots=[True],coefficients=[True]);v=projected(reader.read(allow['frozen_parameters'][0],'parameters'),{'slots':[spec]})
    slots={}
    for s in v['slots']:
        key=(s['case_id'],s['endpoint'],s['family']);need(key not in slots,'CALIBRATOR_SLOT_DUPLICATE');slots[key]=s
        need(re.fullmatch('[0-9a-f]{64}',s['slot_sha256']) is not None,'CALIBRATOR_SLOT_ID')
    need(len(slots)==40,'FROZEN40_CALIBRATORS')
    return slots
def function_audit(reader,g,allow,assets,calls,sources,keys,tables,meta_seal):
    import numpy as np
    slots=parameters(reader,allow);ep=np.array([k[2] for k in keys]);base_stats={};q_stats={};r_stats={};affine=[];max_residual=0.
    def checkres(value):
        nonlocal max_residual
        v=float(np.max(np.abs(value))) if np.size(value) else 0.;need(math.isfinite(v) and v<=1e-8,'NUMERICAL_QUALIFICATION_STOP_IDENTITY');max_residual=max(max_residual,v);return v
    def compose(v,w):return math.fsum(float(a)*float(b) for a,b in zip(v,w))
    for ci,(case,table) in enumerate(zip(assets['cases'],tables)):
        g.phase='L1_FUNCTIONAL_'+case['case_id'];h0,h1=table['h0'],table['h1'];aff=np.empty_like(h0);nonlin=np.empty_like(h0);slopes={}
        for e in ('Kd','Ki'):
            mask=ep==e
            for family,dest in [('affine',aff),('piecewise_linear',nonlin)]:
                matches=[v for (c,ee,f),v in slots.items() if c==case['case_id'] and ee==e and ((family=='affine' and f=='affine') or (family!='affine' and f!='affine'))]
                need(len(matches)==1,'CALIBRATOR_CASE_ENDPOINT_FAMILY');slot=matches[0];co=slot['coefficients'];knots=slot['knots']
                need(len(co)==2+len(knots) and len(co)>=2 and all(math.isfinite(x) for x in co+knots),'CALIBRATOR_SCHEMA')
                if family=='affine':need(len(knots)==0,'AFFINE_KNOTS');slopes[e]=co[0]
                calls.add('calibration_scalar_applications_max',int(mask.sum()));t=h0[mask];out=co[0]*t+co[1]
                for coeff,knot in zip(co[2:],knots):out=out+coeff*np.maximum(t-knot,0.)
                need(bool(np.isfinite(out).all()),'CALIBRATOR_NONFINITE');dest[mask]=out
        functions=np.stack([h0,aff,nonlin,h1]);q=np.stack([h1-aff,h1-h0,h1-nonlin]);need(bool(np.isfinite(q).all()),'Q_NONFINITE');calls.add('q_scalar_contrasts_max',q.size)
        bs=collections.defaultdict(list);qs=collections.defaultdict(list);rs=collections.defaultdict(list);scale_ref=collections.defaultdict(int);scale_comp=collections.defaultdict(int);scale_err=collections.defaultdict(float)
        for src in sources:
            e=src['row']['endpoint'];n_total=len(src['corner_ids']);partial_base={};partial_q={};source_A=src['corner_ids'][0][0]
            for lo in range(0,n_total,8192):
                ids=np.asarray(src['corner_ids'][lo:lo+8192],dtype=np.int64);n=len(ids);A,B,C,D=ids.T
                vals=functions[:,ids];iv=(vals[:,:,0]-vals[:,:,1])-(vals[:,:,2]-vals[:,:,3])
                calls.add('basic_function_four_point_tuples_max',4*n);calls.add('basic_function_corner_scalar_lookups_max',16*n)
                scale_err[e]=max(scale_err[e],checkres(iv[1]-slopes[e]*iv[0]));scale_ref[e]+=n;calls.add('affine_scale_per_reference_numeric_checks_max',n)
                for v in list(functions)+list(q):
                    aa,bb,cc,dd=v[A],v[B],v[C],v[D]
                    for zero in [(aa-bb)-(aa-bb),(aa-aa)-(cc-cc),(aa-aa)-(aa-aa)]:checkres(zero)
                    calls.add('structural_degenerate_four_point_checks_max',3*n)
                for v in q:
                    zero=v[A]-v[A];checkres(zero);checkres(zero);calls.add('structural_identity_response_checks_max',2*n)
                weights=[np.array([1/x for x in src['denominators'][lo:lo+n]]),np.array([1/x for x in src['sensitive_denominators'][lo:lo+n]])]
                for policy,w in enumerate(weights):
                    for fi in range(4):
                        partial_base.setdefault((policy,fi,0),[]).append(compose(iv[fi],w))
                        partial_base.setdefault((policy,fi,1),[]).append(compose(np.abs(iv[fi]),w))
                for ti,v in enumerate(q):
                    aa,bb,cc,dd=v[A],v[B],v[C],v[D];terms=np.stack([dd,bb-dd,cc-dd,(aa-bb)-(cc-dd),cc-aa,bb-aa])
                    calls.add('target_reference_tuples_max',n);calls.add('target_corner_scalar_lookups_max',4*n);calls.add('observed_single_side_score_difference_values_max',2*n)
                    checkres(aa-np.sum(terms[:4],axis=0));checkres(terms[4]+terms[1]+terms[3]);checkres(terms[5]+terms[2]+terms[3]);checkres(terms[3]-(iv[3]-iv[[1,0,2][ti]]))
                    for policy,w in enumerate(weights):
                        calls.add('weighted_component_contributions_max',4*n);calls.add('weighted_response_contributions_max',2*n)
                        for qi in range(6):
                            partial_q.setdefault((ti,policy,qi,0),[]).append(compose(terms[qi],w))
                            partial_q.setdefault((ti,policy,qi,1),[]).append(compose(np.abs(terms[qi]),w))
                g.check()
            for policy in range(2):
                bar=[math.fsum(partial_base[policy,fi,0]) for fi in range(4)];unc=[math.fsum(partial_base[policy,fi,1]) for fi in range(4)]
                scale_err[e]=max(scale_err[e],checkres(bar[1]-slopes[e]*bar[0]));scale_comp[e]+=1;calls.add('affine_scale_per_source_policy_numeric_checks_max')
                for fi in range(4):
                    calls.add('basic_function_source_amplitude_records_max');append(L1,'basic_four_point.jsonl',[ci,src['source_id'],policy,fi,bar[fi],unc[fi]]);bs[e,policy,fi].append((abs(bar[fi]),unc[fi]))
            for ti in range(3):
                composed=[]
                for policy in range(2):
                    bar=[math.fsum(partial_q[ti,policy,qi,0]) for qi in range(6)];unc=[math.fsum(partial_q[ti,policy,qi,1]) for qi in range(6)];composed.append(bar)
                    calls.add('source_target_scheme_records_max')
                    checkres(float(q[ti,source_A])-math.fsum(bar[:4]));checkres(bar[4]+bar[1]+bar[3]);checkres(bar[5]+bar[2]+bar[3])
                    append(L1,'source_components.jsonl',[ci,src['source_id'],ti,policy,float(q[ti,source_A]),*bar,*unc])
                    for qi in range(6):qs[e,policy,ti,qi].append((bar[qi],abs(bar[qi]),unc[qi]))
                for qi,(main,sensitive) in enumerate(zip(*composed)):
                    delta=sensitive-main;sg0=int(main>0)-int(main<0);sg1=int(sensitive>0)-int(sensitive<0);flip=sg0*sg1==-1
                    calls.add('reference_sensitivity_all_quantity_source_pairs_max');calls.add('reference_sensitivity_qI_abs_difference_subset_max' if qi==3 else 'reference_sensitivity_other_quantity_source_pairs_max')
                    append(L1,'reference_sensitivity_pairs.jsonl',[ci,src['source_id'],ti,qi,main,sensitive,delta,abs(delta),sg0,sg1,int(flip),int(src['identical_weights'])])
                    rs[e,ti,qi].append((delta,abs(delta),sg0,sg1))
            g.check()
        for k,rows in bs.items():base_stats[(ci,)+k]=[math.fsum(r[j] for r in rows)/len(rows) for j in range(2)]
        for k,rows in qs.items():q_stats[(ci,)+k]=[math.fsum(r[j] for r in rows)/len(rows) for j in range(3)]
        for k,rows in rs.items():
            bins=[sum(1 for row in rows if row[2]==a and row[3]==b) for a in (-1,0,1) for b in (-1,0,1)]
            r_stats[(ci,)+k]=[math.fsum(row[0] for row in rows)/len(rows),math.fsum(row[1] for row in rows)/len(rows),*[n/len(rows) for n in bins]]
        for e in ('Kd','Ki'):affine.append({'case_id':case['case_id'],'endpoint':e,'n_reference_checks':scale_ref[e],'n_source_policy_checks':scale_comp[e],'max_abs_reference_or_composite_residual':scale_err[e],'frozen_numeric_tolerance':1e-8,'qualification_status':'PASS_IMPLEMENTATION_ONLY'})
    def layer(stats,arm,seedlayer,eplayer,tail):
        cis=[i for i,c in enumerate(assets['cases']) if c['arm']==arm and (seedlayer=='MEAN5' or c['seed']==int(seedlayer[-3:]))]
        ends=('Kd','Ki') if eplayer=='KD_KI_EQUAL' else (eplayer,)
        values=[stats[(ci,e)+tail] for ci in cis for e in ends]
        return [math.fsum(v[j] for v in values)/len(values) for j in range(len(values[0]))]
    seedlayers=['seed_017','seed_029','seed_043','seed_071','seed_101','MEAN5'];ends=['Kd','Ki','KD_KI_EQUAL'];basic=[];comp=[];functional=[];refs=[]
    def cancellation(M,U):return {'C_U_MINUS_M':U-M,'cancellation_numeric_status':'NEGATIVE_NUMERIC_RESIDUAL_NOT_INTERPRETED' if U-M<0 else 'DESCRIPTIVE_VALUE_NO_EFFECT_THRESHOLD'}
    for arm in ('S','G'):
        for sl in seedlayers:
            for el in ends:
                common={'arm':arm,'seed_layer':sl,'endpoint_layer':el,'source_denominators':{'Kd':206,'Ki':2036}}
                for policy in range(2):
                    for fi,name in enumerate(['h0','h_aff','h_1d','h1']):
                        M,U=layer(base_stats,arm,sl,el,(policy,fi));calls.add('basic_function_amplitude_summary_slots_max');calls.add('descriptive_U_minus_M_summary_differences_max')
                        basic.append({**common,'reference_scheme':['FIT_ROW_UNIFORM','FIT_PROTEIN_INPUT_UNIFORM'][policy],'function_id':name,'M_f':M,'U_f':U,**cancellation(M,U)})
                    for fi in (1,0,2):
                        new=layer(base_stats,arm,sl,el,(policy,3));base=layer(base_stats,arm,sl,el,(policy,fi));calls.add('fixed_amplitude_comparison_slots_max')
                        comp.append({**common,'reference_scheme':['FIT_ROW_UNIFORM','FIT_PROTEIN_INPUT_UNIFORM'][policy],'reference_function_id':['h0','h_aff','h_1d','h1'][fi],'delta_M':new[0]-base[0],'delta_U':new[1]-base[1]})
                    for ti in range(3):
                        for qi,name in enumerate(['q0','qD','qP','qI','rD_obs','rP_obs']):
                            signed,M,U=layer(q_stats,arm,sl,el,(policy,ti,qi));row={**common,'reference_scheme':['FIT_ROW_UNIFORM','FIT_PROTEIN_INPUT_UNIFORM'][policy],'target_id':['q','q_bg','q_nl'][ti],'quantity_id':name,'signed_mean':signed,'M':M,'U':U}
                            if qi==3:calls.add('descriptive_U_minus_M_summary_differences_max');row.update(cancellation(M,U))
                            functional.append(row)
                for ti in range(3):
                    for qi in range(6):
                        values=layer(r_stats,arm,sl,el,(ti,qi));calls.add('reference_sensitivity_all_quantity_summary_slots_max')
                        counts=None
                        if sl!='MEAN5' and el!='KD_KI_EQUAL':
                            n={'Kd':206,'Ki':2036}[el];counts=[int(round(v*n)) for v in values[2:]]
                        row={**common,'target_id':['q','q_bg','q_nl'][ti],'quantity_id':['q0','qD','qP','qI','rD_obs','rP_obs'][qi],'mean_delta':values[0],'mean_abs_delta':values[1],'transition_fractions':values[2:],'transition_counts':counts,'counts_reason':None if counts is not None else 'NOT_APPLICABLE_TO_EQUAL_WEIGHT_AGGREGATE','strict_sign_flip_fraction':values[4]+values[8]}
                        if qi==3:calls.add('D_ref_qI_summary_subset_slots_max');row['D_ref']=values[1]
                        refs.append(row)
    put(L1,'functional_summary.json',{'primary_definition':'G/q/qI/FIT_ROW_UNIFORM/MEAN5/KD_KI_EQUAL/M','functional':functional,'basic_amplitudes':basic,'fixed_amplitude_comparisons':comp,'practical_margin':None,'scientific_PASS':False})
    put(L1,'reference_sensitivity.json',{'complete_six_quantity_summaries':refs,'D_ref_is_qI_subset':True,'DESIGN_IDENTICAL':all(s['identical_weights'] for s in sources),'general_robustness_PASS':False})
    put(L1,'affine_scale_identity_receipt.json',{'status':'PASS_IMPLEMENTATION_ONLY','cases':affine})
    put(L1,'numerical_identity_receipt.json',{'status':'PASS_IMPLEMENTATION_ONLY','maximum_abs_residual':max_residual,'tolerance':1e-8,'source_alias_tolerance':0,'U_minus_M_not_a_gate':True})
    put(L1,'negative_controls.json',{'status':'PASS_REPEATED_SCALAR_ARITHMETIC_ONLY','standalone_tests_or_model_calls':0,'both_identity_responses_reuse_checked_A_minus_A':True,'independent_scientific_negative_controls':False})
    sr=reader.cache[str(PKG/'output_schema.json')][1]
    put(L1,'output_layout_receipt.json',{'schema':{'path':str(PKG/'output_schema.json'),'bytes':len(sr),'sha256':sha(sr)},'L0_seal':meta_seal,'physical_columns_bound_by_schema':True,'lossy_values':False,'source_id_is_original_GATE_index':True,'all_logical_identities_recoverable_via_L0':True})
    primary=next(v for v in functional if v['arm']=='G' and v['target_id']=='q' and v['quantity_id']=='qI' and v['reference_scheme']=='FIT_ROW_UNIFORM' and v['seed_layer']=='MEAN5' and v['endpoint_layer']=='KD_KI_EQUAL')
    scope={'status':'COMPLETE_FINITE_OBSERVED_SCORE_FUNCTION_AUDIT','primary':primary,'scope':'O_SRC2242 complete source rows; exposed development evidence only','not_identified':['unique H3 cause','new cross-modal task information','prediction benefit','biological interaction','XAI incremental value'],'FR30_release':False,'scientific_PASS':False}
    put(L1,'claim_scope.json',scope);calls.add('finite_functional_report')
    rawput(L1,'report.md',('# FR20有限观测分数功能审计\n\n完整2,242源、322,659参考连接及全部10case通过冻结资格。\n\nG主目标／主权重的M_I='+repr(primary['M'])+'，U_I='+repr(primary['U'])+'，U−M='+repr(primary['C_U_MINUS_M'])+'。\n\n这些是已暴露开发域、指定参考下的描述性功能幅度，不证明收益、病因、任务信息增量或XAI价值。全部端点／seed／两权重及六类参考配对见封印JSON。FR30仍未释放。\n').encode())
    return scope
def inherit_metadata(reader,g,binding):
    g.phase='INHERIT_COMPLETED_L0_WITHOUT_RERUN'
    mrec=binding['engineering_handoff']['metadata_seal'];m=json.loads(reader.read(mrec,'inherited_metadata'));raws={}
    for rec in m['artifacts']:
        need(rec in binding['engineering_handoff']['metadata_artifacts'],'INHERITED_METADATA_ALLOWLIST')
        raws[Path(rec['path']).name]=reader.read(rec,'inherited_metadata')
    panels=[json.loads(v) for v in raws['panel_manifest.jsonl'].splitlines()];by={}
    for row in panels:
        i=row['source_id'];need(i not in by,'INHERITED_SOURCE_DUPLICATE');by[i]={'source_id':i,'row':row,'refs':[],'denominators':[],'sensitive_denominators':[],'corner_ids':[],'n_donor_proteins':row['n_donor_proteins'],'identical_weights':row['identical_weights']}
    for line in raws['reference_weights.jsonl'].splitlines():
        i,j,d0,d1,w0,w1=json.loads(line);src=by[i];need(abs(w0-1/d0)<=1e-8 and abs(w1-1/d1)<=1e-8,'INHERITED_WEIGHT')
        src['refs'].append(j);src['denominators'].append(d0);src['sensitive_denominators'].append(d1)
    offsets=collections.Counter()
    for line in raws['corner_map.jsonl'].splitlines():
        i,j,*ids=json.loads(line);src=by[i];need(src['refs'][offsets[i]]==j,'INHERITED_REFERENCE_ORDER');offsets[i]+=1;src['corner_ids'].append(ids)
    keys=[];aliases={}
    for line in raws['needed_corner_alias_manifest.jsonl'].splitlines():
        row=json.loads(line);need(row['key_id']==len(keys),'INHERITED_KEY_ORDER');key=(row['smiles_sha256'],row['target_sequence_key'],row['endpoint']);keys.append(key);aliases[key]=[(v['role'],v['row_index0'],tuple(v['record_id'])) for v in row['aliases']]
    sources=list(by.values());need(len(sources)==2242 and sum(len(s['refs']) for s in sources)==322659,'INHERITED_FULL_DOMAIN')
    need(collections.Counter(s['row']['endpoint'] for s in sources)=={'Kd':206,'Ki':2036},'INHERITED_ENDPOINTS')
    put(L0,'metadata_inheritance_receipt.json',{'status':'INHERITED_COMPLETE_FROZEN_L0','source_seal':mrec,'source_rows':2242,'reference_links':322659,'original_metadata_reopens':0,'new_census_or_selection':False})
    put(L0,'metadata_seal.json',{'status':'INHERITED_COMPLETE_METADATA_ONLY','source_seal':mrec,'artifacts':m['artifacts'],'self_excluded':True,'new_metadata_closure_calls':0})
    return sources,keys,aliases,{},mrec

def worker(binding_raw,claim):
    guard=Guard(claim);reader=Reader(guard,binding_raw);binding=json.loads(binding_raw);calls=None;status='ENGINEERING_STOP';scope={};error=None
    try:
        for rec in binding['package_artifacts']:reader.read(rec,'package')
        reader.read(binding['implementation'],'package')
        budget=json.loads(reader.cache[str(PKG/'budget.json')][1]);allow=json.loads(reader.cache[str(PKG/'input_allowlist.json')][1]);assets=json.loads(reader.cache[str(PKG/'asset_bindings.json')][1])
        need(allow==binding['closed_input_manifest'] and budget==binding['budget_binding'],'CLOSED_INPUT_OR_BUDGET_BINDING')
        calls=Calls(budget);calls.cap=dict(calls.cap);calls.cap['engineering_resume_activations']=3;calls.cap['decoded_prediction_members']+=2;calls.cap['decoded_prediction_scalars']+=51837;calls.cap['engineering_redecoded_members']=2;calls.cap['engineering_redecoded_scalars']=51837;calls.used.update(binding['engineering_handoff']['prior_calls']);calls.add('engineering_resume_activations');guard.check(True)
        need(sys.version.split()[0]=='3.12.7' and importlib.metadata.version('numpy')=='1.26.4','RUNTIME_VERSION')
        for rec in binding['runtime']['build_files']:
            p=Path(rec['path']);raw=p.read_bytes();need(len(raw)==rec['bytes'] and sha(raw)==rec['sha256'],'RUNTIME_BUILD_IDENTITY')
        import numpy as np
        need(np.__version__=='1.26.4','NUMPY_RUNTIME')
        need(str(Path(np.__file__))==binding['runtime']['build_files'][1]['path'] and str(Path(np.core._multiarray_umath.__file__))==binding['runtime']['build_files'][2]['path'],'NUMPY_LOADED_BUILD_PATH')
        put(L0,'runtime_identity_receipt.json',{'python':sys.version.split()[0],'numpy':np.__version__,'build_files_verified':binding['runtime']['build_files'],'runtime_only_physical_reads':len(binding['runtime']['build_files']),'runtime_opaque_bytes':sum(v['bytes'] for v in binding['runtime']['build_files']),'original_project_research_source_opens':0,'tests_canary_model_calls':0})
        sources,keys,aliases,roles,meta=inherit_metadata(reader,guard,binding)
        tables=l1_scores(reader,guard,allow,assets,calls,keys,aliases)
        scope=function_audit(reader,guard,allow,assets,calls,sources,keys,tables,meta);status='COMPLETE_FR20'
    except BaseException as exc:
        error={'code':str(exc) if isinstance(exc,Stop) else 'ENGINEERING_'+type(exc).__name__,'frames':[{'file':Path(v.filename).name,'line':v.lineno,'function':v.name} for v in traceback.extract_tb(exc.__traceback__)]}
        status='SOURCE_SCORE_NOT_IDENTIFIABLE' if error['code']=='SOURCE_SCORE_NOT_IDENTIFIABLE' else ('STOPPED' if isinstance(exc,Stop) else 'ENGINEERING_STOP')
        put(L1 if (L0/'metadata_seal.json').exists() else L0,'stop_record.json',{'status':status,'phase':guard.phase,'error':error,'retry':False,'no_rebaseline':True})
    finally:
        out=L1 if (L0/'metadata_seal.json').exists() else L0
        put(out,'execution_receipt.json',{'status':status,'phase':guard.phase,'error':error,'source_spending':dict(reader.used),'calls':dict(calls.used) if calls else {},'claim_scope':scope,'model_calls':0,'labels_or_tensors_read':0,'FR30_release':False,'scientific_PASS':False,'wall':time.time(),'monotonic':time.monotonic()})
        flush_outputs();put(L0,'worker_done.json',{'status':status})
    return 0 if status=='COMPLETE_FR20' else 2
def watchdog(claim):
    parent=claim['supervisor'];worker_id=claim['worker'];me=ident(os.getpid());put(L0,'watchdog_identity.json',me)
    while not (L0/'worker_done.json').exists():
        if not live(worker_id):break
        try:
            need(live(parent),'SUPERVISOR_IDENTITY_LOST');v=resources([parent['pid'],worker_id['pid'],os.getpid()]);append(L0,'watchdog_resources.jsonl',v);flush_outputs();resource_ok(v,claim)
        except BaseException as exc:
            put(L0,'safety_stop.json',{'reason':str(exc),'wall':time.time(),'monotonic':time.monotonic()})
            if live(worker_id):os.kill(worker_id['pid'],signal.SIGTERM)
            return 2
        time.sleep(5)
    flush_outputs();return 0
def supervise(binding_path,expected_sha):
    global BASE_FILE_COUNT,BASE_BYTES
    raw=binding_path.read_bytes();need(sha(raw)==expected_sha,'BINDING_SHA');binding=json.loads(raw)
    BASE_FILE_COUNT=binding['retained_output_spending']['files'];BASE_BYTES=binding['retained_output_spending']['bytes']
    need(binding['execution_authorized'] is True and binding['FR30_authorized'] is False,'AUTHORITY')
    need(not L0.exists() and not L1.exists(),'OUTPUT_ALREADY_EXISTS')
    L0.mkdir(parents=True);L1.mkdir(parents=True)
    lock=L0/'formal_execution.lock'
    with lock.open('x') as f:f.write(str(os.getpid()))
    now=time.time();mono=time.monotonic();me=ident(os.getpid());history=binding['engineering_handoff'];claim={'wall_start':history['wall_start'],'monotonic_start':history['monotonic_start'],'wall_deadline':history['wall_deadline'],'monotonic_deadline':history['monotonic_deadline'],'supervisor':me,'engineering_continuation':True,'prior_seal':history['prior_seal'],'retained_output_files':BASE_FILE_COUNT,'retained_output_bytes':BASE_BYTES};need(now<claim['wall_deadline'] and mono<claim['monotonic_deadline'],'ORIGINAL_DEADLINE')
    put(L0,'execution_claim.json',{**claim,'binding_path':str(binding_path),'binding_sha256':expected_sha,'runtime_library_load_scope':binding['runtime'],'new_scientific_permission_from_current_user':True,'retries':0})
    py=binding['runtime']['interpreter'];env=dict(os.environ);env.update(binding['environment'])
    proc=subprocess.Popen([py,'-B',str(SCRIPT),'--worker'],stdin=subprocess.PIPE,cwd=ROOT,env=env)
    claim['worker']=ident(proc.pid);put(L0,'worker_identity.json',claim['worker'])
    envelope={'binding':raw.decode(),'claim':claim};proc.stdin.write(enc(envelope));proc.stdin.close()
    watch=subprocess.Popen([py,'-B',str(SCRIPT),'--watchdog'],stdin=subprocess.PIPE,cwd=ROOT,env=env);watch.stdin.write(enc(claim));watch.stdin.close()
    rc=proc.wait();wrc=watch.wait()
    flush_outputs();out=L1 if (L0/'metadata_seal.json').exists() else L0
    put(out,'supervisor_exit_receipt.json',{'worker_actual_wait_exit':rc,'watchdog_actual_wait_exit':wrc,'supervisor_self_exit':'UNKNOWN_AT_SELF_SEAL','supervisor':me,'worker':claim['worker'],'retries':0})
    receipt=json.loads((out/'execution_receipt.json').read_text()) if (out/'execution_receipt.json').exists() else {'status':'WORKER_EXIT_WITHOUT_RECEIPT'}
    put(out,'run_seal.json',{'status':receipt['status'],'artifacts':[desc(v) for v in sorted(ownfiles()) if v.name!='run_seal.json'],'self_excluded':True,'worker_actual_wait_exit':rc,'watchdog_actual_wait_exit':wrc,'FR30_release':False,'scientific_PASS':False})
    print(json.dumps({'status':receipt['status'],'worker_exit':rc,'watchdog_exit':wrc,'seal':str(out/'run_seal.json')}),flush=True)
    return 0 if rc==0 and wrc==0 else 2
def main():
    global BASE_FILE_COUNT,BASE_BYTES
    ap=argparse.ArgumentParser();ap.add_argument('--worker',action='store_true');ap.add_argument('--watchdog',action='store_true');ap.add_argument('--binding');ap.add_argument('--binding-sha');args=ap.parse_args()
    if args.worker:
        v=json.load(sys.stdin);BASE_FILE_COUNT=v['claim']['retained_output_files'];BASE_BYTES=v['claim']['retained_output_bytes'];return worker(v['binding'].encode(),v['claim'])
    if args.watchdog:
        v=json.load(sys.stdin);BASE_FILE_COUNT=v['retained_output_files'];BASE_BYTES=v['retained_output_bytes'];return watchdog(v)
    need(args.binding and args.binding_sha,'BINDING_REQUIRED');return supervise(Path(args.binding),args.binding_sha)
if __name__=='__main__':sys.exit(main())
