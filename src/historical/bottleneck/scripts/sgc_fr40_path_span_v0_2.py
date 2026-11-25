"""Independent single-use FR40 path/span study; no numerical work at import."""
import argparse,collections,hashlib,io,json,math,os,re,shutil,signal,stat,struct,subprocess,sys,time,traceback,zipfile
from pathlib import Path
ROOT=Path('historical_sources/DTI_XAI_Bottleneck')
PKG=ROOT/'protocol/smiles_gine_bottleneck_fr40_path_span_v0_2'
AUD=ROOT/'audit/smiles_gine_bottleneck_fr40_path_span_v0_2/run_001'
OUT=ROOT/'results/smiles_gine_bottleneck_fr40_path_span_v0_2/run_001'
SCRIPT=ROOT/'scripts/sgc_fr40_path_span_v0_2.py'
IDS=('label_group_id','pair_group_id','endpoint')
SEEDS=(17,29,43,71,101);SL=tuple(map(str,SEEDS))+('MEAN5',)
EPS=('Kd','Ki');EL=EPS+('KD_KI_EQUAL',);OPS=('D','P','DP');OL=OPS+('ALL3_EQUAL',)
FUN=('q','h0','h_aff','h1');POL=('FIT_ROW_UNIFORM','FIT_PROTEIN_INPUT_UNIFORM')
N={'Kd':16,'Ki':16};GIB=2**30
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
    f=allfiles();need(len(f)+int(new)<=40,'OUTPUT_FILES_LIMIT')
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

LAMBDA=(1/32,1/16,1/8,1/4,1/2,1.);KS=(1,2,4,8,16,32)
def npy(raw,shape,dtype,np):
    f=io.BytesIO(raw);need(np.lib.format.read_magic(f)==(1,0),'NPY_VERSION')
    sh,fort,dt=np.lib.format.read_array_header_1_0(f);off=f.tell()
    need(tuple(shape)==sh and not fort and dt==np.dtype(dtype),'NPY_SCHEMA')
    need(len(raw)==off+math.prod(shape)*dt.itemsize,'NPY_BYTES')
    return np.ndarray(sh,dtype=dt,buffer=raw,offset=off)
def jlines(rows):return b''.join(enc(r) for r in rows)
def mean(v):v=list(v);need(bool(v),'EMPTY_MEAN');return math.fsum(float(x) for x in v)/len(v)
def sign(x):return 1 if x>0 else -1 if x<0 else 0
class Ctx:
    def __init__(self,m,state):
        self.m=m;self.state=state;self.used={};self.counters=collections.Counter()
        self.charged=collections.Counter();self.outputs=[];self.phase='CONTROL';self.completed_cases=[]
        self.last=0.;self.blocks=0;self.pending=None;self.checks=0;self.max_residual=0.
        self.sources={r['id']:r for r in m['sources']};self.panel_committed=False
    def guard(self):
        need(time.time()<self.state['wall_deadline'] and time.monotonic()<self.state['monotonic_deadline'],'DEADLINE')
        need(not (AUD/'safety_stop.json').exists(),'WATCHDOG_STOP')
        if time.monotonic()-self.last>20:
            need(live(self.state['identities']['supervisor']) and live(self.state['identities']['watchdog']),'SUPERVISION_IDENTITY_LOST')
            physical(resource(self.state),self.state);self.last=time.monotonic()
    def log(self,event,**v):
        row={'event':event,'phase':self.phase,'wall':time.time(),'monotonic':time.monotonic(),
          'numeric_code_id':self.state['numeric_code_id'],'plan_sha256':self.state['config_sha'],'ledger_id':'PATH_LEDGER_V0_2',**v}
        need(len(enc(row))<=4096,'EVENT_RECORD_BYTES');write(AUD,'events.jsonl',row,True)
    def reserve(self,key,fees,**ids):
        self.guard();need(self.pending is None,'UNFINISHED_BLOCK');self.blocks+=1;need(self.blocks<=1024,'BLOCKS_LIMIT')
        for k,n in fees.items():
            need(k in self.m['budgets'] and type(n) is int and n>=0,'FEE_SCOPE')
            self.charged[k]+=n;need(self.charged[k]<=self.m['budgets'][k],'BUDGET_'+k)
        self.pending={'block':self.blocks,'key':key,'fees':fees,'ids':ids}
        self.log('BLOCK_RESERVED',**self.pending)
    def done(self):
        need(self.pending is not None,'BLOCK_MISSING')
        for k,n in self.pending['fees'].items():self.counters[k]+=n
        self.log('BLOCK_RETURNED',**self.pending,certainty='RETURNED')
        self.pending=None
    def read(self,rid):
        rec=self.sources[rid];self.guard();need(rid not in self.used,'SOURCE_RETRY_FORBIDDEN')
        if rec['kind'] not in ('metadata','metadata_int64'):need(self.panel_committed,'PAYLOAD_BEFORE_PANEL_COMMIT')
        self.reserve('SOURCE:'+rid,{'source_files':1,'I_bytes':2*rec['bytes']},source_id=rid)
        self.used[rid]={'status':'UNKNOWN_RESERVED','charged_bytes':2*rec['bytes']}
        p=Path(rec['path']);s=os.lstat(p);need(stat.S_ISREG(s.st_mode) and s.st_size==rec['bytes'],'SOURCE_STAT')
        expected=rec.get('historical_fingerprint',rec.get('fingerprint',{}))
        need(all(fp(s).get(k)==str(v) for k,v in expected.items()),'SOURCE_HISTORICAL_FINGERPRINT')
        fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0));pieces=[];n=0;h=hashlib.sha256()
        try:
            need(fp(os.fstat(fd))==fp(s),'SOURCE_OPEN_IDENTITY')
            while True:
                z=os.read(fd,4*2**20)
                if not z:break
                h.update(z);pieces.append(z);n+=len(z);need(n<=rec['bytes'],'SOURCE_LONG_READ');self.guard()
            need(n==rec['bytes'] and h.hexdigest()==rec['sha256'],'SOURCE_CONTENT_IDENTITY:'+rid)
            need(fp(os.fstat(fd))==fp(s) and fp(os.lstat(p))==fp(s),'SOURCE_CHANGED')
        finally:os.close(fd)
        raw=b''.join(pieces)
        self.used[rid]={'status':'VERIFIED_ONE_HASH_ONE_PURPOSE_SAME_BUFFER','bytes':n,'sha256':h.hexdigest(),
          'fingerprint':fp(s),'historical_fingerprint':'MISSING_NOT_INVENTED' if not expected else expected,
          'opaque_passes':1,'purpose_passes':1,'I_charged_bytes':2*n,'OS_physical_IO':'UNKNOWN'}
        self.done();return raw
    def emit(self,name,data,d=OUT):
        rec=write(d,name,data);self.outputs.append(rec);return rec
    def array(self,name,arr,np):
        f=io.BytesIO();np.lib.format.write_array(f,arr,version=(1,0),allow_pickle=False);raw=f.getvalue()
        need(arr.flags.c_contiguous and len(raw)-arr.nbytes<=4096,'ARRAY_HEADER_OR_LAYOUT');return self.emit(name,raw)
    def numcheck(self,a,b,terms=None):
        z=close(a,b,terms);self.max_residual=max(self.max_residual,abs(z));self.checks+=1;return z
    def nonnegative(self,x,terms):
        need(math.isfinite(float(x)) and x>=-1e-12-1e-12*math.fsum(abs(float(v)) for v in terms),'NEGATIVE_CANCELLATION_BEYOND_TOLERANCE')
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,separators=(',',':')).encode('utf8')).digest()
def metadata(ctx,np):
    ctx.phase='METADATA_PANEL'
    raw=ctx.read('panel');panel=[]
    for line in raw.splitlines():
        lex=Lex(line);panel.append(lex.project(ctx.sources['panel']['fields'],optional=('v3_3_role',)))
    need(len(panel)==2242 and collections.Counter(p['endpoint'] for p in panel)=={'Kd':206,'Ki':2036},'PANEL_COUNTS')
    need(len({p['source_id'] for p in panel})==2242 and len({key(p) for p in panel})==2242,'PANEL_DUPLICATE')
    caseids={c['case_id'] for c in ctx.m['cases']}
    need(all(set(p['case_ids'])==caseids for p in panel),'PANEL10_SUPPORT')
    raw=ctx.read('indices');indices=npy(raw,(322659,14),'<i8',np)
    groups=collections.defaultdict(list)
    for l,o in enumerate(indices):
        sid,donor,e=map(int,o[:3]);need(0<=sid<5697 and 0<=donor<46140 and e in (0,1),'INDEX_IDENTITY_RANGE')
        need(int(o[13])==1 and bool(np.all(o[11:13]>0)),'SUPPORT_OR_WEIGHT')
        need(bool(np.all((o[3:7]>=0)&(o[3:7]<19748))) and bool(np.all((o[7:11]>=0)&(o[7:11]<56962))),'CORNER_RANGE')
        need(int(o[3])!=int(o[6]) and int(o[3])!=int(o[4]) and int(o[3])!=int(o[5]),'INHERITED_BRANCH_NONIDENTITY')
        groups[sid].append(l)
    need(set(groups)=={p['source_id'] for p in panel},'INDEX_PANEL_SOURCE_SET')
    selected=[];links=[];composition={'status':'PANEL_READY','endpoint':{},'donor_biological_identity':'NOT_AVAILABLE_NO_SOURCE_EXPANSION','input_identity_not_independent_entities':True}
    for ep in EPS:
        original=[p for p in panel if p['endpoint']==ep];eligible=[]
        for p in original:
            rows=groups[p['source_id']];need(len(rows)==p['n_reference_rows'],'REFERENCE_ROW_COUNT')
            need(all(int(indices[l,2])==EPS.index(ep) for l in rows),'SOURCE_ENDPOINT_JOIN')
            donors=[int(indices[l,1]) for l in rows];need(len(set(donors))==len(donors),'DUPLICATE_DONOR_ROW')
            need(all(int(indices[l,11])==len(rows) for l in rows),'ORIGINAL_MAIN_DENOMINATOR')
            if len(donors)>=2:
                d=digest(['SGC-FR40-PATH-v0.1','source',ep,str(p['label_group_id']),str(p['pair_group_id'])])
                eligible.append((d,canonical(p),p,rows))
        need(len(eligible)>=16,'PANEL_NOT_READY')
        chosen=sorted(eligible,key=lambda x:(x[0],x[1]))[:16];ep_sources=[];ep_links=[]
        for sd,_,p,rows in chosen:
            chosenrows=sorted(rows,key=lambda l:(digest(['SGC-FR40-PATH-v0.1','donor',ep,str(p['label_group_id']),str(p['pair_group_id']),str(int(indices[l,1]))]),int(indices[l,1])))[:2]
            ints=[[int(x) for x in indices[l]] for l in chosenrows]
            ws=[1./o[12] for o in ints];total=ws[0]+ws[1];sens=[w/total for w in ws];main=[.5,.5]
            same=ints[0][12]==ints[1][12];computed=main==sens
            status='DESIGN_IDENTICAL_ON_SELECTED_SOURCE' if same else 'DESIGN_DIFFERENT_COMPUTED_EQUAL' if computed else 'DESIGN_DIFFERENT'
            q={**p,'selected_source_index0':len(selected),'source_digest':sd.hex(),'selected_operation_rows':chosenrows,
              'original_main_denominators':[o[11] for o in ints],'original_sensitive_denominators':[o[12] for o in ints],
              'restricted_main_weights':main,'restricted_sensitive_weights':sens,'reference_design_status':status,'computed_weights_equal':computed}
            selected.append(q);ep_sources.append(q)
            for j,(l,o) in enumerate(zip(chosenrows,ints)):
                z={'link_index0':len(links),'selected_source_index0':q['selected_source_index0'],'source_id':p['source_id'],
                  'endpoint':ep,'label_group_id':p['label_group_id'],'pair_group_id':p['pair_group_id'],'original_operation_row0':l,
                  'donor_FIT_index0':o[1],'donor_digest':digest(['SGC-FR40-PATH-v0.1','donor',ep,str(p['label_group_id']),str(p['pair_group_id']),str(o[1])]).hex(),
                  'operation_index_columns':o,'restricted_main_weight':main[j],'restricted_sensitive_weight':sens[j]}
                links.append(z);ep_links.append(z)
        counts=lambda field:collections.Counter(p[field] for p in ep_sources)
        dc=collections.Counter(x['donor_FIT_index0'] for x in ep_links)
        composition['endpoint'][ep]={'N_O_SRC':len(original),'N_eligible_with_two_distinct_donor_rows':len(eligible),'N_selected_target':16,'N_selected_actual':len(chosen),
          'selected_fraction_O_SRC':16/len(original),'selected_fraction_eligible':16/len(eligible),
          'source_input_identities':{k:{'unique':len(counts(k)),'row_counts':dict(counts(k)),'max_row_share':max(counts(k).values())/16} for k in ('smiles_sha256','target_sequence_key')},
          'donor_FIT_row_counts':dict(dc),'donor_FIT_unique_rows':len(dc),'donor_FIT_max_reuse':max(dc.values()),'link_denominator':32,
          'reference_design_source_counts':dict(collections.Counter(p['reference_design_status'] for p in ep_sources)),
          'reference_design_source_mass':{k:v/16 for k,v in collections.Counter(p['reference_design_status'] for p in ep_sources).items()}}
    dc=collections.Counter(x['donor_FIT_index0'] for x in links);composition['overall_donor_FIT']={'rows':dict(dc),'unique':len(dc),'max_reuse':max(dc.values()),'denominator':64}
    need(len(selected)==32 and len(links)==64,'SELECTED_COUNTS')
    ctx.emit('source_panel.jsonl',jlines(selected));ctx.emit('link_panel.jsonl',jlines(links));ctx.emit('panel_composition.json',composition)
    ctx.emit('axes.json',{'case_order':[c['case_id'] for c in ctx.m['cases']],'link_order':'Kd,Ki;source_digest;donor_digest',
      'operations':OPS,'nodes':[k/32 for k in range(33)],'lambda':LAMBDA,'function':FUN,'native_head':['h0','h1'],
      'physical_point_key':['case','native_head','endpoint','signed-zero-normalized-f64-vector-with-full-equality'],
      'logical_use_key':['case','source','donor','operation','k','native_head'],'panel_artifacts':ctx.outputs[:3]})
    ctx.panel_committed=True;ctx.log('PANEL_COMMITTED',sources=32,links=64,panel_artifacts=ctx.outputs[:4])
    return selected,links
def calibrators(ctx):
    raw=ctx.read('calibrators');lex=Lex(raw);top=lex.fields();out={}
    for span in lex.items(top['slots'][0]):
        f=lex.fields(span[0])
        if lex.val(f['family'])!='affine':continue
        row={k:lex.val(f[k]) for k in ('case_id','endpoint','family','mode','coefficients','slot_sha256')}
        need(len(row['coefficients'])==2,'AFFINE_FIELDS')
        a,b=map(float,row['coefficients']);need(math.isfinite(a) and a>0 and math.isfinite(b),'AFFINE_COEFFICIENT')
        key_=(row['case_id'],row['endpoint']);need(key_ not in out,'AFFINE_DUPLICATE');out[key_]=(a,b)
    need(set(out)=={(c['case_id'],ep) for c in ctx.m['cases'] for ep in EPS},'AFFINE20');return out

def checkpoint(ctx,torch,np,rid,case,higher):
 schema=ctx.m['checkpoint_schema'];raw=ctx.read(rid);shapes=schema['head_state_shapes'] if higher else schema['state_shapes'][case['arm']]
 cap=schema['per_h1'] if higher else schema['per_original'][case['arm']]
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  names=z.infolist();need(len(names)<=cap['zip_entries_max'] and all(not x.flag_bits&1 and x.compress_type==0 and '..' not in x.filename.split('/') for x in names),'CHECKPOINT_ZIP_LAYOUT')
  storage=[x.file_size for x in names if re.fullmatch(r'[^/]+/data/[0-9]+',x.filename)]
  sizes=[math.prod(v)*4 for v in shapes.values()]
  need(sorted(storage)==sorted(sizes) and sum(storage)==cap['storage_bytes'],'STORAGE_BYTE_MULTISET')
  others=[x for x in names if not re.fullmatch(r'[^/]+/data/[0-9]+',x.filename)]
  need(len(others)<=cap['nonstorage_entries_max'] and sum(x.file_size for x in others)<=cap['nonstorage_payload_bytes_max'],'CHECKPOINT_METADATA_BUDGET')
  need(all('/'.join(x.filename.split('/')[1:]) in schema['zip_metadata_names'] for x in others),'CHECKPOINT_METADATA_NAMES')
 ctx.reserve('CHECKPOINT_DECODE:'+rid,{'checkpoint_decode':1,'checkpoint_storages':len(storage),'checkpoint_storage_bytes':sum(storage),'nonfusion_incidental_bytes':0 if higher else cap['nonfusion_bytes'],'head_parameters':295554})
 obj=torch.load(io.BytesIO(raw),weights_only=True,map_location='cpu',mmap=False)
 roots=schema['roots'][case['arm']+'_h1'] if higher else schema['roots']['original']
 need(set(obj)==set(roots) and obj['arm']==case['arm'] and obj['seed']==case['seed'],'CHECKPOINT_ROOT_IDENTITY')
 need(isinstance(obj['epoch'],int) and 1<=obj['epoch']<=150,'CHECKPOINT_EPOCH')
 if higher:
  need(obj['LR']==.0006 and obj['cache_sha256']==ctx.sources[case['cache']]['sha256'] and obj[roots[-1]] is True,'HIGH_LR_HEAD_IDENTITY')
 else:need(obj['epoch']==case['h0_original_epoch'],'ORIGINAL_EPOCH')
 state=obj['state_dict'];need(set(state)==set(shapes),'STATE_KEYS');ptrs=set()
 for k,shape in shapes.items():
  t=state[k];need(isinstance(t,torch.Tensor) and list(t.shape)==shape and t.dtype==torch.float32 and t.storage_offset()==0 and t.is_contiguous() and t.untyped_storage().nbytes()==t.numel()*4,'TENSOR_METADATA')
  ptr=t.untyped_storage().data_ptr();need(ptr not in ptrs,'STORAGE_ALIAS');ptrs.add(ptr)
 head={k:state[k if higher else 'fusion.'+k] for k in schema['head_state_shapes']}
 for t in head.values():need(bool(torch.isfinite(t).all()),'HEAD_PARAMETER_NONFINITE')
 ctx.done();return head

def make_head(torch,state):
    class Fusion(torch.nn.Module):
        def __init__(self):
            super().__init__();self.first=torch.nn.Linear(1024,256,dtype=torch.float64);self.second=torch.nn.Linear(256,128,dtype=torch.float64);self.Kd=torch.nn.Linear(128,1,dtype=torch.float64);self.Ki=torch.nn.Linear(128,1,dtype=torch.float64)
        def forward(self,x,e):
            p1=self.first(x);p2=self.second(torch.relu(p1));v=torch.relu(p2);both=torch.cat((self.Kd(v),self.Ki(v)),dim=-1)
            route=torch.nn.functional.one_hot(torch.tensor([e],dtype=torch.int64),num_classes=2).to(both.dtype);y=(both*route).sum(dim=-1)
            return y,p1,p2
    model=Fusion();model.load_state_dict(state,strict=True);model.eval()
    for p in model.parameters():p.requires_grad_(False)
    return model
def cache(ctx,np,rid,links):
    raw=ctx.read(rid);f=io.BytesIO(raw);need(np.lib.format.read_magic(f)==(1,0),'CACHE_VERSION')
    sh,fort,dt=np.lib.format.read_array_header_1_0(f);off=f.tell()
    need(sh==(56962,1024) and not fort and dt==np.dtype('<f4') and off+56962*4096==len(raw),'CACHE_SCHEMA')
    rows=sorted({int(k) for l in links for k in l['operation_index_columns'][7:11]});need(len(rows)<=224,'CACHE_ROW_DECODE_CAP')
    ctx.reserve('CACHE_PROJECT:'+rid,{'cache_rows':len(rows),'cache_coordinates':len(rows)*1024})
    V={}
    for k in rows:
        v=np.frombuffer(raw,dtype='<f4',count=1024,offset=off+k*4096);finite(np,v,'CACHE_NONFINITE');V[k]=v.astype(np.float64)
    ctx.done()
    for l in links:
        o=l['operation_index_columns'];A,B,C,D=[V[j] for j in o[7:11]]
        for x,y,lo,hi in ((A,B,0,256),(A,C,256,1024),(C,D,0,256),(B,D,256,1024)):
            need(bool(np.array_equal(x[lo:hi],y[lo:hi])),'CACHE_FOUR_CORNER_COMPOSABILITY')
    return V
def points(np,V,links):
    pts=[];lookup=collections.defaultdict(list);uses=[];mapping=np.empty((64,3,33),dtype=np.int32);deltas=np.empty((64,3,1024),dtype=np.float64)
    reused=0
    for li,l in enumerate(links):
        o=l['operation_index_columns'];z0=V[o[7]]
        for oi,j in enumerate((9,8,10)):
            z1=V[o[j]];delta=z1-z0;finite(np,delta,'DELTA_NONFINITE');deltas[li,oi]=delta
            for k in range(33):
                z=np.ascontiguousarray(z0 if k==0 else z1 if k==32 else z0+(k/32)*delta)
                finite(np,z,'NODE_NONFINITE');canonicalz=z.copy();canonicalz[canonicalz==0]=0.
                dg=hashlib.sha256(canonicalz.tobytes()).digest();key_=(l['endpoint'],dg);pid=None
                for q in lookup[key_]:
                    if bool(np.array_equal(z,pts[q][1])):pid=q;break
                if pid is None:
                    pid=len(pts);pts.append((EPS.index(l['endpoint']),z.copy(),dg.hex()));uses.append([]);lookup[key_].append(pid)
                else:reused+=1
                uses[pid].append((li,oi,k));mapping[li,oi,k]=pid
    need(len(pts)<=6176 and len(pts)+reused==6336,'PHYSICAL_LOGICAL_POINT_CLOSURE')
    for si in range(32):
        p=[mapping[li,oi,0] for li in (2*si,2*si+1) for oi in range(3)];need(len(set(p))==1,'MANDATORY_SOURCE_REUSE')
    return pts,uses,mapping,deltas,reused
def regions(np,p1,p2):
    a=np.concatenate((p1.detach().numpy().ravel(),p2.detach().numpy().ravel()));finite(np,a,'REGION_NONFINITE')
    sg=np.sign(a).astype(np.int8);code=(sg+1).astype(np.uint8)
    packed=((code[0::4]<<6)|(code[1::4]<<4)|(code[2::4]<<2)|code[3::4]).tobytes()
    return sg,int(np.sum(a==0)),bytes.fromhex(sha(packed))
def check_chain_geo(ctx,torch,np,model,g,sg,pts,mapping,deltas,li,ep,case,hid,pid):
    ctx.reserve('CHAIN_GEOMETRY:'+case+':'+str(hid)+':'+ep,
      {'chain_groups':1,'chain_matvec':2,'chain_mask_mult':384,'chain_coord_compare':1024,
       'FD_geometry_dot':1,'FD_geometry_coord_subtract':2048,'FD_geometry_coord_divide':1024,
       'FD_center_coord_subtract':3072,'FD_geometry_maxabs_coordinates':2048},case=case,head=hid,endpoint=ep,point_id=pid)
    we=getattr(model,ep).weight.detach().reshape(-1)
    sg1=torch.tensor(sg[:256]>0,dtype=torch.float64);sg2=torch.tensor(sg[256:]>0,dtype=torch.float64)
    z=model.second.weight.detach().T@(sg2*we);chain=model.first.weight.detach().T@(sg1*z)
    gn=chain.numpy();finite(np,gn,'CHAIN_NONFINITE');allow=1e-6+1e-5*np.maximum(np.abs(gn),np.abs(g));res=gn-g
    row={'case_id':case,'native_head':['h0','h1'][hid],'endpoint':ep,'link_index0':li,'operation':'DP','k':16,
      'point_ids':[int(mapping[li,2,k]) for k in (15,16,17)],'chain_max_abs_residual':float(np.max(np.abs(res))),
      'chain_max_tolerance_normalized_residual':float(np.max(np.abs(res)/allow)),'chain_compared_coordinates':1024,'chain_failed_coordinates':int(np.sum(np.abs(res)>allow))}
    need(row['chain_failed_coordinates']==0,'CHAIN_AD_MISMATCH')
    z15,z16,z17=[pts[int(mapping[li,2,k])][1] for k in (15,16,17)]
    vfd=(z17-z15)/(2/32);finite(np,vfd,'FD_GEOMETRY_NONFINITE');geo=float(np.dot(g,vfd))
    row.update(m_geo=geo,max_abs_direction_geometry=float(np.max(np.abs(vfd-deltas[li,2]))),
      max_abs_centering_geometry=float(np.max(np.abs((z17-z16)-(z16-z15)))),
      whole_vectors_distinct=[bool(np.any(z15!=z16)),bool(np.any(z17!=z16)),bool(np.any(z15!=z17))],
      chain_status='EMPIRICAL_MATCH_ZERO0',geometry_projection_residual=None)
    need(all(math.isfinite(row[k]) for k in ('m_geo','max_abs_direction_geometry','max_abs_centering_geometry')),'GEOMETRY_SCALAR_NONFINITE')
    ctx.done();return row
def native_case(ctx,torch,np,case,V,links,affine,NV,ZC,RH):
    cid=case['case_id'];ci=case['case_code'];pts,uses,mapping,deltas,reused=points(np,V,links)
    mapping_sha=sha(mapping.tobytes());native=np.empty((64,3,33,2,2),dtype=np.float64);qual=[];reuse=[]
    first={'Kd':0,'Ki':32}
    for hid,rid in enumerate((case['h0_checkpoint'],case['h1_head'])):
        ctx.phase=cid+':'+('h0' if hid==0 else 'h1')
        state=checkpoint(ctx,torch,np,rid,case,hid==1)
        ctx.reserve('HEAD_CONSTRUCT_APPLY:'+cid+':'+str(hid),{'head_construct':1,'state_apply':1})
        model=make_head(torch,state);del state;ctx.done()
        masks=np.empty((len(pts),384),dtype=np.int8);values=np.empty(len(pts),dtype=np.float64)
        checks=collections.defaultdict(list)
        for ep,li in first.items():checks[int(mapping[li,2,16])].append((ep,li))
        head_checks=[]
        for start in range(0,len(pts),256):
            end=min(start+256,len(pts));dotn=sum(len(uses[p]) for p in range(start,end))
            ctx.reserve('MODEL:'+cid+':'+str(hid)+':'+str(start),
              {'forward':end-start,'input_AD':end-start,'dual_output_scalars':2*(end-start),'path_dot':dotn,'preactivation_observations':384*(end-start),'model_blocks':1},
              case=cid,head=hid,physical_range=[start,end],logical_mapping_sha256=mapping_sha,
              physical_key_recipe='case,head,endpoint,full_f64_vector; signedzero hash normalized; equality checked',
              logical_key_recipe='case,source,donor,op,k,head; mapping use slots')
            pending=ctx.pending
            for pid in range(start,end):
                epcode,z,dg=pts[pid];x=torch.tensor(z.reshape(1,1024),dtype=torch.float64,requires_grad=True)
                y,p1,p2=model(x,epcode);v=float(y.detach().item());need(math.isfinite(v),'FORWARD_NONFINITE')
                g=torch.autograd.grad(y,x,retain_graph=False,create_graph=False)[0].detach().numpy().ravel();finite(np,g,'INPUT_AD_NONFINITE')
                sg,zeros,dgmask=regions(np,p1,p2);masks[pid]=sg;values[pid]=v
                for li,oi,k in uses[pid]:
                    m=float(np.dot(g,deltas[li,oi]));need(math.isfinite(m),'PATH_PROJECTION_NONFINITE')
                    native[li,oi,k,hid]=v,m;ZC[ci,li,oi,k,hid]=zeros;RH[ci,li,oi,k,hid]=np.frombuffer(dgmask,dtype=np.uint8)
                # Auxiliary work has independent reservation while this model block remains live.
                if pid in checks:
                    ctx.pending=None
                    for ep,li in checks[pid]:
                        row=check_chain_geo(ctx,torch,np,model,g,sg,pts,mapping,deltas,li,ep,cid,hid,pid)
                        row['m_path16']=float(native[li,2,16,hid,1]);row['geometry_projection_residual']=row['m_geo']-row['m_path16'];head_checks.append(row)
                    ctx.pending=pending
            ctx.done()
        for row in head_checks:
            li=row['link_index0'];pids=row['point_ids'];sg=[masks[p] for p in pids]
            partitions_same=bool(np.array_equal(sg[0],sg[1]) and np.array_equal(sg[1],sg[2]));nozero=not any(bool(np.any(s==0)) for s in sg)
            row.update(partitions_same=partitions_same,no_observed_zero=nozero,
              computed_region_sha256=[RH[ci,li,2,k,hid].tobytes().hex() for k in (15,16,17)],
              FD=None,FD_original_residual=None,FD_geometry_residual=None,FD_signed_closure_residual=None)
            eligible=all(row['whole_vectors_distinct']) and partitions_same and nozero
            if eligible:
                ctx.reserve('FD_SCALAR:'+cid+':'+str(hid)+':'+row['endpoint'],{'FD_eligible_values':1,'FD_residual_values':3})
                fd=(float(values[pids[2]])-float(values[pids[0]]))/(2/32);mgeo=row['m_geo'];m=row['m_path16']
                res=fd-mgeo;orig=fd-m;geom=row['geometry_projection_residual'];al=1e-6+1e-5*max(abs(fd),abs(mgeo))
                row.update(FD=fd,FD_original_residual=orig,FD_geometry_residual=res,FD_signed_closure_residual=orig-(res+geom),FD_allowed_error=al,FD_status='EMPIRICAL_MATCH')
                ctx.numcheck(orig,res+geom,[orig,res,geom]);need(abs(res)<=al,'EMPIRICAL_FD_GEOMETRY_MISMATCH');ctx.done()
            else:row['FD_status']='UNRESOLVED_REPEAT_KINK_OR_CROSS_REGION'
            qual.append(row)
        reuse.append({'case_id':cid,'native_head':['h0','h1'][hid],'logical_slots':6336,'physical_points':len(pts),
          'reused_slots':reused,'mandatory_source_identities':32,'distinct_source_points':len({int(mapping[li,0,0]) for li in range(0,64,2)}),'source_logical_uses':192,'mapping_sha256':mapping_sha,'blocks':math.ceil(len(pts)/256),
          'point_vector_equality_checked':True,'source_endpoint_key_preserved':True,'no_gradient_or_activation_persisted':True})
        del masks,values,model
    ctx.reserve('DERIVE_FOUR_FUNCTIONS:'+cid,{'function_node_views':25344,'span_views':4608,'old_endpoint_scalars':1536})
    for li,l in enumerate(links):
        a,b=affine[(cid,l['endpoint'])];f0=native[li,:,:,0,0];m0=native[li,:,:,0,1];f1=native[li,:,:,1,0];m1=native[li,:,:,1,1]
        fa=a*f0+b;ma=a*m0
        NV[ci,li,:,:,0,0]=f1-fa;NV[ci,li,:,:,0,1]=m1-ma
        NV[ci,li,:,:,1,0]=f0;NV[ci,li,:,:,1,1]=m0
        NV[ci,li,:,:,2,0]=fa;NV[ci,li,:,:,2,1]=ma
        NV[ci,li,:,:,3,0]=f1;NV[ci,li,:,:,3,1]=m1
    finite(np,NV[ci],'DERIVED_NODE_NONFINITE');ctx.done()
    return qual,reuse
def classify(np,r,A,e):
    c=np.empty(r.shape,dtype=np.uint8)
    c[(r==0)&(A==0)]=0;c[(r==0)&(A!=0)]=1;c[(r!=0)&(A==0)]=2
    both=(r!=0)&(A!=0);opposite=both&(np.sign(r)!=np.sign(A));c[opposite]=3
    same=both&~opposite;under=same&(np.abs(A)<=np.abs(r));c[under]=4
    over=same&~under;c[over&(np.abs(e)<np.abs(r))]=5;c[over&(np.abs(e)==np.abs(r))]=6;c[over&(np.abs(e)>np.abs(r))]=7
    return c
def spans_and_carry(ctx,np,case,links,NV,SV,SC):
    ci=case['case_code'];cid=case['case_id'];v=NV[ci]
    for j,(lam,k) in enumerate(zip(LAMBDA,KS)):
        r=v[:,:,k,:,0]-v[:,:,0,:,0];A=lam*v[:,:,0,:,1];e=r-A;s=np.abs(r)-np.abs(e)
        SV[ci,:,:,j,:,:]=np.stack((A,r,e,s),axis=-1);SC[ci,:,:,j,:]=classify(np,r,A,e)
    finite(np,SV[ci],'SPAN_NONFINITE')
    raw=ctx.read(case['old_response']);old=npy(raw,(322659,4,12),'<f8',np)
    projection=np.array([old[l['original_operation_row0'],:,:6] for l in links]);finite(np,projection,'OLD_SELECTED_NONFINITE')
    newA=SV[ci,:,:,5,:,0].transpose(0,2,1);newr=SV[ci,:,:,5,:,1].transpose(0,2,1)
    ares=newA-projection[:,:,:3];rres=newr-projection[:,:,3:6]
    need(bool(np.all(np.abs(ares)<=1e-6+1e-5*np.maximum(np.abs(newA),np.abs(projection[:,:,:3])))),'OLD_ENDPOINT_A_MISMATCH')
    need(bool(np.all(np.abs(rres)<=1e-5+1e-5*np.maximum(np.abs(newr),np.abs(projection[:,:,3:6])))),'OLD_ENDPOINT_R_MISMATCH')
    return {'case_id':cid,'checked_scalars':1536,'A_scalars':768,'r_scalars':768,'A_max_absolute_residual':float(np.max(np.abs(ares))),
      'r_max_absolute_residual':float(np.max(np.abs(rres))),'A_tolerance':[1e-6,1e-5],'r_tolerance':[1e-5,1e-5],'status':'EMPIRICAL_CARRY_MATCH'}


SPAN_FIELDS=('E','B','Skill','P','H')
PATH_FIELDS=('V_increment','C_increment','Qplus32','Qminus32','Q32','C_projected32','Q16','resolution_difference','endpoint_integral_residual','non_source_zero_fraction')
CURVE_FIELDS=('m','abs_m','D_abs','qualification_mass','u_numerator','D_align_numerator','reversal_numerator','source_zero_mass','exact_zero_mass')
J_FIELDS=('M','B','E','P','H','Skill','C_increment','C_projected32')
CLASS_GROUP=('C3_DIRECTION','C7_SAME_SIGN_HARM','OTHER_C0_C1_C2_C4_C5_C6')
REV_GROUP=('SOURCE_ZERO_PROJECTION','REVERSAL_OBSERVED','NOT_OBSERVED_ON_GRID')
def scope_meta(arm,seed,ep,op,fi,pi):
    agg=seed=='MEAN5' or ep=='KD_KI_EQUAL' or op=='ALL3_EQUAL'
    return {'arm':arm,'seed_layer':seed,'endpoint_layer':ep,'operation_layer':op,'function_id':FUN[fi],
      'reference_scheme':POL[pi],'source_denominator':None if agg else 16,'raw_link_count':None if agg else 32,
      'denominator_map':{'Kd_sources':16,'Ki_sources':16,'links_per_endpoint':32,'seeds_per_arm':5},
      'evidence_tier':'FINITE_EXPOSED_DEVELOPMENT_SMALL_PANEL'}
def scope_slots(arm,seed,ep,op,cases):
    cs=[c['case_code'] for c in cases if c['arm']==arm and (seed=='MEAN5' or str(c['seed'])==seed)]
    es=range(2) if ep=='KD_KI_EQUAL' else [EPS.index(ep)];os_=range(3) if op=='ALL3_EQUAL' else [OPS.index(op)]
    return [(ci,e,o) for ci in cs for e in es for o in os_]
def path_analysis(ctx,np,links,NV,SV,SC):
    ctx.phase='FROZEN_DERIVATIONS_AND_SUMMARIES'
    ctx.reserve('FIXED_ARITHMETIC_GRID',{'path_function_rows':7680,'weighted_path_views':15360,
      'summary_rows':1152,'span_summary_rows':6912,'curve_summary_rows':38016,'joint_summary_rows':10368,
      'joint_unconditional_fields':82944,'joint_conditional_fields':72576,'lambda_summary_divisions':13824,
      'readout_summary_rows':1728,'weighted_span_views':92160})
    raw={};pmetrics=[];jointcount={};weights={}
    for e in range(2):
        ids=[i for i,l in enumerate(links) if l['endpoint']==EPS[e]]
        for pi,field in enumerate(('restricted_main_weight','restricted_sensitive_weight')):
            w=np.array([links[i][field]/16 for i in ids],dtype=np.float64);ctx.numcheck(float(w.sum()),1.);weights[e,pi]=(ids,w)
    for ci in range(10):
      for e in range(2):
       ids,_=weights[e,0]
       for o in range(3):
        readout=np.empty((32,6,3),dtype=np.float64)
        for j,k in enumerate(KS):
            A=SV[ci,ids,o,j,:,0];r=SV[ci,ids,o,j,:,1];m=NV[ci,ids,o,k,:,1]
            readout[:,j,0]=np.abs(r[:,3])+np.abs(r[:,2])-np.abs(r[:,0])
            readout[:,j,1]=np.abs(A[:,3])+np.abs(A[:,2])-np.abs(A[:,0])
            readout[:,j,2]=np.abs(m[:,3])+np.abs(m[:,2])-np.abs(m[:,0])
            for v,terms in zip(readout[:,j,:].ravel(),np.stack((np.abs(r[:,3])+np.abs(r[:,2])+np.abs(r[:,0]),np.abs(A[:,3])+np.abs(A[:,2])+np.abs(A[:,0]),np.abs(m[:,3])+np.abs(m[:,2])+np.abs(m[:,0])),axis=1).ravel()):
                ctx.nonnegative(float(v),[terms])
        for fi in range(4):
            f=NV[ci,ids,o,:,fi,0];m=NV[ci,ids,o,:,fi,1];m0=m[:,0];eligible=m0!=0
            signs=np.sign(m0);rev=(np.sign(m)!=signs[:,None])&(m!=0)&eligible[:,None];rev[:,0]=False
            counts=np.sum(rev[:,1:],axis=1);zero_count=np.sum(m[:,1:]==0,axis=1)
            first=np.where(counts>0,np.argmax(rev,axis=1),0)
            delta=f[:,1:]-f[:,:-1];V=np.sum(np.abs(delta),axis=1);r1=SV[ci,ids,o,5,fi,1];cinc=V-np.abs(r1)
            wt=np.full(33,1/32,dtype=np.float64);wt[[0,32]]=1/64
            qp=np.sum(np.maximum(m,0)*wt,axis=1);qn=np.sum(np.maximum(-m,0)*wt,axis=1);q32=qp-qn;cproj=qp+qn-np.abs(q32)
            wt16=np.full(17,1/16,dtype=np.float64);wt16[[0,16]]=1/32;q16=np.sum(m[:,::2]*wt16,axis=1)
            pm=np.stack((V,cinc,qp,qn,q32,cproj,q16,q32-q16,r1-q32,zero_count/32),axis=1)
            curves=np.stack((m,np.abs(m),np.abs(m0[:,None])-np.abs(m),np.broadcast_to(eligible[:,None],m.shape),
              np.where(eligible[:,None],signs[:,None]*m,0),np.where(eligible[:,None],np.abs(m0[:,None])-signs[:,None]*m,0),
              rev,np.broadcast_to((~eligible)[:,None],m.shape),m==0),axis=-1).astype(np.float64)
            hist=np.zeros((32,34),dtype=np.float64)
            for j,li in enumerate(ids):
                ctx.nonnegative(float(cinc[j]),[V[j],r1[j]]);ctx.nonnegative(float(cproj[j]),[qp[j],qn[j],q32[j]])
                hist[j,33 if not eligible[j] else 32 if counts[j]==0 else int(first[j])-1]=1.
                cls=int(SC[ci,li,o,5,fi]);cg=0 if cls==3 else 1 if cls==7 else 2;rg=0 if not eligible[j] else 1 if counts[j]>0 else 2
                pmetrics.append({'case_id':ctx.m['cases'][ci]['case_id'],'source_id':links[li]['source_id'],'donor_FIT_index0':links[li]['donor_FIT_index0'],
                  'link_index0':li,'endpoint':EPS[e],'operation':OPS[o],'function_id':FUN[fi],
                  **{k:float(pm[j,k0]) for k0,k in enumerate(PATH_FIELDS)},'source_projection':float(m0[j]),
                  'sampled_non_source_reversal_count':int(counts[j]) if eligible[j] else None,
                  'sampled_non_source_reversal_fraction':float(counts[j]/32) if eligible[j] else None,'non_source_zero_count':int(zero_count[j]),
                  'first_observed_reversal':float(first[j]/32) if eligible[j] and counts[j]>0 else None,
                  'first_observed_reversal_status':'SOURCE_ZERO_PROJECTION' if not eligible[j] else 'NOT_OBSERVED_ON_GRID' if counts[j]==0 else 'OBSERVED_ON_GRID',
                  'class_group_at_lambda1':CLASS_GROUP[cg],'sampled_reversal_group':REV_GROUP[rg]})
            A=SV[ci,ids,o,:,fi,0];r=SV[ci,ids,o,:,fi,1];err=SV[ci,ids,o,:,fi,2];s=SV[ci,ids,o,:,fi,3];cls=SC[ci,ids,o,:,fi]
            B=np.abs(r);E=np.abs(err);P=np.maximum(s,0);H=np.maximum(-s,0)
            spanbase=np.stack((E,B,s,P,H),axis=-1)
            cl=np.stack([np.stack((cls==k,np.where(cls==k,P,0),np.where(cls==k,H,0)),axis=-1) for k in range(8)],axis=-2)
            span=np.concatenate((spanbase,cl.reshape(32,6,24)),axis=-1)
            cg=np.where(cls[:,5]==3,0,np.where(cls[:,5]==7,1,2));rg=np.where(~eligible,0,np.where(counts>0,1,2));cell=cg*3+rg
            jf=np.stack((np.ones(32),B[:,5],E[:,5],P[:,5],H[:,5],s[:,5],cinc,cproj),axis=-1)
            jvals=np.stack([np.where((cell==k)[:,None],jf,0) for k in range(9)],axis=1)
            for pi in range(2):
                _,w=weights[e,pi];L=lambda a:np.tensordot(w,a,axes=(0,0))
                sr=L(span);sr[:,2]=sr[:,1]-sr[:,0]
                cr=L(curves);pr=L(pm);jr=L(jvals);hr=L(hist);rr=L(readout)
                raw[ci,e,o,fi,pi]={'span':sr,'curve':cr,'path':pr,'joint':jr,'hist':hr,'readout':rr,
                  'reversal_num':float(np.dot(w,np.where(eligible,counts/32,0))),'qualification_mass':float(np.dot(w,eligible))}
                jointcount[ci,e,o,fi,pi]=[int(np.sum(cell==k)) for k in range(9)]
                for j in range(6):
                    ctx.numcheck(sr[j,2],sr[j,3]-sr[j,4]);ctx.numcheck(float(sr[j,5:].reshape(8,3)[:,0].sum()),1.)
                ctx.numcheck(float(jr[:,0].sum()),1.);ctx.numcheck(float(hr.sum()),1.)
    need(len(pmetrics)==7680,'PATH_FUNCTION_ROWS')
    ctx.emit('path_metrics.jsonl',jlines(pmetrics))
    spans=[];curves=[];paths=[];joints=[];readouts=[]
    agg=lambda slots,fi,pi,field:np.array([raw[ci,e,o,fi,pi][field] for ci,e,o in slots],dtype=np.float64).mean(axis=0)
    for arm in ('S','G'):
     for seed in SL:
      for ep in EL:
       for op in OL:
        slots=scope_slots(arm,seed,ep,op,ctx.m['cases'])
        israw=len(slots)==1
        for fi in range(4):
         for pi in range(2):
            meta=scope_meta(arm,seed,ep,op,fi,pi);sr=agg(slots,fi,pi,'span');cr=agg(slots,fi,pi,'curve')
            pr=agg(slots,fi,pi,'path');jr=agg(slots,fi,pi,'joint');hr=agg(slots,fi,pi,'hist')
            mass=mean(raw[ci,e,o,fi,pi]['qualification_mass'] for ci,e,o in slots)
            reverse_num=mean(raw[ci,e,o,fi,pi]['reversal_num'] for ci,e,o in slots)
            ctx.numcheck(float(jr[:,0].sum()),1.);ctx.numcheck(float(hr.sum()),1.)
            for j,lam in enumerate(LAMBDA):
                v=sr[j];row={**meta,'lambda':lam,**{k:float(v[i]) for i,k in enumerate(SPAN_FIELDS)},
                  'E_per_lambda':float(v[0]/lam),'Skill_per_lambda':float(v[2]/lam),
                  'classes':[{'class_id':'C'+str(k),'M':float(v[5+3*k]),'P':float(v[6+3*k]),'H':float(v[7+3*k])} for k in range(8)],
                  'H_direction':float(v[7+3*3]),'H_overshoot':float(v[7+3*7]),'H_false_response':float(v[7+3*1]),
                  'h1_paired_differences':None}
                if fi==3:
                    pairs={}
                    for cf in (1,2):
                        z=agg(slots,cf,pi,'span')[j]
                        pairs[FUN[cf]]={k:float(v[i]-z[i]) for i,k in enumerate(('DeltaE','DeltaB','DeltaSkill'))}
                    row['h1_paired_differences']=pairs
                ctx.numcheck(v[2],v[1]-v[0]);ctx.numcheck(v[2],v[3]-v[4]);spans.append(row)
            for k in range(33):
                v=cr[k];qm=float(v[3])
                curves.append({**meta,'k':k,'t':k/32,**{field:float(v[j]) for j,field in enumerate(CURVE_FIELDS)},
                  'u_conditional':float(v[4]/qm) if qm else None,'D_align_conditional':float(v[5]/qm) if qm else None,
                  'reversal_fraction_conditional':float(v[6]/qm) if qm else None,
                  'qualification_status':'DEFINED' if qm else 'SOURCE_ZERO_PROJECTION','population_may_differ_across_functions':True})
            paths.append({**meta,**{field:float(pr[j]) for j,field in enumerate(PATH_FIELDS)},'qualification_mass':mass,
              'sampled_reversal_fraction_numerator':reverse_num,'sampled_reversal_fraction_conditional':float(reverse_num/mass) if mass else None,
              'first_observed_reversal_mass_histogram':hr[:32].tolist(),'not_observed_on_grid_mass':float(hr[32]),'source_zero_mass':float(hr[33]),
              'first_observed_reversal_is_not_continuous_boundary':True})
            for cell in range(9):
                v=jr[cell];M=float(v[0]);cg,rg=divmod(cell,3)
                if M==0:need(bool(np.all(v==0)),'EMPTY_JOINT_NONZERO')
                joints.append({**meta,'class_group_at_lambda1':CLASS_GROUP[cg],'sampled_reversal_group':REV_GROUP[rg],
                  **{field:float(v[j]) for j,field in enumerate(J_FIELDS)},
                  'conditional':{field:float(v[j]/M) if M else None for j,field in enumerate(J_FIELDS) if j},
                  'conditional_status':'DEFINED' if M else 'EMPTY_JOINT_CELL',
                  'raw_link_count':jointcount[(*slots[0],fi,pi)][cell] if israw else None})
                ctx.numcheck(v[5],v[3]-v[4])
            for ji,si in ((1,1),(2,0),(3,3),(4,4),(5,2)):ctx.numcheck(float(jr[:,ji].sum()),sr[5,si])
            ctx.numcheck(float(jr[:,6].sum()),pr[1]);ctx.numcheck(float(jr[:,7].sum()),pr[5])
            ctx.numcheck(float(jr[:3,0].sum()),sr[5,5+3*3]);ctx.numcheck(float(jr[3:6,0].sum()),sr[5,5+3*7])
        for pi in range(2):
            rr=agg(slots,0,pi,'readout');meta=scope_meta(arm,seed,ep,op,0,pi)
            for j,lam in enumerate(LAMBDA):
                readouts.append({**meta,'lambda':lam,'C_readout_r':float(rr[j,0]),'C_readout_A':float(rr[j,1]),'C_readout_m':float(rr[j,2])})
    need((len(spans),len(curves),len(paths),len(joints),len(readouts))==(6912,38016,1152,10368,1728),'FULL_SUMMARY_GRID')
    for name,rows in [('span_summary.jsonl',spans),('curve_summary.jsonl',curves),('path_summary.jsonl',paths),('same_path_joint_summary.jsonl',joints),('readout_cancellation_summary.jsonl',readouts)]:
        ctx.emit(name,jlines(rows))
    ctx.done();return spans,paths,joints


def report(ctx,spans,paths,joints,qual):
    primary=[r for r in spans if r['arm']=='G' and r['seed_layer']=='MEAN5' and r['endpoint_layer']=='KD_KI_EQUAL'
      and r['function_id']=='q' and r['operation_layer'] in OPS]
    out=['# FR40路径与跨度研究 v0.2：单次执行报告','','## Material Passport','',
      '- origin_skill: academic-research-suite / experiment-agent。','- 固定32源／64链接、10case、两参考；MODEL_GATE仅作开发证据。',
      '- 固定33节点与六跨度；无训练、统计推断或科学重试。','- 原实用门禁未裁决，FR50／FR60未释放。','',
      '|参考|操作|lambda|E|B|Skill|E/lambda|Skill/lambda|','|---|---|---:|---:|---:|---:|---:|---:|']
    for r in primary:
        out.append('|'+r['reference_scheme']+'|'+r['operation_layer']+'|'+'|'.join(format(r[k],'.10g') for k in ('lambda','E','B','Skill','E_per_lambda','Skill_per_lambda'))+'|')
    out.extend(['','## 同路径完整跨度失效与采样反号','',
      '|参考|操作|完整跨度组|反号组|原面板质量|Skill贡献|条件Skill|','|---|---|---|---|---:|---:|---:|'])
    jointprimary=[r for r in joints if r['arm']=='G' and r['seed_layer']=='MEAN5' and r['endpoint_layer']=='KD_KI_EQUAL'
      and r['function_id']=='q' and r['operation_layer'] in OPS]
    for r in jointprimary:
        cond=r['conditional']['Skill']
        out.append('|'+r['reference_scheme']+'|'+r['operation_layer']+'|'+r['class_group_at_lambda1']+'|'+r['sampled_reversal_group']+'|'+format(r['M'],'.9g')+'|'+format(r['Skill'],'.9g')+'|'+('null' if cond is None else format(cond,'.9g'))+'|')
    main=[r for r in primary if r['reference_scheme']==POL[0]]
    out.extend(['','## 有限科学结论',''])
    for op in OPS:
        rows=[r for r in main if r['operation_layer']==op];positive=[r['lambda'] for r in rows if r['Skill']>0];negative=[r['lambda'] for r in rows if r['Skill']<0]
        out.append('- G/q主参考 '+op+'：六跨度中观察正Skill的lambda='+str(positive)+'，负Skill的lambda='+str(negative)+'；其余为计算零。完整向量如上，不选择最佳跨度为新主量。')
    out.extend(['','E缩小需结合B和Skill、E/lambda与Skill/lambda解释。相同lambda仅是本路径完整位移比例，不是跨S/G相同物理距离。',
      '九格只反映同一路径共现；未观察到采样反号不证明连续路径无反转。增量抵消、投影面积抵消及两头差值抵消分别报告，不能相加为病因占比。',
      'Q32−Q16与r(1)−Q32始终保留，大小不构成积分认证或AD失败判据。FD使用实际节点几何校正，全部40组chain照常核对。',
      '小面板为新平衡面板与两供体条件分布，不复现完整FR40参考总体；源、供体复用和五seed不构成独立生物重复。',
      '本研究不识别学习率唯一病因、训练机制、化学机制、XAI决策增量或普遍性能—解释性权衡；H1/H2/H3及既有结果不重新裁决。','',
      '## 执行与资格','',json.dumps({'completed_cases':ctx.completed_cases,'charged':dict(ctx.charged),'returned':dict(ctx.counters),
       'FD_status':dict(collections.Counter(r['FD_status'] for r in qual)),'algebra_checks':ctx.checks,'max_algebra_absolute_residual':ctx.max_residual},ensure_ascii=False)])
    ctx.emit('stage_report.md',('\n'.join(out)+'\n').encode(),AUD)
    return primary
def worker():
    state=waitstate();m=json.loads((PKG/'contract.json').read_bytes());ctx=Ctx(m,state);code=1
    NV=SV=SC=ZC=RH=None;np=None;arrays_emitted=False
    try:
        ctx.guard()
        # Loader access begins after the original clock and all process identities are committed.
        import numpy as np,torch,psutil
        versions={'python':sys.version.split()[0],'torch':torch.__version__.split('+')[0],'numpy':np.__version__,'psutil':psutil.__version__}
        need(versions==m['runtime']['versions'],'RUNTIME_VERSION')
        torch.set_num_threads(1);torch.set_num_interop_threads(1);torch.use_deterministic_algorithms(True,warn_only=False)
        need(torch.get_num_threads()==1 and torch.get_num_interop_threads()==1,'RUNTIME_THREAD')
        ctx.emit('runtime_identity_receipt.json',{'versions':versions,'entry':sys.executable,'binaries':m['runtime']['binaries'],'ordinary_loader_IO':'UNKNOWN',
          'CPU_only':True,'float64':True,'batch':1,'eval':True,'parameters_AD':False,'input_AD_rule':'RELU_ZERO0','no_probes':True,
          'runtime_realization_id':state['runtime_realization_id'],'numeric_code_id':state['numeric_code_id']},AUD)
        panel,links=metadata(ctx,np);aff=calibrators(ctx)
        NV=np.full((10,64,3,33,4,2),np.nan,dtype='<f8');SV=np.full((10,64,3,6,4,4),np.nan,dtype='<f8')
        SC=np.full((10,64,3,6,4),255,dtype=np.uint8);ZC=np.full((10,64,3,33,2),65535,dtype='<u2');RH=np.zeros((10,64,3,33,2,32),dtype=np.uint8)
        qual=[];reuse=[];carry=[]
        for case in m['cases']:
            ctx.guard();V=cache(ctx,np,case['cache'],links)
            q,r=native_case(ctx,torch,np,case,V,links,aff,NV,ZC,RH);qual.extend(q);reuse.extend(r)
            carry.append(spans_and_carry(ctx,np,case,links,NV,SV,SC));del V
            ctx.completed_cases.append(case['case_id']);ctx.log('CASE_COMPLETED',case_id=case['case_id'],charged=dict(ctx.charged))
            print('CASE_COMPLETED '+case['case_id']+' forward='+str(ctx.counters['forward'])+' AD='+str(ctx.counters['input_AD']),flush=True)
        need(len(qual)==40 and len(reuse)==20 and len(carry)==10,'QUALIFICATION_COUNTS')
        need(ctx.counters['path_dot']==126720 and ctx.counters['FD_geometry_dot']==40,'DOT_CLOSURE')
        finite(np,NV,'FINAL_NODE_NONFINITE');finite(np,SV,'FINAL_SPAN_NONFINITE');need(bool(np.all(SC<=7)),'CLASS_RANGE')
        for name,arr in [('node_values.npy',NV),('span_values.npy',SV),('span_classes.npy',SC),('node_zero_counts.npy',ZC),('node_region_hashes.npy',RH)]:
            ctx.array(name,arr,np)
        arrays_emitted=True
        ctx.emit('qualification_receipt.json',{'groups':qual,'FD_eligible_count':sum(r['FD_status']=='EMPIRICAL_MATCH' for r in qual),
          'FD_status':'HAS_ELIGIBLE_EMPIRICAL_CHECKS' if any(r['FD_status']=='EMPIRICAL_MATCH' for r in qual) else 'NO_ELIGIBLE_FD_NOT_A_PASS',
          'all40_chain_match':True,'no_extra_forward_AD':True,'geometry_does_not_replace_original_path_projection':True})
        ctx.emit('endpoint_carry_receipt.json',{'cases':carry,'total_checked_scalars':15360})
        ctx.emit('point_reuse_receipt.json',{'heads':reuse,'physical_key':'case,head,endpoint,actualfloat64vector',
          'logical_key':'case,source,donor,op,k,head','signed_zero_hash':'normalized+0; fullnp.array_equal required','no_hash_only_equality':True})
        spans,paths,joints=path_analysis(ctx,np,links,NV,SV,SC);primary=report(ctx,spans,paths,joints,qual)
        for k in ('source_files','I_bytes','path_dot','FD_geometry_dot','function_node_views','span_views','old_endpoint_scalars',
          'head_construct','state_apply','checkpoint_decode','head_parameters','chain_groups','chain_matvec','chain_mask_mult','chain_coord_compare',
          'FD_geometry_coord_subtract','FD_geometry_coord_divide','FD_center_coord_subtract','FD_geometry_maxabs_coordinates','path_function_rows',
          'weighted_path_views','summary_rows','span_summary_rows','curve_summary_rows','joint_summary_rows','joint_unconditional_fields',
          'joint_conditional_fields','lambda_summary_divisions','readout_summary_rows','weighted_span_views'):
            need(ctx.counters[k]==m['budgets'][k],'FINAL_COUNTER_'+k)
        need(len(ctx.used)==43 and len(ctx.completed_cases)==10 and ctx.pending is None,'COMPLETION_CLOSURE')
        ctx.guard();ctx.phase='COMPLETE';ctx.log('COMPLETE',charged=dict(ctx.charged))
        receipt={'status':'COMPLETE_FINITE_PATH_SPAN_STUDY','completed_cases':ctx.completed_cases,'sources':ctx.used,'source_files':43,
          'source_opaque_bytes':3647172856,'I_logical_charged_bytes':dict(ctx.charged).get('I_bytes'),'charged':dict(ctx.charged),'returned':dict(ctx.counters),
          'blocks':ctx.blocks,'unknown_block':ctx.pending,'outputs':ctx.outputs,'primary_G_q':primary,
          'algebra_checks':ctx.checks,'max_checked_absolute_residual':ctx.max_residual,'science_retries':0,'training':0,'statistical_inference':0,
          'encoders_PLM':0,'parameters_AD':0,'physical_IO':'UNKNOWN_NOT_FABRICATED','III_reads':0,
          'all40_chain_match':True,'FD_eligible_count':sum(r['FD_status']=='EMPIRICAL_MATCH' for r in qual),
          'engineering_complete':True,'scientific_result':'FINITE_DESCRIPTIVE_PATH_BEHAVIOR_NOT_CAUSAL_MECHANISM',
          'scientific_generalization_PASS':'NOT_CLAIMED','MODEL_GATE':'EXPOSED_DEVELOPMENT_ONLY',
          'original_practical_gate':'NOT_ADJUDICATED','FR50_release':False,'FR60_release':False}
        ctx.emit('execution_receipt.json',receipt,AUD);code=0
    except BaseException as e:
        why=type(e).__name__+': '+str(e)[:600]
        try:
            if np is not None and NV is not None and ctx.completed_cases and not arrays_emitted:
                n=len(ctx.completed_cases)
                for name,arr in [('partial_node_values.npy',NV[:n]),('partial_span_values.npy',SV[:n]),('partial_span_classes.npy',SC[:n]),('partial_node_zero_counts.npy',ZC[:n]),('partial_node_region_hashes.npy',RH[:n])]:
                    ctx.array(name,arr,np)
            ctx.log('STOPPED',reason=why,completed_cases=ctx.completed_cases,unknown_block=ctx.pending)
            ctx.emit('execution_receipt.json',{'status':'STOPPED_NO_RETRY','reason':why,'completed_cases':ctx.completed_cases,'sources':ctx.used,
              'charged':dict(ctx.charged),'returned':dict(ctx.counters),'unknown_block':ctx.pending,'outputs':ctx.outputs,
              'science_retries':0,'training':0,'statistical_inference':0,'FR50_release':False,'FR60_release':False,
              'failure_trace':traceback.format_exc()[-6000:]},AUD)
        except BaseException:pass
        print('WORKER_STOPPED '+why,flush=True)
    return code
def watchdog():
    state=waitstate()
    try:
        while True:
            v=resource(state);physical(v,state);write(AUD,'resources.jsonl',v,True)
            if (AUD/'execution_receipt.json').exists() or not live(state['identities']['worker']):return 0
            need(live(state['identities']['supervisor']),'WATCHDOG_OWNER_LOST');time.sleep(5)
    except BaseException as e:
        try:write(AUD,'safety_stop.json',{'reason':type(e).__name__+': '+str(e)[:300],'wall':time.time()})
        except BaseException:pass
        if live(state['identities']['worker']):
            try:os.killpg(state['identities']['worker']['PGID'],signal.SIGTERM)
            except ProcessLookupError:pass
        return 2
def supervise():
    start=time.time();mono=time.monotonic()
    need(not AUD.exists() and not OUT.exists(),'ATTEMPT_ALREADY_EXISTS')
    AUD.mkdir(parents=True,exist_ok=False);OUT.mkdir(parents=True,exist_ok=False)
    write(AUD,'attempt_claim.json',{'schema':'sgc-path-single-attempt-v0.2','wall_start':start,'monotonic_start':mono,
      'no_retry':True,'authority':'CURRENT_DIRECT_USER_EXECUTION','FR50_release':False,'FR60_release':False})
    m,seal=bindcheck()
    for r in m['controls']:
        raw=docread(r['path']);need(len(raw)==r['bytes'] and sha(raw)==r['sha256'],'OLD_CONTROL_IDENTITY')
    need(sum(r['bytes'] for r in m['sources'])==3647172856 and len(m['sources'])==43,'SOURCE_MANIFEST_CLOSURE')
    codeid=next(z['sha256'] for z in seal['artifacts'] if z['path']==str(SCRIPT));configid=next(z['sha256'] for z in seal['artifacts'] if z['path']==str(PKG/'contract.json'))
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    w=subprocess.Popen([sys.executable,'-B',str(SCRIPT),'--worker'],cwd=ROOT,env=env,start_new_session=True)
    d=subprocess.Popen([sys.executable,'-B',str(SCRIPT),'--watchdog'],cwd=ROOT,env=env,start_new_session=True)
    state={'wall_start':start,'monotonic_start':mono,'wall_deadline':start+7200,'monotonic_deadline':mono+7200,
      'config_sha':configid,'numeric_code_id':codeid,'writer_control_code_id':codeid,'ledger_version':'PATH_LEDGER_V0_2',
      'runtime_realization_id':sha(enc(m['runtime'])),'interpreter':sys.executable,
      'identities':{'supervisor':ident(os.getpid()),'worker':ident(w.pid),'watchdog':ident(d.pid)}}
    write(AUD,'execution_state.json',state);wr=w.wait()
    try:dr=d.wait(timeout=15)
    except subprocess.TimeoutExpired:d.terminate();dr=d.wait()
    before=allfiles();verified=[];bytes_=0;resource_rows=[];rec={'status':'UNKNOWN_NO_WORKER_RECEIPT'}
    # Package/source code received their one opaque validation at launch; inherit those verified bytes here.
    early={z['path']:z for z in seal['artifacts']}
    for f in before:
        if str(f) in early:
            verified.append(early[str(f)]);bytes_+=early[str(f)]['bytes'];continue
        raw=f.read_bytes();bytes_+=len(raw);need(bytes_<=512*2**20,'II_OUTPUT_VERIFY_LIMIT')
        verified.append({'path':str(f),'bytes':len(raw),'sha256':sha(raw)})
        if f.name=='resources.jsonl':resource_rows=[json.loads(v) for v in raw.splitlines() if v.strip()]
        if f.name=='execution_receipt.json':rec=json.loads(raw)
    for r in rec.get('outputs',[]):
        actual=next((z for z in verified if z['path']==r['path']),None);need(actual is not None and all(actual[k]==r[k] for k in ('bytes','sha256')),'COMMITTED_OUTPUT_IDENTITY')
    success=wr==0 and dr==0 and rec.get('status')=='COMPLETE_FINITE_PATH_SPAN_STUDY'
    physical(resource(state),state)
    sr=write(AUD,'supervision_receipt.json',{'status':'COMPLETE' if success else 'STOPPED','identities':state['identities'],
      'worker_actual_wait_exit':wr,'watchdog_actual_wait_exit':dr,'supervisor_exit':'SEE_TOOL_WAIT','II_one_opaque_validation_bytes':bytes_,
      'II_product_passes_max':1,'III_reads':0,'output_files_before_final_controls':len(before),'unknown_never_exit0':True,
      'resources':{'samples':len(resource_rows),'max_owned_RSS_bytes':max((v['owned_RSS_bytes'] for v in resource_rows),default=None),
      'min_available_bytes':min((v['available_bytes'] for v in resource_rows),default=None),'min_disk_free_bytes':min((v['disk_free_bytes'] for v in resource_rows),default=None),
      'MPS_initialized':False,'MPS_driver_bytes':0},'runtime':m['runtime'],'artifacts_verified':verified,'same_original_clock':True})
    write(AUD,'run_seal.json',{'schema':'sgc-fr40-path-span-run-seal-v0.2','status':rec.get('status'),'self_excluded':True,
      'artifacts':verified+[sr],'worker_actual_wait_exit':wr,'watchdog_actual_wait_exit':dr,'completed_cases':rec.get('completed_cases'),
      'MODEL_GATE':'EXPOSED_DEVELOPMENT_ONLY','original_practical_gate':'NOT_ADJUDICATED','FR50_release':False,'FR60_release':False,
      'science_retries':0,'clock':{k:state[k] for k in ('wall_start','monotonic_start','wall_deadline','monotonic_deadline')},
      'II_byte_budget':512*2**20,'III_byte_budget':512*2**20,'II_plus_III_byte_budget':2**30,
      'no_old_writes':True,'opaque_byte_hash_not_replication_or_WORM':True})
    output_limits()
    print(json.dumps({'status':'COMPLETE' if success else 'STOPPED','worker_exit':wr,'watchdog_exit':dr,'completed_cases':rec.get('completed_cases'),
      'receipt':str(AUD/'execution_receipt.json'),'seal':str(AUD/'run_seal.json')},ensure_ascii=False),flush=True)
    return 0 if success else 1
if __name__=='__main__':
    a=argparse.ArgumentParser();g=a.add_mutually_exclusive_group(required=True);g.add_argument('--supervise',action='store_true');g.add_argument('--worker',action='store_true');g.add_argument('--watchdog',action='store_true')
    args=a.parse_args()
    try:sys.exit(supervise() if args.supervise else worker() if args.worker else watchdog())
    except BaseException as e:
        if isinstance(e,SystemExit):raise
        why=type(e).__name__+': '+str(e)[:600]
        if args.supervise and AUD.exists() and not (AUD/'run_seal.json').exists():
            try:
                r=write(AUD,'control_stop_receipt.json',{'status':'STOPPED_NO_RETRY','reason':why,'traceback':traceback.format_exc()[-6000:],
                  'worker_exit':'UNKNOWN_UNLESS_RECORDED','watchdog_exit':'UNKNOWN_UNLESS_RECORDED','FR50_release':False,'FR60_release':False})
                write(AUD,'run_seal.json',{'status':'STOPPED_NO_RETRY','self_excluded':True,'failure_receipt':r,'FR50_release':False,'FR60_release':False})
            except BaseException:pass
        print('CONTROL_STOP '+why,flush=True);sys.exit(1)

