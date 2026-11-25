"""Independent single-use S/A/B saved-output diagnostic. No numerical work at import."""
import argparse,collections,hashlib,io,json,math,os,re,shutil,signal,stat,struct,subprocess,sys,time,traceback,zipfile
from pathlib import Path
ROOT=Path('historical_sources/DTI_XAI_Bottleneck')
PKG=ROOT/'protocol/smiles_gine_bottleneck_fr40_diag_v0_2'
AUD=ROOT/'audit/smiles_gine_bottleneck_fr40_diag_v0_2/run_001'
OUT=ROOT/'results/smiles_gine_bottleneck_fr40_diag_v0_2/run_001'
SCRIPT=ROOT/'scripts/sgc_fr40_diag_v0_2.py'
IDS=('label_group_id','pair_group_id','endpoint')
SEEDS=(17,29,43,71,101);SL=tuple(map(str,SEEDS))+('MEAN5',)
EPS=('Kd','Ki');EL=EPS+('KD_KI_EQUAL',);OPS=('D','P','DP');OL=OPS+('ALL3_EQUAL',)
FUN=('q','h0','h_aff','h1');POL=('FIT_ROW_UNIFORM','FIT_PROTEIN_INPUT_UNIFORM')
N={'Kd':206,'Ki':2036};GIB=2**30
NUMBER=re.compile(rb'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
class Stop(Exception):pass
def need(v,c):
    if not v:raise Stop(c)
def enc(v):return (json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def sha(b):return hashlib.sha256(b).hexdigest()
def fp(s):return {k:str(getattr(s,k)) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_nlink')}
def key(r):return tuple(r[k] for k in IDS)
def canonical(r):return tuple(v.encode('utf8') for v in key(r))
def finite(np,x,c):need(bool(np.isfinite(x).all()),c)
def close(a,b,terms=None,absolute=None):
    need(math.isfinite(float(a)) and math.isfinite(float(b)),'NONFINITE_CHECK')
    allowed=absolute if absolute is not None else 1e-12+1e-12*math.fsum(abs(float(x)) for x in (terms if terms is not None else (a,b)))
    need(abs(float(a)-float(b))<=allowed,'NUMERIC_IDENTITY_MISMATCH')
    return float(a)-float(b)
def docread(p):return Path(p).read_bytes()
def allfiles():
    out=[]
    for d in (AUD,OUT,PKG):
        if d.exists():out.extend(p for p in d.iterdir() if p.is_file())
    if SCRIPT.exists():out.append(SCRIPT)
    return out
def output_limits(extra=0,new=False):
    f=allfiles();need(len(f)+int(new)<=32,'OUTPUT_FILES_LIMIT')
    need(sum(p.stat().st_size for p in f)+extra<=512*2**20,'OUTPUT_BYTES_LIMIT')
    need(sum(p.stat().st_size for p in f if p.suffix!='.npy')+extra<=128*2**20,'OUTPUT_OTHER_LIMIT')
def write(d,name,data,append=False):
    need('/' not in name and name not in ('.','..'),'BAD_OUTPUT_NAME')
    p=d/name;b=data if isinstance(data,bytes) else enc(data)
    output_limits(len(b),not p.exists())
    with p.open('ab' if append else 'xb') as f:
        need(f.write(b)==len(b),'OUTPUT_SHORT_WRITE');f.flush();os.fsync(f.fileno())
    return {'path':str(p),'bytes':len(b),'sha256':sha(b)} if not append else None
def ident(pid):
    s=subprocess.check_output(['/bin/ps','-ww','-p',str(pid),'-o','pid=,uid=,pgid=,lstart=,command='],text=True).strip().split(None,8)
    need(len(s)==9,'PROCESS_IDENTITY_LAYOUT')
    lines=subprocess.check_output(['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],text=True).splitlines()
    cwd=[v[1:] for v in lines if v.startswith('n')];need(len(cwd)==1,'PROCESS_CWD')
    return {'pid':int(s[0]),'UID':int(s[1]),'PGID':int(s[2]),'birth':' '.join(s[3:8]),'command':s[8],'cwd':cwd[0]}
def live(rec):
    try:return ident(rec['pid'])==rec
    except (OSError,subprocess.CalledProcessError,Stop):return False
def resource(state):
    text=subprocess.check_output(['/usr/bin/vm_stat'],text=True)
    page=int(re.search(r'page size of (\d+) bytes',text).group(1))
    pages=lambda name:int(re.search(r'^'+re.escape(name)+r':\s+(\d+)',text,re.M).group(1))
    rss=0
    for rec in state['identities'].values():
        if live(rec):
            try:rss+=int(subprocess.check_output(['/bin/ps','-p',str(rec['pid']),'-o','rss='],text=True).strip())*1024
            except (ValueError,subprocess.CalledProcessError):pass
    return {'wall':time.time(),'monotonic':time.monotonic(),'owned_RSS_bytes':rss,
        'available_bytes':(pages('Pages free')+pages('Pages inactive'))*page,'available_method':'Darwin free+inactive pages',
        'disk_free_bytes':shutil.disk_usage(ROOT).free,'MPS_initialized':False,'MPS_driver_bytes':0}
def physical(v,state):
    need(v['owned_RSS_bytes']<=36*GIB,'RSS_LIMIT')
    need(v['available_bytes']>=4*GIB,'AVAILABLE_MEMORY_LIMIT')
    need(v['disk_free_bytes']>=20*GIB,'DISK_LIMIT')
    need(v['wall']<state['wall_deadline'] and v['monotonic']<state['monotonic_deadline'],'DEADLINE')
    output_limits()
def bindcheck():
    p=json.loads((PKG/'package_seal.json').read_bytes())
    for r in p['artifacts']:
        raw=docread(r['path']);need(len(raw)==r['bytes'] and sha(raw)==r['sha256'],'PACKAGE_IDENTITY')
    m=json.loads((PKG/'contract.json').read_bytes())
    for r in m['documents'].values():
        raw=docread(r['path']);need(len(raw)==r['bytes'] and sha(raw)==r['sha256'],'DOCUMENT_IDENTITY')
    need(sys.executable==m['runtime']['executable'],'INTERPRETER_PATH')
    for r in m['runtime']['binaries']:
        s=os.lstat(r['path'])
        need(all(fp(s)[k]==v for k,v in r['fingerprint'].items()),'RUNTIME_FINGERPRINT')
    return m,p
def waitstate():
    end=time.monotonic()+30
    while not (AUD/'execution_state.json').exists():
        need(time.monotonic()<end,'STATE_HANDSHAKE_TIMEOUT');time.sleep(.05)
    return json.loads((AUD/'execution_state.json').read_bytes())
class Lex:
    """Lexical spans only; numbers outside approved projections are never converted."""
    def __init__(self,raw):
        self.t=raw.decode('utf8') if isinstance(raw,bytes) else raw;self.n=len(self.t);self.dec=json.JSONDecoder()
        need(self.ws(self.end(0))==self.n,'JSON_TRAILING')
    def ws(self,i):
        while i<self.n and self.t[i].isspace():i+=1
        return i
    def end(self,i):
        i=self.ws(i);need(i<self.n,'JSON_EOF');s=self.t
        if s[i]=='"':
            j=i+1
            while j<self.n:
                if s[j]=='"':return j+1
                j+=2 if s[j]=='\\' else 1
            raise Stop('JSON_STRING_EOF')
        if s[i] in '[{':
            end=']' if s[i]=='[' else '}';j=self.ws(i+1)
            while j<self.n and s[j]!=end:
                if s[j] in ',:':j=self.ws(j+1)
                else:j=self.ws(self.end(j))
            need(j<self.n,'JSON_CONTAINER_EOF');return j+1
        m=re.match(r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null',s[i:])
        need(m is not None,'JSON_TOKEN');return i+len(m[0])
    def fields(self,start=0):
        i=self.ws(start);need(self.t[i]=='{','JSON_OBJECT');out={};i=self.ws(i+1)
        while self.t[i]!='}':
            k,j=self.dec.raw_decode(self.t,i);need(isinstance(k,str) and k not in out,'JSON_KEY')
            j=self.ws(j);need(self.t[j]==':','JSON_COLON');a=self.ws(j+1);z=self.end(a);out[k]=(a,z);i=self.ws(z)
            if self.t[i]==',':i=self.ws(i+1)
            else:need(self.t[i]=='}','JSON_COMMA')
        return out
    def items(self,start):
        i=self.ws(start);need(self.t[i]=='[','JSON_ARRAY');i=self.ws(i+1);out=[]
        while self.t[i]!=']':
            z=self.end(i);out.append((i,z));i=self.ws(z)
            if self.t[i]==',':i=self.ws(i+1)
            else:need(self.t[i]==']','JSON_ARRAY_COMMA')
        return out
    def val(self,span):return json.loads(self.t[span[0]:span[1]])
    def project(self,names,start=0,optional=()):
        f=self.fields(start);need(all(k in f for k in names if k not in optional),'MISSING_PROJECTION_FIELD')
        return {k:self.val(f[k]) for k in names if k in f}
class Ctx:
    def __init__(self,m,state):
        self.m=m;self.state=state;self.stage='S';self.used={};self.counters=collections.Counter()
        self.spend=collections.Counter();self.outputs=[];self.max_residual=0.;self.last=0.;self.checks=0
        self.completed={'S':False,'A':False,'B':False};self.phase='START'
    def log(self,event,**v):write(AUD,'events.jsonl',{'event':event,'phase':self.phase,'wall':time.time(),**v},True)
    def guard(self):
        need(time.time()<self.state['wall_deadline'] and time.monotonic()<self.state['monotonic_deadline'],'DEADLINE')
        need(not (AUD/'safety_stop.json').exists(),'WATCHDOG_STOP')
        if time.monotonic()-self.last>20:
            need(live(self.state['identities']['supervisor']) and live(self.state['identities']['watchdog']),'SUPERVISION_IDENTITY_LOST')
            self.last=time.monotonic()
    def count(self,k,n):
        need(type(n) is int and n>=0 and k in self.m['budgets'],'COUNTER_SCOPE')
        self.counters[k]+=n;need(self.counters[k]<=self.m['budgets'][k],'COUNT_LIMIT_'+k)
    def read(self,rec):
        self.guard();rid=rec['id'];module=rec['module']
        need(module==self.stage and rid not in self.used,'SOURCE_STAGE_OR_REUSE')
        if module=='B':need(self.completed['A'] and (OUT/'module_A_commit.json').exists(),'B_BEFORE_A_COMMIT')
        self.used[rid]='UNKNOWN';self.spend[module]+=rec['bytes']
        caps={'S':self.m['budgets']['S_bytes'],'A':self.m['budgets']['A_bytes'],'B':self.m['budgets']['B_bytes']}
        need(self.spend[module]<=caps[module],'SOURCE_BYTE_BUDGET')
        self.log('SOURCE_RESERVED',id=rid,bytes=rec['bytes'],charged_bytes=rec['bytes'])
        p=Path(rec['path']);s=os.lstat(p);need(stat.S_ISREG(s.st_mode) and s.st_size==rec['bytes'],'SOURCE_STAT')
        expected=rec.get('historical_fingerprint',rec.get('fingerprint',{}))
        need(all(fp(s).get(k)==str(v) for k,v in expected.items()),'SOURCE_HISTORICAL_FINGERPRINT')
        fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        try:
            need(fp(os.fstat(fd))==fp(s),'SOURCE_OPEN_IDENTITY')
            pieces=[];h=hashlib.sha256();n=0
            while True:
                z=os.read(fd,4*2**20)
                if not z:break
                h.update(z);pieces.append(z);n+=len(z);need(n<=rec['bytes'],'SOURCE_LONG_READ');self.guard()
            need(n==rec['bytes'] and h.hexdigest()==rec['sha256'],'SOURCE_CONTENT_IDENTITY')
            need(fp(os.fstat(fd))==fp(s) and fp(os.lstat(p))==fp(s),'SOURCE_CHANGED')
        finally:os.close(fd)
        raw=b''.join(pieces);del pieces
        self.used[rid]={'bytes':n,'sha256':h.hexdigest(),'fingerprint':fp(s),'read_passes':1,'decode_purposes':1,'physical_IO':'UNKNOWN_LOADER_OS_NOT_FABRICATED'}
        self.log('SOURCE_VERIFIED',id=rid,bytes=n)
        return raw
    def emit(self,name,data,d=OUT):
        rec=write(d,name,data);self.outputs.append(rec);return rec
    def array(self,name,arr):
        need(arr.dtype.str=='<f8' and arr.flags.c_contiguous,'OUTPUT_NPY_LAYOUT')
        f=io.BytesIO();import numpy as np
        np.lib.format.write_array(f,arr,version=(1,0),allow_pickle=False);raw=f.getvalue()
        need(len(raw)-arr.nbytes<=4096,'OUTPUT_NPY_HEADER')
        return self.emit(name,raw)
    def commit(self,module):
        bound=list(self.outputs)
        self.emit('module_'+module+'_commit.json',{'schema':'sgc-fr40-diag-module-commit-v0.2','module':module,'status':'COMPLETED',
          'self_excluded':True,'config_sha':self.state['config_sha'],'numeric_code_id':self.state['numeric_code_id'],
          'ledger_schema':'diag-file-and-case-ledger-v0.2','segment_id':'run_001','artifacts':bound,
          'sources':{k:v for k,v in self.used.items() if k.startswith(module)},'charged_bytes':dict(self.spend),
          'arithmetic':dict(self.counters),'clock':{k:self.state[k] for k in ('wall_start','monotonic_start','wall_deadline','monotonic_deadline')},
          'FR50_release':False,'FR60_release':False,'A_commit_required_before_B':True})
        self.completed[module]=True;self.log('MODULE_COMMITTED',module=module,outputs=len(self.outputs))
    def numcheck(self,a,b,terms=None,absolute=None):
        r=close(a,b,terms,absolute);self.max_residual=max(self.max_residual,abs(r));self.checks+=1;return r
def npy(raw,shape,dtype,np):
    f=io.BytesIO(raw);need(np.lib.format.read_magic(f)==(1,0),'NPY_VERSION')
    sh,fort,dt=np.lib.format.read_array_header_1_0(f);off=f.tell()
    need(tuple(shape)==sh and not fort and dt==np.dtype(dtype),'NPY_SCHEMA')
    need(len(raw)==off+math.prod(shape)*dt.itemsize,'NPY_BYTES')
    return np.ndarray(sh,dtype=dt,buffer=raw,offset=off)
def jlines(rows):return b''.join(enc(r) for r in rows)
def mean(v):v=list(v);need(bool(v),'EMPTY_MEAN');return math.fsum(float(x) for x in v)/len(v)
def sign(x):return 1 if x>0 else -1 if x<0 else 0


S_FIELDS={
'S02':('status','cases','engineering_complete','successful_calibrators','scientific_PASS','MODEL_GATE','FR30_release','verification','FR10_final_seal_sha256'),
'S03':('status','self_excluded','scientific_PASS','FR30_release'),
'S05':('schema','contract_id','status','engineering_complete','finite_descriptive_scientific_outputs_complete','scientific_PASS','scientific_PASS_meaning','authoritative_execution','scientific_summary','accepted_scientific_conclusion','source_and_scope','remaining_FR20_research_work','FR30_status','FR30_release'),
'S06':('schema','status','contract_id','self_excluded','all10_cases_accepted','metadata_and_functional_research_complete','practical_margin','scientific_PASS','scientific_scope','FR30_status','FR30_release'),
'S07':('schema','contract_id','status','authoritative_execution_directory','run_seal_sha256','scope','validation','registered_finite_summary','registered_seed_endpoint_direction_disclosure','scientific_conclusion','original_practical_gate','practical_margin','FR40_release'),
'S08':('schema','contract_id','status','self_excluded','all10_cases_accepted','registered_source_and_call_budgets_accepted','original_practical_gate','practical_margin','practical_scientific_PASS','FR40_release','remaining_FR30_work','new_stage_automatic_release'),
'S10':('schema','contract_id','status','all10cases','evidence_tier','primary_G_q','comparisons_G','findings','MODEL_GATE','original_practical_gate','FR50_FR60_release','remaining_science_authority'),
'S11':('schema','contract_id','status','self_excluded','all10cases_complete','scientific_result','evidence_tier','original_practical_gate','numerical_precision_certification','independent_confirmation','FR50_FR60_release')}
def stage_S(ctx):
    ctx.phase='S_CONTROL_SUMMARY';data={};reports={}
    listed={x['path']:x for x in ctx.m['sources']['S']}
    for rec in ctx.m['sources']['S']:
        raw=ctx.read(rec);sid=rec['id']
        if rec['kind']=='report':reports[sid]=raw.decode('utf8');continue
        lex=Lex(raw);v=lex.project(S_FIELDS[sid])
        if sid in ('S03','S06','S08','S11'):
            fields=lex.fields();entries=[]
            if 'artifacts' in fields:
                a=fields['artifacts'][0]
                spans=lex.items(a) if lex.t[a]=='[' else list(lex.fields(a).values())
                for span in spans:
                    f=lex.fields(span[0]);p=lex.val(f['path']) if 'path' in f else None
                    if p in listed:
                        z={k:lex.val(f[k]) for k in ('path','bytes','sha256')}
                        need(all(z[k]==listed[p][k] for k in z),'S_SEAL_ENTRY_CONFLICT');entries.append(z)
            v['selected_control_artifact_identities']=entries
        data[sid]=v
    need(len(ctx.used)==11 and ctx.spend['S']==124231,'S_COVERAGE')
    need(data['S02']['engineering_complete'] is True and data['S05']['engineering_complete'] is True,'S_ENGINEERING_STATUS')
    need(data['S08']['all10_cases_accepted'] is True and data['S11']['all10cases_complete'] is True,'S_TEN_CASE_STATUS')
    specs=[
      ('FR10',['S01','S02','S03'],'S02','Full 5697 MODEL_GATE; h1 versus fixed FIT affine and piecewise calibrators','Saved float32 predictions promoted to float64; fixed EBMAE','FR20/FR40 NOT_EXECUTED and FR30 unreleased were historical exits'),
      ('FR20',['S04','S05','S06'],'S05','2242 O_SRC sources; two frozen reference policies; q and anchored qI','Saved observational scores and fixed component arithmetic','Historical FR30 unreleased; later FR30 controls record its separate descriptive use'),
      ('FR30',['S07','S08'],'S07','2242 O_SRC; original anchored deletion and five-function risk chain','Original float32 h1 promoted to float64 and saved FR20 qI','Historical FR40 unreleased; FR40 later completed in its own use'),
      ('FR40',['S09','S10','S11'],'S10','2242 O_SRC; D/P/DP; q/h0/h_aff/h1','Frozen float64 CPU head and source AD; float32 replay only for qualification','FR50/FR60 remain unreleased')]
    matrix=[]
    for phase,ids,terminal,estimand,numeric,history in specs:
        locators={'S01':['## 本轮结论'],'S02':['/status','/verification'],'S03':['/status','/artifacts'],'S04':['## 1. 本阶段能够得出的结论'],'S05':['/scientific_summary','/accepted_scientific_conclusion'],'S06':['/status','/scientific_scope'],'S07':['/scientific_conclusion','/registered_finite_summary'],'S08':['/status','/original_practical_gate'],'S09':['## 1. 科学结论'],'S10':['/primary_G_q','/comparisons_G','/findings'],'S11':['/status','/scientific_result']}
        matrix.append({'stage':phase,'source_control_ids':ids,'locator':[{'control':i,'selectors':locators[i]} for i in ids],
          'estimand':estimand,'panel_denominator':'5697 full GATE' if phase=='FR10' else '2242 sources; Kd206/Ki2036; dependent donor links',
          'function_reference_seed_endpoint':'Original fixed functions/references; five seeds and endpoint equal summaries; no new aggregation',
          'numeric_object':numeric,'engineering_terminal':data[terminal]['status'],'scientific_adjudication':data[terminal],
          'then_release_state':history,'subsequent_completion_state':data[terminal]['status'],
          'claim_limits':['EXPOSED_DEVELOPMENT_ONLY','no practical or population PASS','no unique historical cause','no automatic FR50/FR60'],
          'display_precision':'Copied existing report/JSON values without new arithmetic','new_scientific_calculation':False})
    ctx.emit('stage_S_evidence_matrix.json',{'schema':'sgc-diag-stage-S-v0.2','stages':matrix,'source_identity_only':True,'MODEL_GATE':'EXPOSED_DEVELOPMENT_ONLY','FR50_release':False})
    out=['# FR10—FR40阶段总结：已封印结论承接','','本表仅摘录既有报告与批准聚合控制，不重新计算科学量。工程完成、科学用途结论与实用门禁分开。MODEL_GATE已暴露；FR50/FR60未释放。','',
      '| 阶段 | 自身权威终态 | 数值对象／面板 |','|---|---|---|']
    for v in matrix:out.append('| '+v['stage']+' | '+str(v['engineering_terminal'])+' | '+v['estimand']+' |')
    for sid,start,stop in [('S01','## 本轮结论','## Material Passport'),('S04','## 1.','## 2.'),('S09','## 1.','## 2.')]:
        text=reports[sid];i=text.find(start);need(i>=0,'S_REPORT_LOCATOR');j=text.find(stop,i+len(start));need(j>i,'S_REPORT_END_LOCATOR')
        out.extend(['','### 已有报告摘录 '+sid,'',text[i:j].strip()])
    out.extend(['','### FR30已封印结论原文','',data['S07']['scientific_conclusion'],'',
      'FR30指定qI保留有益，删减后仍有部分超校准收益。该事实不等于所有交互均有益或全局加性机制已识别。','',
      '早期报告中的后续NOT_EXECUTED／未释放保留为当时状态；FR20、FR30、FR40各自后续终态按自身控制报告。原实用门禁仍NOT_ADJUDICATED，早期scientific_PASS不被改写。'])
    ctx.emit('stage_S_summary.md',('\n'.join(out)+'\n').encode());ctx.commit('S')
def load_mapping(ctx,np):
    ctx.stage='A';ctx.phase='A_IDENTITY';src=ctx.m['sources']['A'];get=lambda kind:next(r for r in src if r['kind']==kind)
    panel=[]
    names=('source_id',*IDS,'v3_3_role','n_reference_rows','n_donor_proteins','identical_weights','case_ids')
    for line in ctx.read(get('panel')).splitlines():
        if line.strip():panel.append(Lex(line).project(names))
    need(len(panel)==2242 and panel==sorted(panel,key=canonical),'PANEL_ORDER_COUNT')
    need(len({key(r) for r in panel})==2242 and len({r['source_id'] for r in panel})==2242,'PANEL_ID_UNIQUE')
    need(collections.Counter(r['endpoint'] for r in panel)==N,'PANEL_ENDPOINTS')
    cases=[v['case_id'] for v in ctx.m['cases']]
    for r in panel:
        need(type(r['source_id']) is int and 0<=r['source_id']<5697 and r['v3_3_role']=='MODEL_GATE','PANEL_SOURCE_ROLE')
        need(all(isinstance(x,str) and x for x in key(r)) and r['case_ids']==cases,'PANEL_KEYS_CASES')
    ix={r['source_id']:i for i,r in enumerate(panel)};raw=ctx.read(get('indices'));ops=npy(raw,(322659,14),'<i8',np)
    groups=[[] for r in panel];seen=set()
    for l,row in enumerate(ops):
        sid,donor,ep=int(row[0]),int(row[1]),int(row[2])
        need(sid in ix and 0<=donor<46140 and ep in (0,1),'OP_FOREIGN_KEY')
        need((sid,donor) not in seen,'OP_DUPLICATE');seen.add((sid,donor));i=ix[sid]
        need(EPS[ep]==panel[i]['endpoint'] and int(row[13])==1,'OP_ENDPOINT_SUPPORT')
        need(int(row[11])>0 and int(row[12])>0,'WEIGHT_DENOMINATOR');groups[i].append(l)
    need(collections.Counter(EPS[int(r[2])] for r in ops)=={'Kd':8150,'Ki':314509},'LINK_ENDPOINT_COUNTS')
    weights=[]
    for i,L in enumerate(groups):
        need(len(L)==panel[i]['n_reference_rows'] and L,'SOURCE_LINK_COUNT')
        donors=ops[L,1].tolist();need(donors==sorted(donors),'FIT_DONOR_ORDER')
        need(bool(np.all(ops[L,11]==len(L))),'MAIN_DENOMINATOR')
        w=[1/ops[L,j].astype(np.float64) for j in (11,12)]
        for z in w:ctx.numcheck(math.fsum(map(float,z)),1.)
        need(bool(np.array_equal(w[0],w[1]))==panel[i]['identical_weights'],'WEIGHT_FLAG')
        groups[i]=np.array(L,dtype=np.int64);weights.append(w)
    del ops,raw
    old={}
    fields=('arm','seed_layer','endpoint_layer','operation_layer','function_id','policy_id','E','B','Skill')
    for line in ctx.read(get('fr40_summary')).splitlines():
        if line.strip():
            r=Lex(line).project(fields);k=(r['arm'],str(r['seed_layer']),r['endpoint_layer'],r['operation_layer'],r['function_id'],r['policy_id'])
            need(k not in old and all(math.isfinite(r[f]) for f in ('E','B','Skill')),'FR40_SUMMARY_SCHEMA');old[k]=r
    need(len(old)==1152,'FR40_SUMMARY_COUNT')
    public=[{k:r[k] for k in ('source_id',*IDS)} for r in panel]
    ctx.emit('source_index.jsonl',jlines(public))
    ctx.emit('axes.json',{'cases':ctx.m['cases'],'source_order':'UTF8 label_group_id,pair_group_id,endpoint; source_id is original GATE index','functions':FUN,'operations':OPS,'references':POL,'classes':['C'+str(i) for i in range(8)],'q_class_last_axis':['M','P','H'],'q_totals_last_axis':['B','E_saved','E_diag','Skill_saved','Skill_diag','max_abs_eps_replay_difference'],'endpoint_code':{'0':'Kd','1':'Ki'}})
    return panel,groups,weights,old
class Acc:
    def __init__(self,n):self.parts=[[] for _ in range(n)];self.count=0;self.maximum=0.;self.counts=[0]*8
    def add(self,v,counts):
        self.count+=1;self.maximum=max(self.maximum,float(v[5]))
        for k,x in enumerate(v):
            if k==5:continue
            p=self.parts[k];x=float(x);i=0
            for y in p:
                if abs(x)<abs(y):x,y=y,x
                hi=x+y;lo=y-(hi-x)
                if lo:p[i]=lo;i+=1
                x=hi
            p[i:]=[x]
        self.counts=[a+int(b) for a,b in zip(self.counts,counts)]
    def value(self,np):
        need(self.count>0,'ACC_EMPTY')
        v=np.array([math.fsum(p)/self.count if k!=5 else self.maximum for k,p in enumerate(self.parts)],dtype=np.float64)
        return v
def slot_indices(ctx,arm,seed,ep,op):
    cs=[v['code'] for v in ctx.m['cases'] if v['arm']==arm and (seed=='MEAN5' or str(v['seed'])==str(seed))]
    ee=range(2) if ep=='KD_KI_EQUAL' else (EPS.index(ep),)
    oo=range(3) if op=='ALL3_EQUAL' else (OPS.index(op),)
    return [(ci,e,o) for ci in cs for e in ee for o in oo]
def average_vectors(vectors,np,max_index=None):
    vectors=list(vectors);need(vectors,'NO_RAW_SLOTS')
    out=np.array([mean(v[j] for v in vectors) for j in range(len(vectors[0]))],dtype=np.float64)
    if max_index is not None:out[max_index]=max(float(v[max_index]) for v in vectors)
    return out
def meta(arm,seed,ep,op,pi,fi=None):
    v={'arm':arm,'seed_layer':str(seed),'endpoint_layer':ep,'operation_layer':op,'reference_scheme':POL[pi]}
    if fi is not None:v['function_id']=FUN[fi]
    return v
def summary_record(v,np):
    classes=v[6:].reshape(8,3);P=math.fsum(classes[:,1]);H=math.fsum(classes[:,2])
    harms=[float(classes[c,2]) for c in (3,7,1)]
    return {'B':float(v[0]),'E_saved':float(v[1]),'E_diag':float(v[2]),'Skill_saved':float(v[3]),'Skill_diag':float(v[4]),
      'max_abs_eps_replay_difference':float(v[5]),'E_diag_minus_saved':float(v[2]-v[1]),'Skill_diag_minus_saved':float(v[4]-v[3]),
      'P_total':P,'H_total':H,'H_direction':harms[0],'H_overshoot':harms[1],'H_false_response':harms[2],
      'harm_proportions':[x/H for x in harms] if H>0 else None,'harm_status':'DEFINED' if H>0 else 'NO_OBSERVED_HARM'}
def stage_A(ctx,np):
    panel,groups,weights,old=load_mapping(ctx,np);bank={};countsbank={}
    qstats=np.empty((10,2242,3,2,8,3),dtype='<f8');qtot=np.empty((10,2242,3,2,6),dtype='<f8')
    for rec in [r for r in ctx.m['sources']['A'] if r['kind']=='operation_values']:
        ci=rec['case_code'];ctx.phase='A_'+ctx.m['cases'][ci]['case_id'];raw=ctx.read(rec)
        arr=npy(raw,(322659,4,12),'<f8',np);acc={(ep,oi,fi,pi):Acc(30) for ep in range(2) for oi in range(3) for fi in range(4) for pi in range(2)}
        for i,p in enumerate(panel):
            L=groups[i];v=arr[L,:,:9];finite(np,v,'A_REQUIRED_NONFINITE')
            a=v[:,:,:3];r=v[:,:,3:6];saved=v[:,:,6:9];e=r-a
            b=np.abs(r);absa=np.abs(a);d=np.abs(e);s=b-d
            finite(np,e,'A_SUBTRACTION_NONFINITE');finite(np,s,'A_SKILL_NONFINITE')
            diff=np.abs(e-saved);need(bool(np.all(diff<=1e-12+1e-12*(np.abs(e)+np.abs(saved)))),'EPSILON_REPLAY')
            cls=np.full(r.shape,255,dtype=np.uint8);rz=r==0;az=a==0
            cls[rz&az]=0;cls[rz&~az]=1;cls[~rz&az]=2
            nz=~rz&~az;same=nz&((r>0)==(a>0));cls[nz&~same]=3
            cls[same&(absa<=b)]=4;over=same&(absa>b)
            cls[over&(d<b)]=5;cls[over&(d==b)]=6;cls[over&(d>b)]=7
            need(bool(np.all(cls<8)),'CLASS_PARTITION')
            n=len(L);ctx.count('A_views',n*12);ctx.count('A_weighted_views',n*24)
            ei=EPS.index(p['endpoint'])
            for fi in range(4):
                for oi in range(3):
                    cc=cls[:,fi,oi];cn=np.bincount(cc,minlength=8)
                    need(int(cn.sum())==n,'CLASS_INTEGER_PARTITION')
                    pos=np.maximum(s[:,fi,oi],0.);neg=np.maximum(-s[:,fi,oi],0.)
                    for pi,w in enumerate(weights[i]):
                        def wm(x):return math.fsum(float(z)*float(y) for z,y in zip(w,x))
                        B=wm(b[:,fi,oi]);Ed=wm(d[:,fi,oi]);Es=wm(np.abs(saved[:,fi,oi]))
                        vv=np.empty(30,dtype=np.float64);vv[:6]=[B,Es,Ed,B-Es,B-Ed,float(diff[:,fi,oi].max())]
                        cl=vv[6:].reshape(8,3)
                        for k in range(8):
                            mask=cc==k;ww=w[mask]
                            cl[k]=[math.fsum(map(float,ww)),math.fsum(float(z)*float(y) for z,y in zip(ww,pos[mask])),math.fsum(float(z)*float(y) for z,y in zip(ww,neg[mask]))]
                        finite(np,vv,'A_SOURCE_STATS_NONFINITE');need(bool(np.all(cl>=0)),'A_NEGATIVE_MPH')
                        ctx.numcheck(math.fsum(cl[:,0]),1.)
                        P=math.fsum(cl[:,1]);H=math.fsum(cl[:,2])
                        ctx.numcheck(P-H,vv[4],[P,H,vv[4]]);ctx.numcheck(H,math.fsum(cl[[1,3,7],2]))
                        acc[(ei,oi,fi,pi)].add(vv,cn)
                        if fi==0:qstats[ci,i,oi,pi]=cl;qtot[ci,i,oi,pi]=vv[:6]
            ctx.count('all_source_slots',24)
            if i%128==0:ctx.guard()
        for (ei,oi,fi,pi),ac in acc.items():
            need(ac.count==N[EPS[ei]],'A_SOURCE_DENOMINATOR');bank[(ci,ei,oi,fi,pi)]=ac.value(np);countsbank[(ci,ei,oi,fi,pi)]=ac.counts
        del arr,raw,acc
        ctx.log('A_CASE_COMPLETE',case_id=ctx.m['cases'][ci]['case_id'],arithmetic=dict(ctx.counters))
        print('A_CASE_COMPLETE '+ctx.m['cases'][ci]['case_id'],flush=True)
    summaries=[];classrows=[];lookup={};paired=[]
    for arm in ('S','G'):
      for seed in SL:
       for ep in EL:
        for op in OL:
         slots=slot_indices(ctx,arm,seed,ep,op);agg=seed=='MEAN5' or ep=='KD_KI_EQUAL' or op=='ALL3_EQUAL'
         for fi in range(4):
          for pi in range(2):
            v=average_vectors((bank[ci,e,o,fi,pi] for ci,e,o in slots),np,5);row=summary_record(v,np)
            k=(arm,seed,ep,op,FUN[fi],POL[pi]);need(k in old,'OLD_SUMMARY_SLOT')
            for name,x in [('B',v[0]),('E',v[1]),('Skill',v[3])]:ctx.numcheck(x,old[k][name])
            ctx.numcheck(math.fsum(v[6:].reshape(8,3)[:,0]),1.)
            ctx.numcheck(row['P_total']-row['H_total'],row['Skill_diag'],[row['P_total'],row['H_total'],row['Skill_diag']])
            row.update(meta(arm,seed,ep,op,pi,fi));row.update(status='FINITE_COMPUTED_DIAGNOSTIC',source_denominator_map=N,independent_sample_count=None)
            summaries.append(row);lookup[k]=row
            cl=v[6:].reshape(8,3)
            for kclass,(M,P,H) in enumerate(cl):
                if M==0:need(P==0 and H==0,'EMPTY_CLASS_CONTRIBUTION')
                classrows.append({**meta(arm,seed,ep,op,pi,fi),'class_id':'C'+str(kclass),'M':float(M),'P':float(P),'H':float(H),'N':float(P-H),
                  'P_conditional':float(P/M) if M>0 else None,'H_conditional':float(H/M) if M>0 else None,'N_conditional':float((P-H)/M) if M>0 else None,
                  'conditional_status':'DEFINED' if M>0 else 'EMPTY_CLASS','link_class_count':None if agg else countsbank[(slots[0][0],slots[0][1],slots[0][2],fi,pi)][kclass]})
         for pi in range(2):
          h=lookup[(arm,seed,ep,op,'h1',POL[pi])]
          for comparator in ('h0','h_aff'):
            z=lookup[(arm,seed,ep,op,comparator,POL[pi])]
            names=('E_diag','E_saved','B','P_total','H_total','Skill_diag','H_direction','H_overshoot','H_false_response')
            dif={name:h[name]-z[name] for name in names};x=dif
            rs=ctx.numcheck(x['Skill_diag'],x['P_total']-x['H_total'],[x['Skill_diag'],x['P_total'],x['H_total']])
            re_=ctx.numcheck(x['E_diag'],x['B']-x['P_total']+x['H_total'],[x['E_diag'],x['B'],x['P_total'],x['H_total']])
            paired.append({**meta(arm,seed,ep,op,pi),'comparator_id':comparator,**{'delta_'+k:v for k,v in dif.items()},'residual_skill':rs,'residual_error':re_,'aggregate_source_count':None})
    need((len(summaries),len(classrows),len(paired))==(1152,9216,576),'A_OUTPUT_GRID')
    ctx.count('paired_rows',576);ctx.count('paired_difference_values',5184);ctx.count('paired_residual_values',1152)
    ctx.count('summary_rows',1152);ctx.count('class_rows',9216)
    ctx.array('q_source_class_stats.npy',qstats);ctx.array('q_source_totals.npy',qtot)
    ctx.emit('diagnostic_summary.jsonl',jlines(summaries));ctx.emit('class_summary.jsonl',jlines(classrows))
    ctx.emit('paired_error_decomposition.jsonl',jlines(paired))
    ctx.commit('A');return panel,qstats,qtot,summaries,paired,bank


def parse_fr30_aggregates(ctx):
    by={r['kind']:r for r in ctx.m['sources']['B'] if r['kind']!='prediction'}
    raw=ctx.read(by['fr30_differences']);lex=Lex(raw);fields=lex.fields();diff={};comp={}
    names=('arm','seed_layer','endpoint_layer','reference_scheme')
    for a,z in lex.items(fields['rows'][0]):
        h=lex.project(names+('contrast_id',),a)
        if h['contrast_id']=='C_I':
            f=lex.fields(a);v=lex.val(f['difference']);k=(h['arm'],str(h['seed_layer']),h['endpoint_layer'],h['reference_scheme'])
            need(k not in diff and math.isfinite(v),'FR30_CI_SCHEMA');diff[k]=v
    for a,z in lex.items(fields['secondary_composition'][0]):
        h=lex.project(names+('A_plus','A_minus','p_plus','p_minus','p_zero','n_plus','n_minus','n_zero','N_e'),a)
        k=(h['arm'],str(h['seed_layer']),h['endpoint_layer'],h['reference_scheme']);need(k not in comp,'FR30_COMPOSITION_DUPLICATE');comp[k]=h
    raw=ctx.read(by['fr30_reference']);lex=Lex(raw);f=lex.fields();M={};D={}
    for name,bank,haspol in [('M_I',M,True),('D_ref',D,False)]:
        for a,z in lex.items(f[name][0]):
            h=lex.project(('arm','seed_layer','endpoint_layer','value')+(('reference_scheme',) if haspol else ()),a)
            k=(h['arm'],str(h['seed_layer']),h['endpoint_layer'])+((h['reference_scheme'],) if haspol else ())
            need(k not in bank and math.isfinite(h['value']),'FR30_REF_SCHEMA');bank[k]=h['value']
    need(len(diff)==len(comp)==len(M)==72 and len(D)==36,'FR30_AGGREGATE_COUNTS')
    return diff,comp,M,D,by
def load_components(ctx,rec,panel,np):
    raw=ctx.read(rec);I=np.empty((10,2242,2),dtype=np.float64);Q=np.empty_like(I)
    index={r['source_id']:i for i,r in enumerate(panel)};seen=set();n=primary=0
    for body in raw.splitlines():
        if not body.strip():continue
        line=body.strip();need(line[:1]==b'[' and line[-1:]==b']','COMPONENT_ARRAY')
        tokens=[v.strip() for v in line[1:-1].split(b',')]
        need(len(tokens)==17 and all(NUMBER.fullmatch(v) for v in tokens),'COMPONENT_LEXICAL_LAYOUT')
        need(all(re.fullmatch(rb'0|[1-9][0-9]*',v) for v in tokens[:4]),'COMPONENT_CODE_INTEGER')
        ci,sid,target,pi=(int(v) for v in tokens[:4])
        need(0<=ci<10 and sid in index and target in (0,1,2) and pi in (0,1),'COMPONENT_FOREIGN_KEY')
        k=(ci,sid,target,pi);need(k not in seen,'COMPONENT_DUPLICATE');seen.add(k);n+=1
        if target==0:
            q,i=float(tokens[4]),float(tokens[8]);need(math.isfinite(q) and math.isfinite(i),'COMPONENT_SELECTED_FINITE')
            Q[ci,index[sid],pi]=q;I[ci,index[sid],pi]=i;primary+=1
        if n%4096==0:ctx.guard()
    need(n==134520 and primary==44840,'COMPONENT_ROWS')
    ctx.log('COMPONENT_PROJECTION',rows=n,identity_integer_cells=n*4,numeric_cells=primary*2,other_numeric_tokens_interpreted=0)
    return Q,I
def load_affine(ctx,rec):
    lex=Lex(ctx.read(rec));root=lex.fields();slots={};allids=set()
    for a,z in lex.items(root['slots'][0]):
        f=lex.fields(a);h={k:lex.val(f[k]) for k in ('case_id','endpoint','family','mode','FIT_rows','slot_sha256')}
        k=(h['case_id'],h['endpoint'],h['family']);need(k not in allids,'CALIBRATOR_DUPLICATE');allids.add(k)
        need(h['family'] in ('affine','piecewise') and h['endpoint'] in EPS,'CALIBRATOR_FAMILY_ENDPOINT')
        need(re.fullmatch('[0-9a-f]{64}',h['slot_sha256']) is not None,'CALIBRATOR_SLOT_SHA')
        if h['family']=='affine':
            h['coefficients']=lex.val(f['coefficients']);need(lex.val(f['knots'])==[],'AFFINE_KNOTS')
            need(len(h['coefficients'])==2 and all(math.isfinite(v) for v in h['coefficients']),'AFFINE_COEFFICIENTS')
            need(h['coefficients'][0]>0,'POSITIVE_AFFINE_SLOPE');slots[(h['case_id'],h['endpoint'])]=h
    need(len(allids)==40 and len(slots)==20,'CALIBRATOR_COUNTS');return slots
class ParquetProjection:
    def __init__(self,raw):
        import pyarrow.parquet as pq
        need(raw[:4]==raw[-4:]==b'PAR1','PARQUET_MAGIC')
        start=len(raw)-8-struct.unpack('<I',raw[-8:-4])[0];need(4<=start<len(raw)-8,'PARQUET_FOOTER')
        meta=pq.read_metadata(io.BytesIO(b'PAR1'+raw[start:]))
        need(meta.num_rows==5697 and set((*IDS,'y_true'))<=set(meta.schema.names),'PARQUET_SCHEMA')
        chunks=[]
        for gi in range(meta.num_row_groups):
            group=meta.row_group(gi)
            for ci in range(group.num_columns):
                col=group.column(ci);offsets=[v for v in (col.dictionary_page_offset,col.data_page_offset) if v is not None and v>0]
                need(offsets,'PARQUET_OFFSET');lo=min(offsets);hi=lo+col.total_compressed_size
                need(4<=lo<hi<=start,'PARQUET_CHUNK_BOUNDS');chunks.append((col.path_in_schema,lo,hi))
        for name,lo,hi in chunks:
            if name in (*IDS,'y_true'):
                need(all(other==name or max(lo,a)>=min(hi,b) for other,a,b in chunks),'PARQUET_CHUNK_OVERLAP')
        self.mask=bytearray(len(raw));self.mask[:4]=b'PAR1';self.mask[start:]=raw[start:];self.raw=raw;self.chunks=chunks;self.done=set()
    def read(self,columns):
        import pyarrow.parquet as pq
        need(set(columns)<=set((*IDS,'y_true')) and not self.done.intersection(columns),'PARQUET_COLUMN_REUSE')
        for name,lo,hi in self.chunks:
            if name in columns:self.mask[lo:hi]=self.raw[lo:hi]
        table=pq.ParquetFile(io.BytesIO(self.mask)).read(columns=columns,use_threads=False)
        need(table.num_rows==5697,'PARQUET_ROWS');self.done.update(columns)
        return {k:table[k].to_pylist() for k in columns}
def bscope(ctx,bank,arm,seed,ep,pi=None):
    cs=[c['code'] for c in ctx.m['cases'] if c['arm']==arm and (seed=='MEAN5' or str(c['seed'])==str(seed))]
    ee=range(2) if ep=='KD_KI_EQUAL' else (EPS.index(ep),)
    return mean(bank[(ci,e,pi)] if pi is not None else bank[(ci,e)] for ci in cs for e in ee)
def stage_B(ctx,np,panel,qstats,qtot):
    need(ctx.completed['A'],'B_NOT_RELEASED');ctx.stage='B';ctx.phase='B_PRETRUTH'
    oldci,oldcomp,oldM,oldD,by=parse_fr30_aggregates(ctx)
    Q,I=load_components(ctx,by['components'],panel,np);affine=load_affine(ctx,by['calibrators'])
    selected_indices=np.array([r['source_id'] for r in panel],dtype=np.int64)
    epm=[np.array([r['endpoint']==ep for r in panel]) for ep in EPS]
    pred={};maxq=maxpolicy=0.
    recs={(r['case_id'],r['function']):r for r in ctx.m['sources']['B'] if r['kind']=='prediction'}
    M={};D={}
    for case in ctx.m['cases']:
        ci=case['code'];cid=case['case_id'];scores={}
        for fn in ('h0','h1'):
            rec=recs[(cid,fn)];raw=ctx.read(rec);member=next(iter(rec['decode_members']))
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                need(z.namelist().count(member)==1,'PREDICTION_MEMBER')
                zraw=z.read(member);arr=npy(zraw,(5697,),'<f4',np)
                scores[fn]=arr[selected_indices].astype(np.float64);del arr,zraw
            finite(np,scores[fn],'PANEL_SCORE_NONFINITE');ctx.log('PREDICTION_PROJECTION',source_id=rec['id'],decoded=5697,used=2242,discarded_before_use=3455)
        h0,h1=scores['h0'],scores['h1'];aff=np.empty(2242,dtype=np.float64)
        for e,mask in enumerate(epm):
            slot=affine[(cid,EPS[e])];a,b=slot['coefficients'];aff[mask]=a*h0[mask]+b
        ctx.count('affine_uses',2242)
        eq=float(np.max(np.abs((h1-aff)-Q[ci,:,0])));epair=float(np.max(np.abs(Q[ci,:,0]-Q[ci,:,1])))
        need(eq<=1e-8 and epair<=1e-8,'FR30_Q_RECONSTRUCTION');maxq=max(maxq,eq);maxpolicy=max(maxpolicy,epair)
        pred[ci]=h1
        for e,mask in enumerate(epm):
            for pi in range(2):M[(ci,e,pi)]=mean(np.abs(I[ci,mask,pi]))
            D[(ci,e)]=mean(np.abs(I[ci,mask,0]-I[ci,mask,1]))
        ctx.guard()
    mviews=[];dviews=[]
    for arm in ('G','S'):
      for seed in SL:
       for ep in EL:
        for pi,pol in enumerate(POL):
            value=bscope(ctx,M,arm,seed,ep,pi);k=(arm,seed,ep,pol);ctx.numcheck(value,oldM[k],absolute=1e-8)
            mviews.append({'arm':arm,'seed_layer':seed,'endpoint_layer':ep,'reference_scheme':pol,'value':value,'saved':oldM[k]})
        value=bscope(ctx,D,arm,seed,ep);k=(arm,seed,ep);ctx.numcheck(value,oldD[k],absolute=1e-8)
        dviews.append({'arm':arm,'seed_layer':seed,'endpoint_layer':ep,'value':value,'saved':oldD[k]})
    ctx.emit('B_pretruth_binding.json',{'status':'ALL10_FIXED_RECIPE_BOUND_BEFORE_TRUTH','A_commit_required':True,'config_sha':ctx.state['config_sha'],
      'numeric_code_id':ctx.state['numeric_code_id'],'cases':10,'sources':2242,'q_source_max_abs_residual':maxq,'q_policy_max_abs_residual':maxpolicy,
      'M_I':mviews,'D_ref':dviews,'formula':'g=abs(y-(float64(h1_saved)-original_bar_qI))-abs(y-float64(h1_saved))','MODEL_GATE':'EXPOSED_DEVELOPMENT_ONLY',
      'truth_source_descriptor':{k:by['truth'][k] for k in ('path','bytes','sha256')},'prediction_or_truth_values_saved':False})
    ctx.phase='B_TRUTH_ONCE';projection=ParquetProjection(ctx.read(by['truth']))
    ids=projection.read(list(IDS));ks=list(zip(*(ids[k] for k in IDS)));del ids
    need(len(set(ks))==5697 and collections.Counter(k[2] for k in ks)=={'Kd':798,'Ki':4899},'TRUTH_IDENTITIES')
    pos={k:i for i,k in enumerate(ks)};need(all(key(r) in pos for r in panel),'TRUTH_EXACT_TRIPLE_JOIN')
    vals=projection.read(['y_true'])['y_true'];chosen=[vals[pos[key(r)]] for r in panel];del vals,projection,ks,pos
    y=np.asarray(chosen,dtype=np.float64);del chosen;finite(np,y,'USED_TRUTH_NONFINITE')
    ctx.log('TRUTH_PROJECTION',decoded=5697,used=2242,discarded_before_numeric_use=3455,columns=list(IDS)+['y_true'],identity_cells=17091)
    gains=np.empty((10,2242,2),dtype='<f8');craw={};compraw={}
    for case in ctx.m['cases']:
        ci=case['code'];ctx.phase='B_RECONSTRUCT_'+case['case_id'];p=pred[ci];loss1=np.abs(y-p);finite(np,loss1,'LOSS1_NONFINITE');ctx.count('absolute_losses',2242)
        for pi in range(2):
            minus=p-I[ci,:,pi];lm=np.abs(y-minus);g=lm-loss1;finite(np,g,'GAIN_NONFINITE')
            gains[ci,:,pi]=g;ctx.count('absolute_losses',2242);ctx.count('g_constructs',2242)
            for e,mask in enumerate(epm):
                gg=g[mask];n=N[EPS[e]];counts=[int(np.count_nonzero(gg>0)),int(np.count_nonzero(gg<0)),int(np.count_nonzero(gg==0))]
                need(sum(counts)==n,'GAIN_SIGN_PARTITION')
                c={'A_plus':mean(np.maximum(gg,0.)),'A_minus':mean(np.maximum(-gg,0.)),
                    'p_plus':counts[0]/n,'p_minus':counts[1]/n,'p_zero':counts[2]/n,
                    'n_plus':counts[0],'n_minus':counts[1],'n_zero':counts[2],'N_e':n}
                compraw[(ci,e,pi)]=c;craw[(ci,e,pi)]=mean(gg)
    reconstruction=[]
    for arm in ('G','S'):
      for seed in SL:
       for ep in EL:
        for pi,pol in enumerate(POL):
            k=(arm,seed,ep,pol);ci_val=bscope(ctx,craw,arm,seed,ep,pi);ctx.numcheck(ci_val,oldci[k],absolute=1e-8)
            row={'arm':arm,'seed_layer':seed,'endpoint_layer':ep,'reference_scheme':pol,'C_I_rebuilt':ci_val,'C_I_saved':oldci[k]}
            for field in ('A_plus','A_minus','p_plus','p_minus','p_zero'):
                bk={kk:vv[field] for kk,vv in compraw.items()};value=bscope(ctx,bk,arm,seed,ep,pi)
                ctx.numcheck(value,oldcomp[k][field],absolute=1e-8);row[field]=value
            rawslot=seed!='MEAN5' and ep!='KD_KI_EQUAL'
            for field in ('n_plus','n_minus','n_zero','N_e'):
                value=compraw[(next(c['code'] for c in ctx.m['cases'] if c['arm']==arm and str(c['seed'])==seed),EPS.index(ep),pi)][field] if rawslot else None
                need(value==oldcomp[k][field],'FR30_EXACT_SIGN_COUNTS');row[field]=value
            reconstruction.append(row)
    ctx.emit('FR30_reconstruction_receipt.json',{'status':'ALL40_RAW_72_AGGREGATE_RECONSTRUCTED','rows':reconstruction,'M_I':mviews,'D_ref':dviews,
      'truth_decoded':5697,'truth_used':2242,'prediction_decoded':113940,'prediction_used':44840,'absolute_losses':67260,'g_constructs':44840,
      'q_source_max_abs_residual':maxq,'FR30_absolute_tolerance':1e-8,'signed_g_threshold':0,'model_calls':0,'statistics_inference':0})
    ctx.phase='B_JOINT';joint={};nraw={}
    signs=(1,-1,0)
    for case in ctx.m['cases']:
      ci=case['code']
      for e,emask in enumerate(epm):
       n=N[EPS[e]]
       for oi in range(3):
        for pi in range(2):
            g=gains[ci,:,pi];skill=qtot[ci,:,oi,pi,4];v=np.zeros((9,27),dtype=np.float64);cn=[]
            for a,sg in enumerate(signs):
             gm=g>0 if sg==1 else g<0 if sg==-1 else g==0
             for b,ss in enumerate(signs):
                sm=skill>0 if ss==1 else skill<0 if ss==-1 else skill==0;mask=emask&gm&sm;k=a*3+b;count=int(mask.sum());cn.append(count)
                v[k,:3]=[count/n,math.fsum(map(float,skill[mask]))/n,math.fsum(map(float,g[mask]))/n]
                block=qstats[ci,mask,oi,pi].reshape(count,24)
                v[k,3:]=[math.fsum(map(float,block[:,j]))/n for j in range(24)]
                ctx.numcheck(math.fsum(v[k,3:].reshape(8,3)[:,0]),v[k,0])
            need(sum(cn)==n,'JOINT_SIGN_PARTITION')
            ctx.numcheck(math.fsum(v[:,0]),1.)
            ctx.numcheck(math.fsum(v[:,1]),mean(skill[emask]))
            ctx.numcheck(math.fsum(v[:,2]),craw[(ci,e,pi)])
            joint[(ci,e,oi,pi)]=v;nraw[(ci,e,oi,pi)]=cn
            ctx.count('q_source_slots',n)
      ctx.guard()
    rows=[];groups=[]
    for arm in ('S','G'):
      for seed in SL:
       for ep in EL:
        for op in OL:
         slots=slot_indices(ctx,arm,seed,ep,op);agg=seed=='MEAN5' or ep=='KD_KI_EQUAL' or op=='ALL3_EQUAL'
         for pi in range(2):
            j=np.array([[mean(joint[ci,e,o,pi][cell,k] for ci,e,o in slots) for k in range(27)] for cell in range(9)])
            ctx.numcheck(math.fsum(j[:,0]),1.)
            target=bscope(ctx,craw,arm,seed,ep,pi);ctx.numcheck(math.fsum(j[:,2]),target)
            for cell in range(9):
                v=j[cell];mass=float(v[0]);classes=v[3:].reshape(8,3);a,b=divmod(cell,3)
                ctx.numcheck(math.fsum(classes[:,0]),mass)
                P=math.fsum(classes[:,1]);H=math.fsum(classes[:,2]);ctx.numcheck(P-H,v[1],[P,H,v[1]])
                if mass==0:need(bool(np.all(v==0)),'EMPTY_JOINT_NONZERO')
                classes_out=[{'class_id':'C'+str(k),'M':float(M),'P':float(P0),'H':float(H0),'N':float(P0-H0),
                    'M_conditional':float(M/mass) if mass>0 else None,'P_conditional':float(P0/mass) if mass>0 else None,'H_conditional':float(H0/mass) if mass>0 else None} for k,(M,P0,H0) in enumerate(classes)]
                rows.append({**meta(arm,seed,ep,op,pi),'gain_sign':signs[a],'Skill_diag_sign':signs[b],'source_mass':mass,'Skill_contribution':float(v[1]),
                    'gain_contribution':float(v[2]),'Skill_conditional':float(v[1]/mass) if mass>0 else None,'gain_conditional':float(v[2]/mass) if mass>0 else None,
                    'P_total':P,'H_total':H,'H_direction':float(classes[3,2]),'H_overshoot':float(classes[7,2]),'H_false_response':float(classes[1,2]),
                    'classes':classes_out,'conditional_status':'DEFINED' if mass>0 else 'EMPTY_CELL',
                    'source_count':None if agg else nraw[(*slots[0],pi)][cell],'denominator_map':N})
            for a,sg in enumerate(signs):
                v=np.array([math.fsum(j[a*3:a*3+3,k]) for k in range(27)]);mass=float(v[0]);classes=v[3:].reshape(8,3)
                groups.append({**meta(arm,seed,ep,op,pi),'gain_sign':sg,'source_mass':mass,'Skill_contribution':float(v[1]),'gain_contribution':float(v[2]),
                  'Skill_conditional':float(v[1]/mass) if mass>0 else None,'gain_conditional':float(v[2]/mass) if mass>0 else None,
                  'class_MPH':classes.tolist(),'conditional_status':'DEFINED' if mass>0 else 'EMPTY_GROUP','source_count':None if agg else sum(nraw[(*slots[0],pi)][a*3:a*3+3])})
    need((len(rows),len(groups))==(2592,864),'JOINT_OUTPUT_GRIDS');ctx.count('joint_rows',2592)
    ctx.array('fr30_source_gain.npy',gains);ctx.emit('source_joint_summary.jsonl',jlines(rows));ctx.emit('gain_group_summary.jsonl',jlines(groups))
    ctx.commit('B');return rows,groups,reconstruction


def report(ctx,summaries,paired,joint,groups,recon):
    out=['# FR40保存结果阶段总结与失效诊断 v0.2','','## Material Passport','',
      '- origin_skill: academic-research-suite / experiment-agent。',
      '- S/A/B为新后续用途的一次有限开发性描述；MODEL_GATE已暴露。',
      '- 原FR40/FR30对象与裁决不改，原实用门禁NOT_ADJUDICATED；FR50/FR60未释放。',
      '- 无新模型、梯度、训练、拟合、统计推断或科学重试。','',
      '## A：主G／q／MEAN5／Kd-Ki等权','','| 参考 | 操作 | E_diag | B | Skill_diag | P_total | H_total | 异号损害 | 同向有害超幅 | 零响应假响应 |',
      '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    primary=[]
    for row in summaries:
        if row['arm']=='G' and row['function_id']=='q' and row['seed_layer']=='MEAN5' and row['endpoint_layer']=='KD_KI_EQUAL' and row['operation_layer'] in OPS:
            primary.append(row);keys=('E_diag','B','Skill_diag','P_total','H_total','H_direction','H_overshoot','H_false_response')
            out.append('| '+row['reference_scheme']+' | '+row['operation_layer']+' | '+' | '.join(format(row[k],'.9g') for k in keys)+' |')
    out.extend(['','分类为计算float64精确零／符号及误差边界；损害构成和类内均值均保留原完整分母。损害占比不是病因百分比，未从频率推断有效性。','',
      '## h1配对误差变化分解','','| 参考 | 操作 | 对照 | ΔE_diag | ΔB | ΔP | ΔH | ΔSkill |','|---|---|---|---:|---:|---:|---:|---:|'])
    for r in paired:
        if r['arm']=='G' and r['seed_layer']=='MEAN5' and r['endpoint_layer']=='KD_KI_EQUAL' and r['operation_layer'] in OPS:
            out.append('| '+r['reference_scheme']+' | '+r['operation_layer']+' | '+r['comparator_id']+' | '+' | '.join(format(r[k],'.9g') for k in ('delta_E_diag','delta_B','delta_P_total','delta_H_total','delta_Skill_diag'))+' |')
    out.extend(['','ΔE≈ΔB−ΔP+ΔH为同域描述性分解，不识别训练历史／具体层。S及其他函数、全部端点／seed和两参考保存在完整表中，不只交付主聚合。','',
      '## B：FR30损益与q解释净收益同源共现','','| 参考 | 操作 | FR30 g符号 | 原源质量 | Skill完整分母贡献 | Skill组内均值 |',
      '|---|---|---:|---:|---:|---:|'])
    for r in groups:
        if r['arm']=='G' and r['seed_layer']=='MEAN5' and r['endpoint_layer']=='KD_KI_EQUAL' and r['operation_layer'] in OPS:
            v='null' if r['Skill_conditional'] is None else format(r['Skill_conditional'],'.9g')
            out.append('| '+r['reference_scheme']+' | '+r['operation_layer']+' | '+str(r['gain_sign'])+' | '+format(r['source_mass'],'.9g')+' | '+format(r['Skill_contribution'],'.9g')+' | '+v+' |')
    out.extend(['','g仅表示保留指定锚定qI相对删减的观察损益；整个q的近似不是qI归因。两阶段按精确源身份连接，保持各自数值对象，不要求逐值相同。九格和边际含全部正／负／零，三操作不产生三份独立g。','',
      '## 识别边界与退出','',
      '本诊断分解异号、同向有害超幅及计算零响应假响应，不识别沿途折点、具体非线性层、输入信息缺失或学习率唯一因果作用。没有拟合收缩、步长扫描或新增解释器。',
      '有益qI与局部近似失效可以在同源案例中共存；共现不等于机制中介，也不证明XAI相对直接计算的决策增量。有效零结果、混合构成及参考依赖均保留，不追加实验追阳性。',
      '原H1/H2/H3、路线1、NP-01及FR10—FR40旧封印保持各自范围，未被重新裁决。',
      '',
      '## 执行摘要','',json.dumps({'modules':ctx.completed,'source_bytes':dict(ctx.spend),'arithmetic':dict(ctx.counters),'algebra_checks':ctx.checks,'max_checked_absolute_residual':ctx.max_residual},ensure_ascii=False)])
    ctx.emit('stage_report.md',('\n'.join(out)+'\n').encode(),AUD)
    return primary
def worker():
    state=waitstate();m,p=bindcheck();ctx=Ctx(m,state);code=1
    try:
        import numpy as np,pyarrow
        need(sys.version.split()[0]=='3.12.7' and np.__version__=='1.26.4' and pyarrow.__version__=='16.1.0','RUNTIME_VERSION')
        need(not any(k in sys.modules for k in ('torch','scipy','tensorflow')),'FORBIDDEN_IMPORT')
        stage_S(ctx)
        panel,qstats,qtot,summaries,paired,bank=stage_A(ctx,np)
        joint,groups,recon=stage_B(ctx,np,panel,qstats,qtot)
        primary=report(ctx,summaries,paired,joint,groups,recon)
        for k in ('A_views','A_weighted_views','all_source_slots','q_source_slots','affine_uses','absolute_losses','g_constructs','summary_rows','class_rows','paired_rows','paired_difference_values','paired_residual_values','joint_rows'):
            need(ctx.counters[k]==m['budgets'][k],'FINAL_COUNTER_'+k)
        need(len(ctx.used)==49 and all(isinstance(v,dict) for v in ctx.used.values()),'FINAL_SOURCE_COVERAGE')
        need(ctx.spend==collections.Counter({'S':124231,'A':1278532497,'B':44479458}),'FINAL_SOURCE_BYTES')
        ctx.guard()
        receipt={'status':'COMPLETE_S_A_B_FINITE_DIAGNOSTIC','modules':ctx.completed,'sources':ctx.used,'source_bytes':dict(ctx.spend),
          'science_source_files':38,'S_control_files':11,'one_hash_and_decode_shared_buffer':True,'science_retries':0,
          'model_calls':0,'AD':0,'training':0,'fit':0,'statistical_inference':0,'arithmetic':dict(ctx.counters),
          'numerical_checks':ctx.checks,'max_checked_absolute_residual':ctx.max_residual,
          'outputs':ctx.outputs,'primary_G_q':primary,'original_practical_gate':'NOT_ADJUDICATED','FR50_release':False,'FR60_release':False,
          'same_original_clock':True,'worker_actual_exit':'AWAIT_SUPERVISOR_WAIT','source_and_output_physical_IO':'UNKNOWN_NOT_FILLED_ZERO'}
        ctx.phase='COMPLETE';ctx.log('COMPLETE',arithmetic=dict(ctx.counters));ctx.emit('execution_receipt.json',receipt,AUD);code=0
    except BaseException as e:
        why=type(e).__name__+': '+str(e)[:500]
        try:
            ctx.log('STOPPED',reason=why,modules=ctx.completed)
            ctx.emit('execution_receipt.json',{'status':'STOPPED_NO_RETRY','reason':why,'modules':ctx.completed,'sources':ctx.used,'source_bytes':dict(ctx.spend),
              'arithmetic':dict(ctx.counters),'outputs':ctx.outputs,'science_retries':0,'model_calls':0,'AD':0,'training':0,'FR50_release':False,'FR60_release':False},AUD)
        except BaseException:pass
        print('WORKER_STOPPED '+why,flush=True)
    return code
def watchdog():
    state=waitstate()
    try:
        while True:
            v=resource(state);physical(v,state);write(AUD,'resources.jsonl',v,True)
            if (AUD/'execution_receipt.json').exists() or not live(state['identities']['worker']):return 0
            need(live(state['identities']['supervisor']),'WATCHDOG_OWNER_LOST')
            time.sleep(5)
    except BaseException as e:
        try:write(AUD,'safety_stop.json',{'reason':type(e).__name__+': '+str(e)[:300],'wall':time.time()})
        except BaseException:pass
        if live(state['identities']['worker']):
            try:os.killpg(state['identities']['worker']['PGID'],signal.SIGTERM)
            except ProcessLookupError:pass
        return 2
def supervise():
    m,seal=bindcheck();need(not AUD.exists() and not OUT.exists(),'ATTEMPT_ALREADY_EXISTS')
    AUD.mkdir(parents=True,exist_ok=False);OUT.mkdir(parents=True,exist_ok=False)
    start=time.time();mono=time.monotonic()
    write(AUD,'attempt_claim.json',{'schema':'sgc-diag-single-attempt-v0.2','wall_start':start,'monotonic_start':mono,'no_retry':True,'authority':'CURRENT_DIRECT_USER_EXECUTION'})
    env=os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    w=subprocess.Popen([sys.executable,'-B',str(SCRIPT),'--worker'],cwd=ROOT,env=env,start_new_session=True)
    d=subprocess.Popen([sys.executable,'-B',str(SCRIPT),'--watchdog'],cwd=ROOT,env=env,start_new_session=True)
    state={'wall_start':start,'monotonic_start':mono,'wall_deadline':start+7200,'monotonic_deadline':mono+7200,
      'config_sha':sha((PKG/'contract.json').read_bytes()),'numeric_code_id':sha(SCRIPT.read_bytes()),'interpreter':sys.executable,
      'identities':{'supervisor':ident(os.getpid()),'worker':ident(w.pid),'watchdog':ident(d.pid)}}
    write(AUD,'execution_state.json',state)
    wr=w.wait()
    try:dr=d.wait(timeout=15)
    except subprocess.TimeoutExpired:
        d.terminate();dr=d.wait()
    rec=json.loads((AUD/'execution_receipt.json').read_bytes()) if (AUD/'execution_receipt.json').exists() else {'status':'UNKNOWN_NO_WORKER_RECEIPT','modules':{}}
    success=wr==0 and dr==0 and rec.get('status')=='COMPLETE_S_A_B_FINITE_DIAGNOSTIC'
    before=allfiles();verified=[];bytes_=0;resource_rows=[]
    for f in before:
        raw=f.read_bytes();bytes_+=len(raw);need(bytes_<=512*2**20,'OUTPUT_VERIFY_LIMIT')
        verified.append({'path':str(f),'bytes':len(raw),'sha256':sha(raw)})
        if f.name=='resources.jsonl':resource_rows=[json.loads(v) for v in raw.splitlines() if v.strip()]
    for r in rec.get('outputs',[]):
        actual=next((z for z in verified if z['path']==r['path']),None)
        need(actual is not None and all(actual[k]==r[k] for k in ('bytes','sha256')),'COMMITTED_OUTPUT_IDENTITY')
    physical(resource(state),state)
    sr=write(AUD,'supervision_receipt.json',{'status':'COMPLETE' if success else 'STOPPED','identities':state['identities'],
      'worker_actual_wait_exit':wr,'watchdog_actual_wait_exit':dr,'supervisor_exit':'SEE_TOOL_WAIT','output_opaque_verification_passes':1,
      'output_opaque_verification_bytes':bytes_,'output_recovery_reads':0,'unknown_never_exit0':True,
      'output_count_before_final_controls':len(before),'artifacts_verified':verified,'runtime':m['runtime'],'FR50_release':False,
      'resource_extrema':{'samples':len(resource_rows),'max_owned_RSS_bytes':max((v['owned_RSS_bytes'] for v in resource_rows),default=None),'min_available_bytes':min((v['available_bytes'] for v in resource_rows),default=None),'min_disk_free_bytes':min((v['disk_free_bytes'] for v in resource_rows),default=None)}})
    artifacts=list(verified)
    artifacts.append(sr)
    write(AUD,'run_seal.json',{'schema':'sgc-fr40-diag-run-seal-v0.2','status':'COMPLETE_S_A_B_FINITE_DIAGNOSTIC' if success else 'STOPPED_NO_RETRY',
      'self_excluded':True,'artifacts':artifacts,'modules':rec.get('modules'),'worker_actual_wait_exit':wr,'watchdog_actual_wait_exit':dr,
      'original_practical_gate':'NOT_ADJUDICATED','MODEL_GATE':'EXPOSED_DEVELOPMENT_ONLY','FR50_release':False,'FR60_release':False,
      'no_old_artifact_writes':True,'scientific_retry':False,'original_clock':{k:state[k] for k in ('wall_start','monotonic_start','wall_deadline','monotonic_deadline')},
      'opaque_byte_hash_is_not_independent_replication_or_WORM':True})
    output_limits()
    print(json.dumps({'status':'COMPLETE' if success else 'STOPPED','worker_exit':wr,'watchdog_exit':dr,'modules':rec.get('modules'),'receipt':str(AUD/'execution_receipt.json'),'seal':str(AUD/'run_seal.json')},ensure_ascii=False),flush=True)
    return 0 if success else 1
if __name__=='__main__':
    a=argparse.ArgumentParser();g=a.add_mutually_exclusive_group(required=True);g.add_argument('--supervise',action='store_true');g.add_argument('--worker',action='store_true');g.add_argument('--watchdog',action='store_true')
    args=a.parse_args()
    try:sys.exit(supervise() if args.supervise else worker() if args.worker else watchdog())
    except BaseException as e:
        if isinstance(e,SystemExit):raise
        print('CONTROL_STOP '+type(e).__name__+': '+str(e)[:500],flush=True);sys.exit(1)
