"""Independent single-use FR30 v0.4 scalar executor. No research work at import."""
import argparse, collections, fcntl, hashlib, io, json, math, os, re, shutil
import signal, stat, struct, subprocess, sys, time, traceback, zipfile
from pathlib import Path
ROOT=Path('historical_sources/DTI_XAI_Bottleneck')
PKG=ROOT/'protocol/smiles_gine_bottleneck_fr30_v0_4'
SCRIPT=ROOT/'scripts/sgc_fr30_execute_v0_2.py'
AUD=ROOT/'audit/smiles_gine_bottleneck_fr30_engineering_recovery_v0_1/run_001'
OUT=ROOT/'results/smiles_gine_bottleneck_fr30_engineering_recovery_v0_1/run_001'
CID='SGC-FR30-v0.4-DESCRIPTIVE-RISK-COMPOSITION-FULL-BOUND-CHAIN'
IDS=('label_group_id','pair_group_id','endpoint')
ARMS=('G','S');SEEDS=(17,29,43,71,101);LAYERS=(*SEEDS,'MEAN5')
EPS=('Kd','Ki','KD_KI_EQUAL');POLS=('FIT_ROW_UNIFORM','FIT_PROTEIN_INPUT_UNIFORM')
FUNCS=('h0','h_aff','h_1d','h1','minus_main','minus_sensitivity')
N={'Kd':206,'Ki':2036};GIB=2**30;TOL=1e-8
NUMBER=re.compile(rb'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
JNUMBER=re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
class Stop(Exception):pass
def need(v,code):
    if not v:raise Stop(code)
def enc(v):return (json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n').encode()
def sha(b):return hashlib.sha256(b).hexdigest()
def fp(s):return {k:str(getattr(s,k)) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_nlink')}
def desc(p):
    b=p.read_bytes();return {'path':str(p),'bytes':len(b),'sha256':sha(b)}
def outfiles():return [p for d in (AUD,OUT) if d.exists() for p in d.iterdir() if p.is_file()]
def write(out,name,v,append=False):
    need('/' not in name and name not in ('.','..'),'OUTPUT_NAME')
    raw=enc(v);p=out/name
    with (AUD/'.output_write_lock').open('ab') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        files=outfiles();need(len(files)+(0 if p.exists() else 1)<=40,'OUTPUT_FILE_BUDGET')
        need(sum(x.stat().st_size for x in files)+len(raw)<=134217728,'OUTPUT_BYTE_BUDGET')
        with p.open('ab' if append else 'xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        fcntl.flock(lock,fcntl.LOCK_UN)
    return {'path':str(p),'bytes':len(raw),'sha256':sha(raw)} if not append else None
def log(event,**v):write(AUD,'source_and_call_ledger.jsonl',{'event':event,**v},True)
def identity(pid):
    v=subprocess.check_output(['/bin/ps','-ww','-p',str(pid),'-o','pid=,uid=,pgid=,lstart=,command='],text=True).strip().split(None,8)
    need(len(v)==9,'PROCESS_IDENTITY')
    lines=subprocess.check_output(['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],text=True).splitlines()
    cwd=[s[1:] for s in lines if s.startswith('n')];need(len(cwd)==1,'PROCESS_CWD')
    return {'pid':int(v[0]),'UID':int(v[1]),'PGID':int(v[2]),'birth':' '.join(v[3:8]),'command':v[8],'cwd':cwd[0]}
def live(v):
    try:return identity(v['pid'])==v
    except (OSError,subprocess.CalledProcessError,Stop):return False
def resource_sample(claim):
    text=subprocess.check_output(['/usr/bin/vm_stat'],text=True)
    page=int(re.search(r'page size of (\d+) bytes',text).group(1))
    def pages(n):return int(re.search(r'^'+re.escape(n)+r':\s+(\d+)',text,re.M).group(1))
    rss=0
    for rec in claim['identities'].values():
        if live(rec):
            try:rss+=int(subprocess.check_output(['/bin/ps','-p',str(rec['pid']),'-o','rss='],text=True).strip())*1024
            except (ValueError,subprocess.CalledProcessError):pass
    return {'wall':time.time(),'monotonic':time.monotonic(),'owned_total_RSS_bytes':rss,
            'available_bytes':(pages('Pages free')+pages('Pages inactive'))*page,
            'available_method':'Darwin free+inactive pages','disk_free_bytes':shutil.disk_usage(ROOT).free,
            'MPS_initialized':False,'MPS_driver_bytes':0,'footprint_missing_advisory_only':True}
def limits(v,claim):
    need(v['owned_total_RSS_bytes']<=36*GIB,'RSS_LIMIT')
    need(v['available_bytes']>=4*GIB,'AVAILABLE_MEMORY_LIMIT')
    need(v['disk_free_bytes']>=20*GIB,'DISK_LIMIT')
    need(v['wall']<claim['wall_deadline'] and v['monotonic']<claim['monotonic_deadline'],'PURPOSE_DEADLINE')
    files=outfiles();need(len(files)<=40 and sum(p.stat().st_size for p in files)<=134217728,'OUTPUT_LIMIT')
class Guard:
    def __init__(self,claim):self.claim=claim;self.phase='BOOT';self.last=0
    def check(self,force=False):
        need(time.time()<self.claim['wall_deadline'] and time.monotonic()<self.claim['monotonic_deadline'],'PURPOSE_DEADLINE')
        need(not (AUD/'safety_stop.json').exists(),'WATCHDOG_STOP')
        if force or time.monotonic()-self.last>=10:
            need(live(self.claim['identities']['supervisor']),'SUPERVISOR_IDENTITY_LOST')
            need(live(self.claim['identities']['watchdog']),'WATCHDOG_IDENTITY_LOST')
            v=resource_sample(self.claim);limits(v,self.claim)
            write(AUD,'resource_log.jsonl',{'actor':'worker','phase':self.phase,**v},True);self.last=time.monotonic()
class Calls:
    def __init__(self,budget):
        self.caps={k:{n:v for n,v in budget[k].items() if type(v) is int} for k in ('future_arithmetic','future_secondary_arithmetic')}
        self.used={k:collections.Counter() for k in self.caps}
    def add(self,key,n=1,secondary=False):
        cat='future_secondary_arithmetic' if secondary else 'future_arithmetic'
        need(type(n) is int and n>=0 and key in self.caps[cat],'CALL_KEY_OR_COUNT')
        self.used[cat][key]+=n;need(self.used[cat][key]<=self.caps[cat][key],'CALL_BUDGET_'+key)
    def snapshot(self):return {k:dict(v) for k,v in self.used.items()}
    def complete(self):
        for cat,cap in self.caps.items():
            for k,n in cap.items():need(self.used[cat][k]==n,'INCOMPLETE_CALL_'+k)
class Reader:
    def __init__(self,allow,g):
        self.g=g;self.allow=allow;self.cache={};self.seen=set();self.used=collections.Counter()
        self.registry={}
        for r in allow['controls']:self.registry[r['path']]=(r,'control')
        for r in [allow['panel'],allow['components'],allow['calibrators'],*allow['prediction_archives'],allow['truth']]:
            need(r['path'] not in self.registry,'SOURCE_REGISTRY_DUPLICATE');self.registry[r['path']]=(r,'payload')
    def read(self,r):
        path=r['path'];need(path in self.registry and self.registry[path][0]['sha256']==r['sha256'],'SOURCE_NOT_CLOSED')
        if path in self.cache:return self.cache[path]
        need(path not in self.seen,'NO_SOURCE_RETRY');self.seen.add(path);self.g.check()
        rec,cat=self.registry[path];p=Path(path)
        need(p.is_absolute() and '..' not in p.parts,'SOURCE_PATH')
        need((p==Path(self.allow['truth']['path'])) or p.is_relative_to(ROOT),'SOURCE_ROOT')
        self.used[cat+'_reserved_files']+=1;self.used[cat+'_reserved_bytes']+=r['bytes']
        need(self.used[cat+'_reserved_files']<=(21 if cat=='control' else 24),'SOURCE_FILE_BUDGET')
        need(self.used[cat+'_reserved_bytes']<=(235759 if cat=='control' else 45918852),'SOURCE_BYTE_BUDGET')
        log('SOURCE_RESERVE',category=cat,path=path,bytes=r['bytes'],used=dict(self.used))
        before=p.lstat();need(stat.S_ISREG(before.st_mode) and before.st_size==r['bytes'],'SOURCE_SIZE_TYPE')
        for k,v in r.get('historical_fingerprint',{}).items():need(str(getattr(before,k))==str(v),'SOURCE_HISTORICAL_'+k)
        fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW);self.used[cat+'_opens']+=1
        with os.fdopen(fd,'rb') as f:
            opened=os.fstat(f.fileno());need(fp(opened)==fp(before),'SOURCE_OPEN_RACE')
            raw=f.read(r['bytes']+1)
        after=p.lstat();h=sha(raw);self.used[cat+'_hashes']+=1;self.used[cat+'_bytes_read']+=len(raw)
        need(len(raw)==r['bytes'] and fp(before)==fp(after) and h==r['sha256'],'SOURCE_SHA_OR_STABILITY')
        self.cache[path]=raw;log('SOURCE_COMPLETE',category=cat,path=path,bytes=len(raw),sha256=h,fingerprint=fp(before))
        return raw
class SelectArray:
    def __init__(self,field,value,schema):self.field,self.value,self.schema=field,value,schema
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
                j=ws(j+1) if t[j] in ',:' else ws(end(j))
            need(j<n,'JSON_CONTAINER');return j+1
        m=JNUMBER.match(t,i)
        if m:return m.end()
        for token in ('true','false','null'):
            if t.startswith(token,i):return i+len(token)
        raise Stop('JSON_SYNTAX')
    def value(i,spec):
        i=ws(i)
        if spec is False:return None,end(i)
        if spec is True:return dec.raw_decode(t,i)
        if isinstance(spec,SelectArray):
            need(t[i]=='[','JSON_SELECTED_ARRAY');out=[];i=ws(i+1)
            while t[i]!=']':
                key,j=value(i,{spec.field:True})
                if key.get(spec.field)==spec.value:
                    rec,j=value(i,spec.schema);out.append(rec)
                i=ws(j)
                if t[i]==',':i=ws(i+1)
                else:need(t[i]==']','JSON_SELECTED_COMMA')
            return out,i+1
        if isinstance(spec,dict):
            need(t[i]=='{','JSON_OBJECT');out={};seen=set();i=ws(i+1)
            while t[i]!='}':
                key,i=dec.raw_decode(t,i);need(isinstance(key,str) and key not in seen,'JSON_KEY');seen.add(key)
                i=ws(i);need(t[i]==':','JSON_COLON');sub=spec.get(key,spec.get('*',False))
                v,i=value(i+1,sub)
                if sub is not False:out[key]=v
                i=ws(i)
                if t[i]==',':i=ws(i+1)
                else:need(t[i]=='}','JSON_OBJECT_COMMA')
            return out,i+1
        if isinstance(spec,list):
            need(t[i]=='[','JSON_ARRAY');out=[];i=ws(i+1)
            while t[i]!=']':
                v,i=value(i,spec[0]);out.append(v);i=ws(i)
                if t[i]==',':i=ws(i+1)
                else:need(t[i]==']','JSON_ARRAY_COMMA')
            return out,i+1
        raise Stop('PROJECTION_SPEC')
    result,i=value(0,schema);need(ws(i)==n,'JSON_TRAILING');return result
def control_projection(r,raw):
    cid=r['id']
    if cid in ('C01','C02','C03','C04'):
        prefixes=r['projection']['markdown_heading_prefixes'];active=False;kept=[]
        for line in raw.decode().splitlines():
            if line.startswith('## '):active=any(line.startswith(p) for p in prefixes)
            if active:kept.append(line)
        return {'selected_text_digest':sha('\n'.join(kept).encode()),'selected_line_count':len(kept)}
    if cid=='C17':
        return projected(raw,{'foreign_keys':{k:True for k in ('case_code','target_code','scheme_code')},'source_components':True,'L0_logical_fields':True})
    if cid=='C20':
        spec={k:True for k in ('id','path','bytes','sha256','historical_fingerprint','columns','physical_rows_max')}
        return projected(raw,{'FR10':{'truth_files':SelectArray('id','Y-GATE',spec)}})
    if cid=='C21':return projected(raw,{'summary':{'G':True},'primary':True,'practical_margin':True,'scientific_PASS':True})
    return projected(raw,{k:True for k in r['projection']['json_fields']})
def key(row):return tuple(row[k] for k in IDS)
def canonical(row):return tuple(v.encode('utf8') for v in key(row))
def controls(reader,allow,assets,spec):
    result={}
    for rec in allow['controls']:result[rec['id']]=control_projection(rec,reader.read(rec))
    need(result['C07']['all10_cases_accepted'] is True and result['C07']['remaining_FR20_work']==[],'FR20_NOT_COMPLETE')
    need(result['C08']['engineering_complete'] and result['C08']['finite_descriptive_scientific_outputs_complete'],'FR20_ACCEPTANCE')
    need(result['C07']['FR30_release'] is False and result['C07']['scientific_PASS'] is False,'HISTORICAL_SCOPE_CHANGED')
    need(result['C21']['practical_margin'] is None and result['C21']['scientific_PASS'] is False,'PRACTICAL_GATE_CHANGED')
    need(result['C06']['all_40_slots_complete'] and result['C06']['before_GATE_decode'] and result['C06']['no_more_fit'],'CALIBRATOR_NOT_SEALED')
    need(result['C10']['status']=='ALL10_REQUIRED_SCORE_MAPS_IDENTIFIABLE' and result['C10']['alias_tolerance']==0,'FR20_SCORE_QUALIFICATION')
    need({r['case_id'] for r in result['C10']['cases']}=={r['case_id'] for r in assets['case_mappings']},'FR20_CASE_COVERAGE')
    need(result['C11']['maximum_abs_residual']<=TOL,'FR20_NUMERIC_QUALIFICATION')
    need(result['C12']['status']=='PASS_IMPLEMENTATION_ONLY','FR20_AFFINE_QUALIFICATION')
    for k in ('all_logical_identities_recoverable_via_L0','physical_columns_bound_by_schema','source_id_is_original_GATE_index'):
        need(result['C14'][k] is True,'FR20_PHYSICAL_LAYOUT')
    need(result['C14']['lossy_values'] is False,'FR20_LOSSY_LAYOUT')
    need(result['C17']['foreign_keys']['case_code']==assets['case_mappings'],'CASE_CODE_MAP')
    need(result['C17']['source_components']['physical_columns']==allow['components']['column_names'],'COMPONENT_COLUMN_MAP')
    for cid,rec in [('C09',allow['components']),('C15',allow['panel']),('C06',allow['calibrators'])]:
        matches=[x for x in result[cid]['artifacts'] if x['path']==rec['path']]
        need(len(matches)==1 and all(matches[0][k]==rec[k] for k in ('path','bytes','sha256')),'PARENT_ARTIFACT_DESCRIPTOR')
    truth=result['C20']['FR10']['truth_files'];need(len(truth)==1,'TRUTH_CONTROL_FILTER')
    need(all(truth[0][k]==allow['truth'][k] for k in ('path','bytes','sha256','columns','historical_fingerprint')),'TRUTH_DESCRIPTOR_LINEAGE')
    write(AUD,'entry_receipt.json',{'status':'FINITE_DESCRIPTIVE_ENTRY_CONTROL_PASS','cases':10,'historical_practical_margin':None,'original_practical_gate':'NOT_ADJUDICATED','MODEL_GATE':'EXPOSED_DEVELOPMENT_ONLY','full_GATE_J_not_substituted_for_J30':True,'qI_magnitude_not_entry':True,'FR40_release':False,'control_projection_ids':list(result)})
def load_panel(reader,allow,calls):
    schema={k:([True] if k=='case_ids' else True) for k in allow['panel']['projection']}
    rows=[projected(v,schema) for v in reader.read(allow['panel']).splitlines() if v.strip()]
    need(len(rows)==2242 and len({key(v) for v in rows})==2242 and len({v['source_id'] for v in rows})==2242,'PANEL_CARDINALITY')
    need(rows==sorted(rows,key=canonical),'PANEL_CANONICAL_ORDER')
    expected=[x['case_id'] for x in allow['_cases']]
    need(collections.Counter(v['endpoint'] for v in rows)==N,'PANEL_ENDPOINT_COUNTS')
    for row in rows:
        need(type(row['source_id']) is int and 0<=row['source_id']<5697,'SOURCE_ID_ORIGINAL_GATE_INDEX')
        need(all(isinstance(row[k],str) and row[k] for k in (*IDS,'smiles_sha256','target_sequence_key')),'PANEL_IDENTITY_FIELDS')
        need(row['v3_3_role']=='MODEL_GATE' and row['case_ids']==expected,'PANEL_ROLE_CASES')
        need(type(row['n_reference_rows']) is int and type(row['n_donor_proteins']) is int and 0<row['n_donor_proteins']<=row['n_reference_rows'],'REFERENCE_METADATA')
        need(type(row['identical_weights']) is bool,'WEIGHT_FLAG')
    need({e:sum(v['n_reference_rows'] for v in rows if v['endpoint']==e) for e in N}=={'Kd':8150,'Ki':314509},'REFERENCE_LINK_COUNTS')
    return rows
def load_components(reader,allow,rows,calls,np):
    index={v['source_id']:i for i,v in enumerate(rows)};Q=np.empty((10,2,2242),dtype=np.float64);I=np.empty_like(Q);seen=set()
    count=primary=0
    for line in reader.read(allow['components']).splitlines():
        if not line.strip():continue
        body=line.strip();need(body[:1]==b'[' and body[-1:]==b']','COMPONENT_ARRAY')
        tokens=[v.strip() for v in body[1:-1].split(b',')]
        need(len(tokens)==17 and all(NUMBER.fullmatch(v) for v in tokens),'COMPONENT_LEXICAL_LAYOUT')
        need(all(re.fullmatch(rb'0|[1-9][0-9]*',v) for v in tokens[:4]),'COMPONENT_CODE_TYPE')
        ci,sid,target,pol=(int(v) for v in tokens[:4]);count+=1
        need(0<=ci<10 and sid in index and target in (0,1,2) and pol in (0,1),'COMPONENT_FOREIGN_KEY')
        k=(ci,sid,target,pol);need(k not in seen,'COMPONENT_DUPLICATE');seen.add(k)
        if target==0:
            q,i=float(tokens[4]),float(tokens[8]);need(math.isfinite(q) and math.isfinite(i),'COMPONENT_REQUIRED_NONFINITE')
            Q[ci,pol,index[sid]]=q;I[ci,pol,index[sid]]=i;primary+=1
            calls.add('qI_scalar_lookups')
        if count%4096==0:reader.g.check()
    need(count==134520 and primary==44840,'COMPONENT_COMPLETE_COUNTS')
    reader.used.update({'components_rows_envelope':count,'components_envelope_integer_cells':count*4,'components_primary_rows':primary,'components_allowed_numeric_cells':primary*2,'panel_rows':2242})
    return Q,I
def calibrators(reader,allow):
    fields={k:True for k in ('case_id','endpoint','family','mode','FIT_rows','slot_sha256')};fields.update(knots=[True],coefficients=[True])
    data=projected(reader.read(allow['calibrators']),{'slots':[fields]});out={}
    for s in data['slots']:
        k=(s['case_id'],s['endpoint'],s['family']);need(k not in out,'CALIBRATOR_SLOT_DUPLICATE');out[k]=s
        need(re.fullmatch('[0-9a-f]{64}',s['slot_sha256']) is not None,'CALIBRATOR_SLOT_ID')
        need(s['FIT_rows']==(6456 if s['endpoint']=='Kd' else 39684),'CALIBRATOR_FIT_ROWS')
        need(s['family'] in ('affine','piecewise') and len(s['coefficients'])==2+len(s['knots']),'CALIBRATOR_SCHEMA')
        need(all(type(v) in (int,float) and math.isfinite(v) for v in s['coefficients']+s['knots']),'CALIBRATOR_NONFINITE')
        if s['family']=='affine':need(s['knots']==[],'AFFINE_KNOTS')
    need(len(out)==40,'CALIBRATOR40');return out
def decode_score(reader,rec,indices,np):
    raw=reader.read(rec);name=next(iter(rec['decode_members']))
    reader.used.update({'prediction_archives':1,'prediction_archive_bytes':len(raw),'prediction_members':1,'prediction_scalars':5697})
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        need(z.namelist().count(name)==1,'NPZ_MEMBER_ID')
        arr=np.load(io.BytesIO(z.read(name)),allow_pickle=False)
    need(arr.shape==(5697,) and arr.dtype==np.dtype('float32'),'SAVED_SCORE_SHAPE_DTYPE')
    selected=arr[indices].astype(np.float64);del arr
    reader.used.update({'prediction_numeric_selected_for_use':2242,'prediction_incidental_discarded':3455})
    need(bool(np.isfinite(selected).all()),'SELECTED_SCORE_NONFINITE');return selected
def mean_array(a):return math.fsum(float(x) for x in a)/len(a)
def aggregate(raw,arm,seed,ep,*tail):
    if seed=='MEAN5':return math.fsum(aggregate(raw,arm,s,ep,*tail) for s in SEEDS)/5
    if ep=='KD_KI_EQUAL':return (aggregate(raw,arm,seed,'Kd',*tail)+aggregate(raw,arm,seed,'Ki',*tail))/2
    return raw[(arm,seed,ep,*tail)]
def scope(arm,seed,ep,pol=None):
    v={'arm':arm,'seed_layer':seed,'endpoint_layer':ep,'target_id':'q','source_denominator_map':dict(N)}
    if pol is not None:v['reference_scheme']=pol
    return v
def full_scopes():
    for arm in ARMS:
        for seed in LAYERS:
            for ep in EPS:yield arm,seed,ep
def prepare_predictions(reader,allow,assets,rows,Q,I,calls,np):
    slots=calibrators(reader,allow);indices=np.array([r['source_id'] for r in rows],dtype=np.int64)
    masks={e:np.array([r['endpoint']==e for r in rows]) for e in N};pred={};mraw={};draw={};hashes=[];maxq=maxpair=0.
    recs={(r['case_id'],r['function']):r for r in allow['prediction_archives']}
    for case in assets['case_mappings']:
        reader.g.phase='QUALIFY_'+case['case_id'];ci=case['code'];name=case['case_id'];arm,seed=case['arm'],case['seed']
        h0=decode_score(reader,recs[(name,'h0')],indices,np);h1=decode_score(reader,recs[(name,'h1')],indices,np)
        aff=np.empty(2242,dtype=np.float64);one=np.empty_like(aff)
        for ep,mask in masks.items():
            for family,dest in [('affine',aff),('piecewise',one)]:
                s=slots[(name,ep,family)];co=s['coefficients'];t=h0[mask];value=co[0]*t+co[1]
                for theta,knot in zip(co[2:],s['knots']):value=value+theta*np.maximum(t-knot,0.)
                need(bool(np.isfinite(value).all()),'CALIBRATED_SCORE_NONFINITE');dest[mask]=value
                calls.add('calibrator_scalar_applications',int(mask.sum()))
        recon=h1-aff;calls.add('q_source_reconstructions',2242)
        er=float(np.max(np.abs(recon-Q[ci,0])));pa=float(np.max(np.abs(Q[ci,0]-Q[ci,1])))
        calls.add('q_source_reconstruction_checks',2242);calls.add('q_source_policy_consistency_pairs',2242)
        need(math.isfinite(er) and math.isfinite(pa) and er<=TOL and pa<=TOL,'Q_REUSE_NUMERIC')
        maxq=max(maxq,er);maxpair=max(maxpair,pa)
        f={'h0':h0,'h_aff':aff,'h_1d':one,'h1':h1}
        for pi,mname in [(0,'minus_main'),(1,'minus_sensitivity')]:
            f[mname]=h1-I[ci,pi];calls.add('deletion_prediction_scalars',2242)
            amp=np.abs(I[ci,pi]);calls.add('M_I_abs_contributions',2242,True)
            for ep,mask in masks.items():mraw[(arm,seed,ep,POLS[pi])]=mean_array(amp[mask])
        da=np.abs(I[ci,0]-I[ci,1]);calls.add('D_ref_paired_abs_difference_contributions',2242,True)
        for ep,mask in masks.items():draw[(arm,seed,ep)]=mean_array(da[mask])
        for fn in FUNCS:
            need(bool(np.isfinite(f[fn]).all()),'PREDICTION_NONFINITE');bb=np.asarray(f[fn],dtype='<f8').tobytes()
            hashes.append({'case_id':name,'function':fn,'shape':[2242],'dtype':'little_endian_float64','sha256':sha(bb)})
            calls.add('truth_blind_prediction_buffer_digest_arrays');calls.add('truth_blind_prediction_buffer_digest_bytes',len(bb))
        pred[(arm,seed)]=f;reader.g.check()
    M={};D={};ml=[];dl=[]
    for arm in ARMS:
        for pol in POLS:
            for seed in LAYERS:
                for ep in EPS:
                    v=aggregate(mraw,arm,seed,ep,pol);need(math.isfinite(v) and v>=0,'M_DOMAIN')
                    M[(arm,seed,ep,pol)]=v;ml.append({**scope(arm,seed,ep,pol),'value':v})
                    calls.add('M_I_summary_values',1,True);calls.add('M_I_value_domain_validations',1,True)
        for seed in LAYERS:
            for ep in EPS:
                v=aggregate(draw,arm,seed,ep);need(math.isfinite(v) and v>=0,'D_DOMAIN')
                D[(arm,seed,ep)]=v;dl.append({**scope(arm,seed,ep),'value':v,'policy_pair':list(POLS)})
                calls.add('D_ref_summary_values',1,True);calls.add('D_ref_value_domain_validations',1,True)
    block=np.array([x['value'] for x in ml]+[x['value'] for x in dl],dtype='<f8').tobytes()
    calls.add('unlabeled_summary_digest_blocks',1,True);calls.add('unlabeled_summary_digest_values',108,True);calls.add('unlabeled_summary_digest_bytes',len(block),True)
    write(AUD,'reuse_qualification_receipt.json',{'status':'ALL10_FULL_PANEL_QUALIFIED','case_count':10,'source_counts':N,'q_reconstruction_max_abs':maxq,'q_policy_max_abs':maxpair,'implementation_tolerance':TOL,'not_effect_or_sign_bound':True,'source_components_numeric_columns':[4,8],'slot_sha256':[s['slot_sha256'] for s in slots.values()],'singleton_alias_scope_retained':True})
    calls.add('prediction_recipe_seals')
    write(AUD,'prediction_recipe_seal.json',{'before_y_true_decode':True,'case_count':10,'source_rows':2242,'source_key_order_sha256':sha(enc([list(key(r)) for r in rows])),'prediction_arrays':hashes,'array_count':60,'formula':'h1_i-bar_qI_i then source absolute error; no seed prediction ensemble','unlabeled_summary_digest':{'sha256':sha(block),'values':108,'bytes':len(block),'dtype':'little_endian_float64','M_keys':[scope(x['arm'],x['seed_layer'],x['endpoint_layer'],x['reference_scheme']) for x in ml],'D_keys':[scope(x['arm'],x['seed_layer'],x['endpoint_layer']) for x in dl]},'scientific_package_seal':reader.allow['_package_seal'],'historical_GATE_exposure_unchanged':True})
    log('ALL10_PRETRUTH_QUALIFICATION_COMPLETE',calls=calls.snapshot(),sources=dict(reader.used))
    return pred,masks,M,D,ml,dl
class Projection:
    def __init__(self,raw,allowed):
        import pyarrow.parquet as pq
        need(raw[:4]==raw[-4:]==b'PAR1','PARQUET_MAGIC')
        footer=struct.unpack('<I',raw[-8:-4])[0];start=len(raw)-8-footer;need(4<=start<len(raw)-8,'PARQUET_FOOTER')
        meta=pq.read_metadata(io.BytesIO(b'PAR1'+raw[start:]))
        need(meta.num_rows==5697 and set(allowed)<=set(meta.schema.names),'PARQUET_SCHEMA_ROWS')
        chunks=[]
        for gi in range(meta.num_row_groups):
            group=meta.row_group(gi)
            for ci in range(group.num_columns):
                col=group.column(ci);offsets=[x for x in (col.dictionary_page_offset,col.data_page_offset) if x is not None and x>0]
                need(bool(offsets),'PARQUET_OFFSET');lo=min(offsets);hi=lo+col.total_compressed_size
                need(4<=lo<hi<=start,'PARQUET_CHUNK_RANGE');chunks.append((col.path_in_schema,lo,hi))
        for name,lo,hi in chunks:
            if name in allowed:need(all(other==name or max(lo,a)>=min(hi,b) for other,a,b in chunks),'PARQUET_CHUNK_OVERLAP')
        self.mask=bytearray(len(raw));self.mask[:4]=b'PAR1';self.mask[start:]=raw[start:]
        self.raw,self.allowed,self.chunks,self.done=raw,set(allowed),chunks,set()
    def read(self,columns):
        import pyarrow.parquet as pq
        need(set(columns)<=self.allowed and not self.done.intersection(columns),'PARQUET_COLUMN_REPEAT_OR_SCOPE')
        for name,lo,hi in self.chunks:
            if name in columns:self.mask[lo:hi]=self.raw[lo:hi]
        table=pq.ParquetFile(io.BytesIO(self.mask)).read(columns=columns,use_threads=False)
        need(table.num_rows==5697,'PARQUET_PROJECTION_ROWS');self.done.update(columns)
        return {k:table[k].to_pylist() for k in columns}
def truth(reader,allow,rows,np):
    reader.g.phase='ONE_TRUTH_PROJECTION';r=allow['truth'];pr=Projection(reader.read(r),r['columns'])
    ids=pr.read(list(IDS));keys=list(zip(*(ids[k] for k in IDS)));del ids
    need(all(all(isinstance(x,str) and x for x in k) for k in keys),'TRUTH_KEY_TYPE')
    need(len(keys)==len(set(keys))==5697 and collections.Counter(k[2] for k in keys)=={'Kd':798,'Ki':4899},'TRUTH_IDENTITIES')
    pos={k:i for i,k in enumerate(keys)};need(all(key(r) in pos for r in rows),'TRUTH_PANEL_JOIN')
    reader.used['truth_identity_cells']+=5697*3
    need((AUD/'prediction_recipe_seal.json').exists(),'NO_PRETRUTH_RECIPE')
    vals=pr.read(['y_true'])['y_true'];reader.used['truth_numeric_decoded']+=5697
    selected=[vals[pos[key(r)]] for r in rows];del vals,pr,keys,pos
    reader.used.update({'truth_numeric_used':2242,'truth_numeric_discarded_before_numeric_use':3455,'truth_file_opens':1,'truth_file_bytes':r['bytes'],'truth_columns':4})
    need(all(type(v) in (int,float) and math.isfinite(v) for v in selected),'SELECTED_TRUTH_NONFINITE_OR_TYPE')
    arr=np.asarray(selected,dtype=np.float64);del selected
    write(AUD,'truth_projection_and_exposure_receipt.json',{'source':r['path'],'columns':r['columns'],'identities':5697,'decoded_labels':5697,'used_labels':2242,'discarded_before_numeric_use':3455,'M1_predictions_decoded':False,'other_role_or_corner_labels':0,'pretruth_recipe_bound':True,'MODEL_GATE':'EXPOSED_DEVELOPMENT_ONLY','source_truth_values_written':0})
    return arr
def risk_analysis(reader,rows,pred,masks,M,D,ml,dl,calls,np):
    y=truth(reader,reader.allow,rows,np);riskraw={};compr={};count_receipts=[]
    for arm in ARMS:
        for seed in SEEDS:
            reader.g.phase='RISK_'+arm+'_'+str(seed);errors={}
            for fn in FUNCS:
                err=np.abs(y-pred[(arm,seed)][fn]);calls.add('unique_absolute_error_scalars',2242)
                need(bool(np.isfinite(err).all()),'LOSS_NONFINITE');errors[fn]=err
                for ep,mask in masks.items():riskraw[(arm,seed,ep,fn)]=mean_array(err[mask])
            for pi,pol in enumerate(POLS):
                delta=errors['minus_main' if pi==0 else 'minus_sensitivity']-errors['h1']
                calls.add('delta_loss_differences',2242,True);calls.add('delta_finite_validations',2242,True)
                need(bool(np.isfinite(delta).all()),'DELTA_NONFINITE')
                plus=np.maximum(delta,0.);minus=np.maximum(-delta,0.);calls.add('positive_and_negative_part_contributions',4484,True);calls.add('exclusive_sign_classifications',2242,True)
                for ep,mask in masks.items():
                    dd=delta[mask];n=N[ep];np_=int(np.count_nonzero(dd>0));nm=int(np.count_nonzero(dd<0));nz=int(np.count_nonzero(dd==0))
                    need(np_+nm+nz==n,'RAW_INTEGER_COUNTS')
                    row={'A_plus':mean_array(plus[mask]),'A_minus':mean_array(minus[mask]),'p_plus':np_/n,'p_minus':nm/n,'p_zero':nz/n,'n_plus':np_,'n_minus':nm,'n_zero':nz,'N_e':n}
                    compr[(arm,seed,ep,pol)]=row;calls.add('raw_integer_count_exact_checks',1,True)
                    count_receipts.append({**scope(arm,seed,ep,pol),'n_plus':np_,'n_minus':nm,'n_zero':nz,'N_e':n,'integer_residual':np_+nm+nz-n,'status':'PASS_EXACT_INTEGER'})
            reader.g.check()
    risks={};riskviews=[];diff={};diffrows=[];comp=[];identities=[];policybounds=[];refbounds=[];refviews=[];algebra=[]
    cfields=('A_plus','A_minus','p_plus','p_minus','p_zero')
    craw={fld:{k:v[fld] for k,v in compr.items()} for fld in cfields}
    for arm,seed,ep in full_scopes():
        for fn in FUNCS:risks[(arm,seed,ep,fn)]=aggregate(riskraw,arm,seed,ep,fn);calls.add('unique_risk_summary_slots')
        rh1=risks[(arm,seed,ep,'h1')]
        jaff=risks[(arm,seed,ep,'h_aff')]-rh1;jone=risks[(arm,seed,ep,'h_1d')]-rh1;calls.add('unique_difference_slots',2)
        for pi,pol in enumerate(POLS):
            rm=risks[(arm,seed,ep,'minus_main' if pi==0 else 'minus_sensitivity')]
            vals={'C_I':rm-rh1,'J30_aff':jaff,'J30_1d':jone,'J30_minusI_aff':risks[(arm,seed,ep,'h_aff')]-rm,'J30_minusI_1d':risks[(arm,seed,ep,'h_1d')]-rm};diff[(arm,seed,ep,pol)]=vals;calls.add('unique_difference_slots',3)
            for fn in ('h0','h_aff','h_1d','h1_minus_bar_qI','h1'):
                value=rm if fn=='h1_minus_bar_qI' else risks[(arm,seed,ep,fn)]
                riskviews.append({**scope(arm,seed,ep,pol),'function_id':fn,'risk':value,'status':'FINITE_DESCRIPTIVE'});calls.add('five_function_risk_views')
            for name,value in vals.items():
                diffrows.append({**scope(arm,seed,ep,pol),'contrast_id':name,'difference':value,'raw_sign':1 if value>0 else (-1 if value<0 else 0),'status':'FINITE_DESCRIPTIVE_NOT_PRACTICAL_PASS'});calls.add('five_difference_views')
            for j,res in [('J30_aff','J30_minusI_aff'),('J30_1d','J30_minusI_1d')]:
                residual=vals[j]-(vals['C_I']+vals[res]);need(abs(residual)<=TOL,'RISK_ALGEBRA_IDENTITY');calls.add('risk_algebra_identity_checks')
                algebra.append({**scope(arm,seed,ep,pol),'identity':j,'residual':residual})
            cp={fld:aggregate(craw[fld],arm,seed,ep,pol) for fld in cfields}
            need(all(math.isfinite(v) for v in cp.values()) and cp['A_plus']>=0 and cp['A_minus']>=0 and all(0<=cp[f]<=1 for f in ('p_plus','p_minus','p_zero')),'COMPOSITION_DOMAIN')
            calls.add('composition_row_domain_validations',1,True)
            counts={f:compr[(arm,seed,ep,pol)][f] for f in ('n_plus','n_minus','n_zero','N_e')} if type(seed) is int and ep in N else {f:None for f in ('n_plus','n_minus','n_zero','N_e')}
            comp.append({**scope(arm,seed,ep,pol),**cp,**counts});calls.add('composition_summary_records',1,True);calls.add('composition_primary_numeric_fields',5,True)
            rc=vals['C_I']-(cp['A_plus']-cp['A_minus']);rp=math.fsum(cp[f] for f in ('p_plus','p_minus','p_zero'))-1
            calls.add('composition_float_identity_checks',1,True);calls.add('proportion_normalization_checks',1,True)
            need(abs(rc)<=TOL and abs(rp)<=TOL,'COMPOSITION_OR_PROPORTION_IDENTITY');identities.append({**scope(arm,seed,ep,pol),'r_composition':rc,'r_proportion':rp})
            B=cp['A_plus']+cp['A_minus'];calls.add('B_abs_delta_summary_sums',1,True);mv=M[(arm,seed,ep,pol)]
            rr={'r_C_to_B':abs(vals['C_I'])-B,'r_B_to_M':B-mv,'r_M':abs(vals['C_I'])-mv}
            for ck in ('full_chain_C_to_B_checks','full_chain_B_to_M_checks','retained_end_to_end_M_checks'):calls.add(ck,1,True);calls.add('bound_inequality_checks_total',1,True)
            need(all(math.isfinite(v) and v<=TOL for v in rr.values()),'BOUND_CHAIN_NUMERIC')
            policybounds.append({**scope(arm,seed,ep,pol),'C_I':vals['C_I'],'A_plus':cp['A_plus'],'A_minus':cp['A_minus'],'B_abs_delta':B,'M_I':mv,**rr,'status':'PASS_IMPLEMENTATION_ONLY'})
        dc=diff[(arm,seed,ep,POLS[1])]['C_I']-diff[(arm,seed,ep,POLS[0])]['C_I'];calls.add('reference_CI_differences')
        for name,v in [('delta_CI',dc),('delta_J30_minusI_aff',-dc),('delta_J30_minusI_1d',-dc)]:
            refviews.append({**scope(arm,seed,ep),'contrast_id':name,'difference':v,'direction':'sensitivity_minus_main','status':'FINITE_DESCRIPTIVE'});calls.add('reference_difference_views')
        dv=D[(arm,seed,ep)];rd=abs(dc)-dv;calls.add('reference_D_checks',1,True);calls.add('bound_inequality_checks_total',1,True)
        need(math.isfinite(rd) and rd<=TOL,'REFERENCE_BOUND_NUMERIC')
        refbounds.append({**scope(arm,seed,ep),'C_I_main':diff[(arm,seed,ep,POLS[0])]['C_I'],'C_I_sensitivity':diff[(arm,seed,ep,POLS[1])]['C_I'],'delta_CI':dc,'D_ref':dv,'r_D':rd,'status':'PASS_IMPLEMENTATION_ONLY'})
    write(OUT,'five_function_risks.json',{'rows':riskviews,'row_count':360,'unique_numeric_slots':216,'source_counts':N,'seed_prediction_ensemble':False})
    write(OUT,'five_risk_differences.json',{'rows':diffrows,'row_count':360,'unique_numeric_slots':288,'secondary_composition':comp,'primary':diff[('G','MEAN5','KD_KI_EQUAL',POLS[0])]['C_I'],'practical_margin':None,'scientific_practical_PASS':False})
    write(OUT,'reference_sensitivity.json',{'M_I':ml,'D_ref':dl,'risk_difference_views':refviews,'views':108,'policies':list(POLS),'same_complete_panel':True,'reference_choice_after_results':False})
    write(OUT,'numerical_identity_receipt.json',{'status':'PASS_IMPLEMENTATION_ONLY','tolerance':TOL,'risk_algebra':algebra,'composition_identities':identities,'raw_count_equalities':count_receipts,'policy_bounds':policybounds,'reference_bounds':refbounds,'bound_checks':252,'no_clipping':True,'effect_or_sign_error_bound':False})
    return risks,diff,comp,M,D,policybounds,refbounds
def make_report(analysis,calls,reader):
    risks,diffs,comp,M,D,pb,rb=analysis
    rows=['# FR30 v0.4阶段研究与科学结论','',
          'MODEL_GATE为已暴露开发证据。固定O_SRC共同域Kd206、Ki2036，共2242源记录；S/G各五seed。所有结果为有限面板描述，不裁决实用优越、等效或总体确认。','',
          '| 管线 | 参考 | h0 | h_aff | h_1d | h1−qI | h1 | C_I | J30_aff | J30_1d | 删减残余aff | 删减残余1d |',
          '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    primary={}
    for arm in ARMS:
        for pi,pol in enumerate(POLS):
            k=(arm,'MEAN5','KD_KI_EQUAL');d=diffs[(*k,pol)]
            values=[risks[(*k,f)] for f in ('h0','h_aff','h_1d')]+[risks[(*k,'minus_main' if pi==0 else 'minus_sensitivity')],risks[(*k,'h1')]]+[d[x] for x in ('C_I','J30_aff','J30_1d','J30_minusI_aff','J30_minusI_1d')]
            rows.append('| '+arm+' | '+pol+' | '+' | '.join(format(v,'.12g') for v in values)+' |')
            cp=next(x for x in comp if x['arm']==arm and x['seed_layer']=='MEAN5' and x['endpoint_layer']=='KD_KI_EQUAL' and x['reference_scheme']==pol)
            primary[(arm,pol)]={'differences':d,'composition':{x:cp[x] for x in ('A_plus','A_minus','p_plus','p_minus','p_zero')},'M_I':M[(*k,pol)],'D_ref':D[k]}
    rows+=['','## 有限描述性判定','']
    for arm in ARMS:
        for pol in POLS:
            d=primary[(arm,pol)]['differences'];ci=d['C_I']
            connection=d['J30_aff']>0 and d['J30_1d']>0
            direction='保留qI观察风险更低' if ci>0 else ('指定删减观察风险更低' if ci<0 else '未舍入计算差精确为0')
            residual=d['J30_minusI_aff']>0 and d['J30_minusI_1d']>0
            rows.append(f'- {arm}／{pol}：{direction}；同面板双校准观察优势'+('成立' if connection else '不成立')+'；删减后相对两校准观察优势'+('仍成立。' if residual else '不同时成立。'))
        main=primary[(arm,POLS[0])]['differences']['C_I'];sens=primary[(arm,POLS[1])]['differences']['C_I']
        rows.append('- '+arm+'两参考C_I方向'+('改变，描述性结论具有参考依赖。' if (main>0)!=(sens>0) or (main<0)!=(sens<0) else '在此有限汇总上相同；不据此颁发参考稳健性PASS。'))
    rows+=['','## 损益构成（端点等权、seed平均）','',
           '| 管线 | 参考 | A_plus | A_minus | p_plus | p_minus | p_zero | M_I | D_ref |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for arm in ARMS:
        for pol in POLS:
            v=primary[(arm,pol)];a=v['composition']
            rows.append('| '+arm+' | '+pol+' | '+' | '.join(format(x,'.12g') for x in [*(a[k] for k in ('A_plus','A_minus','p_plus','p_minus','p_zero')),v['M_I'],v['D_ref']])+' |')
    rows+=['','比例是既定端点／seed加权平均，不能解释为同一批独特源跨五seed持续受益或独立生物样本受益率。不同源的损失差抵消区别于FR20同源参考合成抵消；不计算收益／病因比例。','',
           '## 完整性与预算','',
           '全部五函数、五差值、全部seed与端点、两套参考以及反向结果均保存在本目录JSON报告。252个界核验、144个构成／比例恒等式、40个原始整数计数检查通过；1e−8仅为实现余量，不是效应或符号可靠性误差界。','',
           '先合成源内参考预测，再计算每源误差；先逐模型形成端点风险，再合成EBMAE及平均五seed。无模型、梯度、XAI、PLM、拟合、训练、CI、p值、bootstrap、测试、canary或重试。原实用门禁NOT_ADJUDICATED；FR40未释放。','',
           '## 与既有研究的关系','',
           '本轮增加指定qI锚定删减与同子域观察风险的连接，或其反向／受限证据。不能据此把qI确定为H3唯一历史原因、证明h0之外的新任务信息、宣称删除全部交互、生物机制或普遍SMILES/GINE优越性。H1、H2、H3、路线1、NP-01旧裁决与封印保持不变；没有新增XAI附加价值验证。']
    rows+=['','## 工程承接','', '首次监督调度在worker创建前被时钟域不兼容检查拦截，研究来源／调用消费均为0。新版本保留原墙钟起点和截止，保守转换剩余单调时钟预算；新增工程控制读取单列承接。科学计算只运行一次，未重试研究或改科学定义。']
    calls.add('finite_report')
    p=OUT/'stage_report.md';raw=('\n'.join(rows)+'\n').encode()
    need(not p.exists(),'REPORT_EXISTS')
    with (AUD/'.output_write_lock').open('ab') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        files=outfiles();need(len(files)+1<=40 and sum(x.stat().st_size for x in files)+len(raw)<=134217728,'REPORT_OUTPUT_BUDGET')
        with p.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        fcntl.flock(lock,fcntl.LOCK_UN)
    return {'primary_scope':'G/FIT_ROW_UNIFORM/MEAN5/KD_KI_EQUAL','primary_C_I':primary[('G',POLS[0])]['differences']['C_I'],
            'summary':{arm:{pol:primary[(arm,pol)] for pol in POLS} for arm in ARMS},'practical_margin':None,'original_practical_gate':'NOT_ADJUDICATED','FR40_release':False}
def worker(envelope):
    binding=envelope['binding'];claim=envelope['claim'];own=envelope['own'];g=Guard(claim);calls=None;reader=None;status='ENGINEERING_STOP';err=None;summary=None
    try:
        deadline=time.monotonic()+30
        while not (AUD/'watchdog_ready.json').exists():
            need(time.monotonic()<deadline,'WATCHDOG_HANDSHAKE_TIMEOUT');time.sleep(.05)
        ready=json.loads((AUD/'watchdog_ready.json').read_bytes());need(ready['identity']==claim['identities']['watchdog'],'WATCHDOG_HANDSHAKE_IDENTITY')
        g.check(True)
        cfg={Path(k).name:json.loads(v) for k,v in own.items() if Path(k).parent==PKG and k.endswith('.json')}
        allow=cfg['input_allowlist.json'];assets=cfg['asset_bindings.json'];budget=cfg['budget.json'];spec=cfg['design_spec.json']
        need(binding['execution_authorized'] and binding['contract_id']==CID and cfg['scientific_freeze.json']['current_total_frozen_items']==7,'CURRENT_AUTHORITY_OR_FREEZE')
        allow['_cases']=assets['case_mappings'];allow['_package_seal']=binding['scientific_package_seal'];allow['_engineering_recovery']=binding.get('engineering_recovery')
        calls=Calls(budget);reader=Reader(allow,g);g.phase='CONTROLS'
        log('FULL_FIXED_BUDGET_RESERVED',original=budget['future_arithmetic'],secondary=budget['future_secondary_arithmetic'],source_caps=budget['future_sources'],runtime_start=binding['runtime']['clock'],engineering_control_exception=binding['engineering_recovery'])
        controls(reader,allow,assets,spec)
        import numpy as np
        import pyarrow as pa
        need(np.__version__=='1.26.4' and pa.__version__=='16.1.0','RUNTIME_VERSION_CHANGED')
        need(str(Path(np.core._multiarray_umath.__file__).resolve())==binding['runtime']['numpy_binary']['path'] and str(Path(pa.lib.__file__).resolve())==binding['runtime']['pyarrow_binary']['path'],'RUNTIME_LIBRARY_PATH_CHANGED')
        pa.set_cpu_count(1);pa.set_io_thread_count(1)
        g.phase='PANEL_COMPONENTS';rows=load_panel(reader,allow,calls);Q,I=load_components(reader,allow,rows,calls,np)
        write(AUD,'closed_input_binding.json',{'contract_id':CID,'own_package':binding['own_descriptors'],'binding_path':binding['binding_path'],'binding_sha256':binding['binding_sha256'],'controls':[{'id':r['id'],'path':r['path'],'bytes':r['bytes'],'sha256':r['sha256']} for r in allow['controls']],'payloads':[{'path':r['path'],'bytes':r['bytes'],'sha256':r['sha256']} for r in [allow['panel'],allow['components'],allow['calibrators'],*allow['prediction_archives'],allow['truth']]],'source_counts':N,'source_id_is_original_GATE_index':True,'record_triple_join':True,'reference_body_reads':0})
        pred,masks,M,D,ml,dl=prepare_predictions(reader,allow,assets,rows,Q,I,calls,np)
        analysis=risk_analysis(reader,rows,pred,masks,M,D,ml,dl,calls,np)
        summary=make_report(analysis,calls,reader);calls.complete()
        need(reader.used['control_opens']==21 and reader.used['control_bytes_read']==235759 and reader.used['payload_opens']==24 and reader.used['payload_bytes_read']==45918852,'FINAL_SOURCE_BUDGET')
        expected={'prediction_archives':20,'prediction_members':20,'prediction_scalars':113940,'prediction_numeric_selected_for_use':44840,'prediction_incidental_discarded':69100,'truth_numeric_decoded':5697,'truth_numeric_used':2242,'truth_numeric_discarded_before_numeric_use':3455,'components_rows_envelope':134520,'components_allowed_numeric_cells':89680}
        need(all(reader.used[k]==v for k,v in expected.items()),'FINAL_SOURCE_COUNTS')
        g.phase='FINAL_ACCEPTANCE';g.check(True)
        write(OUT,'checkpoint_manifest_reference_only.json',{'cases':assets['cases'],'checkpoint_cache_stat_open_hash_decode':0,'new_models':0})
        write(OUT,'claim_scope.json',{'status':'COMPLETE_FINITE_DEVELOPMENT_DESCRIPTIVE_FR30','summary':summary,'MODEL_GATE':'EXPOSED_DEVELOPMENT_ONLY','O':'O_SRC_METADATA_LINKED_NOT_O_ENCODED','practical_scientific_PASS':False,'original_practical_gate':'NOT_ADJUDICATED','FR40_release':False,'H1_H2_H3_route1_NP01':'UNCHANGED','XAI_incremental_value':'NOT_TESTED','population_or_biological_causality':False})
        status='COMPLETE_FR30_FINITE_DESCRIPTIVE'
    except BaseException as exc:
        err={'code':str(exc) if isinstance(exc,Stop) else 'ENGINEERING_'+type(exc).__name__,'frames':[{'file':Path(x.filename).name,'line':x.lineno,'function':x.name} for x in traceback.extract_tb(exc.__traceback__)]}
        status='STOPPED' if isinstance(exc,Stop) else 'ENGINEERING_STOP'
        write(AUD,'stop_record.json',{'status':status,'phase':g.phase,'error':err,'retry':False,'source_baseline_reset':False})
    finally:
        log('WORKER_FINAL',status=status,calls=calls.snapshot() if calls else {},sources=dict(reader.used) if reader else {})
        write(OUT,'execution_receipt.json',{'status':status,'phase':g.phase,'error':err,'summary':summary,'source_spending':dict(reader.used) if reader else {},'calls':calls.snapshot() if calls else {},'model_calls':0,'gradient_XAI_PLM_training':0,'CI_p_bootstrap_tests_scientific_retries':0,'supervision_dispatches':2,'prior_prelaunch_worker_starts':0,'engineering_control_exception':binding['engineering_recovery'],'original_practical_gate':'NOT_ADJUDICATED','FR40_release':False,'wall':time.time(),'monotonic':time.monotonic(),'clock':binding['runtime']['clock']})
        write(AUD,'worker_done.json',{'status':status,'complete':status=='COMPLETE_FR30_FINITE_DESCRIPTIVE','wall':time.time()})
    return 0 if status=='COMPLETE_FR30_FINITE_DESCRIPTIVE' else 2
def watchdog(claim):
    me=identity(os.getpid());need(me==claim['identities']['watchdog'],'WATCHDOG_SELF_IDENTITY')
    try:
        first=True
        while True:
            if (AUD/'worker_done.json').exists():break
            need(live(claim['identities']['supervisor']),'SUPERVISOR_IDENTITY_LOST')
            need(live(claim['identities']['worker']),'WORKER_EXIT_BEFORE_RECEIPT')
            v=resource_sample(claim);limits(v,claim);write(AUD,'resource_log.jsonl',{'actor':'watchdog',**v},True)
            if first:write(AUD,'watchdog_ready.json',{'identity':me,'first_resource_check':v});first=False
            for _ in range(100):
                if (AUD/'worker_done.json').exists():break
                time.sleep(.1)
        write(AUD,'watchdog_done.json',{'status':'NORMAL_WORKER_RECEIPT_OBSERVED'});return 0
    except BaseException as exc:
        write(AUD,'safety_stop.json',{'reason':str(exc) if isinstance(exc,Stop) else type(exc).__name__,'wall':time.time(),'monotonic':time.monotonic(),'worker_identity':claim['identities']['worker']})
        if live(claim['identities']['worker']):os.kill(claim['identities']['worker']['pid'],signal.SIGTERM)
        write(AUD,'watchdog_done.json',{'status':'SAFETY_STOP'});return 2
def supervise(binding_path,expected):
    raw=binding_path.read_bytes();need(sha(raw)==expected,'BINDING_SHA');binding=json.loads(raw)
    need(binding['execution_authorized'] is True and binding['contract_id']==CID,'EXECUTION_AUTHORITY')
    own={str(binding_path):raw.decode()};bytes_=len(raw)
    for d in binding['own_descriptors']:
        p=Path(d['path']);b=p.read_bytes();need(len(b)==d['bytes'] and sha(b)==d['sha256'],'OWN_CONTROL_SHA')
        need(str(p) not in own,'OWN_CONTROL_DUPLICATE');own[str(p)]=b.decode();bytes_+=len(b)
    need(len(own)==binding['engineering_recovery']['current_bound_files_max'] and bytes_+binding['engineering_recovery']['old_own_bytes_read']<=binding['engineering_recovery']['cumulative_bound_control_bytes_max'],'OWN_CONTROL_BUDGET')
    need(len(own)+binding['engineering_recovery']['old_own_files_read']<=binding['engineering_recovery']['cumulative_control_read_events_max'],'CUMULATIVE_OWN_CONTROL_BUDGET')
    need(not AUD.exists() and not OUT.exists(),'EXECUTION_ALREADY_EXISTS')
    clock=binding['runtime']['clock'];need(time.time()<clock['wall_deadline'] and time.monotonic()<clock['monotonic_deadline'],'PURPOSE_DEADLINE_BEFORE_LAUNCH')
    AUD.mkdir(parents=True);OUT.mkdir(parents=True)
    lock=(AUD/'formal_execution.lock').open('x');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);lock.write(str(os.getpid()));lock.flush()
    me=identity(os.getpid());need(me['cwd']==str(ROOT) and me['UID']==os.getuid(),'SUPERVISOR_CONTEXT')
    need(str(Path(sys.executable).resolve())==binding['runtime']['python_binary']['path'],'RUNTIME_INTERPRETER_CHANGED')
    runtime_checked=[]
    for name in ('python_binary','numpy_binary','pyarrow_binary'):
        rec=binding['runtime'][name];p=Path(rec['path']);before=p.stat();bb=p.read_bytes();after=p.stat()
        need(fp(before)==fp(after)==rec['fingerprint'] and len(bb)==rec['bytes'] and sha(bb)==rec['sha256'],'RUNTIME_BINARY_IDENTITY')
        runtime_checked.append(rec)
    write(AUD,'runtime_identity_receipt.json',{'status':'EXACT_BINARIES_BOUND','binaries':runtime_checked,'runtime_loader_IO_not_research_source':True,'model_imports':0})
    claim={**clock,'identities':{'supervisor':me}}
    env=dict(os.environ);env.update(binding['environment']);py=binding['runtime']['interpreter_requested']
    logs=[(AUD/(name+'.log')).open('xb') for name in ('worker','watchdog')]
    proc=subprocess.Popen([py,'-I','-B',str(SCRIPT),'--worker'],stdin=subprocess.PIPE,stdout=logs[0],stderr=subprocess.STDOUT,cwd=ROOT,env=env,start_new_session=True)
    claim['identities']['worker']=identity(proc.pid)
    watch=subprocess.Popen([py,'-I','-B',str(SCRIPT),'--watchdog'],stdin=subprocess.PIPE,stdout=logs[1],stderr=subprocess.STDOUT,cwd=ROOT,env=env,start_new_session=True)
    claim['identities']['watchdog']=identity(watch.pid)
    need(all(v['cwd']==str(ROOT) and v['UID']==os.getuid() for v in claim['identities'].values()),'PROCESS_CONTEXT')
    write(AUD,'process_identities.json',claim['identities'])
    write(AUD,'execution_claim.json',{'contract_id':CID,'binding_path':str(binding_path),'binding_sha256':expected,'own_files':len(own),'own_bytes':bytes_,'cumulative_own_control_read_events':len(own)+binding['engineering_recovery']['old_own_files_read'],'cumulative_own_control_bytes':bytes_+binding['engineering_recovery']['old_own_bytes_read'],'engineering_control_exception':binding['engineering_recovery'],'clock':clock,'current_direct_authority':binding['authority'],'execution_count':1,'scientific_retries':0,'supervision_dispatches':2,'runtime':binding['runtime']})
    binding['binding_path']=str(binding_path);binding['binding_sha256']=expected
    watch.stdin.write(enc(claim));watch.stdin.close()
    proc.stdin.write(enc({'binding':binding,'claim':claim,'own':own}));proc.stdin.close()
    rc=proc.wait();wrc=watch.wait()
    for f in logs:f.close()
    write(OUT,'supervisor_exit_receipt.json',{'worker_actual_wait_exit':rc,'watchdog_actual_wait_exit':wrc,'supervisor_self_exit':'UNKNOWN_AT_SELF_SEAL','identities':claim['identities'],'retry':False})
    receipt=json.loads((OUT/'execution_receipt.json').read_bytes()) if (OUT/'execution_receipt.json').exists() else {'status':'WORKER_EXIT_WITHOUT_RECEIPT'}
    on_time=time.time()<clock['wall_deadline'] and time.monotonic()<clock['monotonic_deadline']
    terminal_status=receipt['status'] if on_time else 'STOPPED_PURPOSE_DEADLINE_AT_SEAL'
    need(len(outfiles())+1<=40 and sum(p.stat().st_size for p in outfiles())<134217728-65536,'FINAL_OUTPUT_RESERVATION')
    write(OUT,'run_seal.json',{'status':terminal_status,'worker_status':receipt['status'],'artifacts':[desc(p) for p in sorted(outfiles()) if p.name!='run_seal.json'],'self_excluded':True,'worker_actual_wait_exit':rc,'watchdog_actual_wait_exit':wrc,'contract_id':CID,'scientific_package_seal':binding['scientific_package_seal'],'original_practical_gate':'NOT_ADJUDICATED','FR40_release':False,'practical_scientific_PASS':False,'purpose_on_time_before_final_seal':on_time,'final_wall':time.time(),'final_monotonic':time.monotonic()})
    print(json.dumps({'status':terminal_status,'worker_exit':rc,'watchdog_exit':wrc,'seal':str(OUT/'run_seal.json'),'summary':receipt.get('summary')}),flush=True)
    lock.close()
    return 0 if rc==wrc==0 and terminal_status=='COMPLETE_FR30_FINITE_DESCRIPTIVE' else 2
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--worker',action='store_true');ap.add_argument('--watchdog',action='store_true');ap.add_argument('--binding');ap.add_argument('--binding-sha');a=ap.parse_args()
    if a.worker:return worker(json.load(sys.stdin))
    if a.watchdog:return watchdog(json.load(sys.stdin))
    need(a.binding and a.binding_sha,'BINDING_ARGUMENTS');return supervise(Path(a.binding),a.binding_sha)
if __name__=='__main__':sys.exit(main())
