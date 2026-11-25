"""Frozen FR40 v0.8 numerical implementation; import has no data/model side effects."""
import collections,hashlib,io,json,math,re,zipfile
IDS=('label_group_id','pair_group_id','endpoint')
EPS=('Kd','Ki')
FUNCS=('q','h0','h_aff','h1')
POLICIES=('FIT_ROW_UNIFORM','FIT_PROTEIN_INPUT_UNIFORM')
OPS=('D','P','DP')
class ScientificStop(Exception):pass
def need(v,c):
 if not v:raise ScientificStop(c)
def sha(b):return hashlib.sha256(b).hexdigest()
def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def key(row):return tuple(row[k] for k in IDS)
def ikey(row):return (row['smiles_sha256'],row['target_sequence_key'],row['endpoint'])
def finite(np,x,code):
 need(bool(np.isfinite(x).all()),code)
def close(np,a,b,atol,rtol):
 a=np.asarray(a);b=np.asarray(b);return bool(np.all(np.abs(a-b)<=atol+rtol*np.maximum(np.abs(a),np.abs(b))))
def algebra(np,res,terms):
 finite(np,res,'ALGEBRA_NONFINITE');scale=sum(np.abs(x) for x in terms)
 need(bool(np.all(np.abs(res)<=1e-12+1e-12*scale)),'ALGEBRA_IDENTITY_MISMATCH')
def lower(np,res,terms):
 finite(np,res,'LOWER_NONFINITE');scale=sum(np.abs(x) for x in terms)
 need(bool(np.all(res<=1e-12+1e-12*scale)),'LOWER_BOUND_MISMATCH')
def spans(raw):
 """Locate JSON values lexically without interpreting unselected numeric leaves."""
 text=raw.decode('utf8');dec=json.JSONDecoder();n=len(text)
 def ws(i):
  while i<n and text[i].isspace():i+=1
  return i
 def end(i):
  i=ws(i);need(i<n,'JSON_EOF')
  if text[i]=='"':
   j=i+1
   while j<n:
    if text[j]=='"':return j+1
    j+=2 if text[j]=='\\' else 1
   raise ScientificStop('JSON_STRING_EOF')
  if text[i] in '[{':
   stop=']' if text[i]=='[' else '}';j=ws(i+1)
   while j<n and text[j]!=stop:
    if text[j] in ',:':j=ws(j+1)
    else:j=ws(end(j))
   need(j<n,'JSON_CONTAINER_EOF');return j+1
  m=re.match(r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null',text[i:])
  need(m is not None,'JSON_TOKEN');return i+len(m[0])
 def fields(i):
  i=ws(i);need(text[i]=='{','JSON_OBJECT');out={};i=ws(i+1)
  while text[i]!='}':
   k,j=dec.raw_decode(text,i);need(isinstance(k,str) and k not in out,'JSON_KEY')
   j=ws(j);need(text[j]==':','JSON_COLON');a=ws(j+1);z=end(a);out[k]=(a,z);i=ws(z)
   if text[i]==',':i=ws(i+1)
   else:need(text[i]=='}','JSON_COMMA')
  return out
 def items(i):
  i=ws(i);need(text[i]=='[','JSON_ARRAY');out=[];i=ws(i+1)
  while text[i]!=']':
   z=end(i);out.append((i,z));i=ws(z)
   if text[i]==',':i=ws(i+1)
   else:need(text[i]==']','JSON_ARRAY_COMMA')
  return out
 def val(s):return json.loads(text[s[0]:s[1]])
 need(ws(end(0))==n,'JSON_TRAILING')
 return text,fields,items,val,ws
def projected_object(raw,allowed):
 text,fields,items,val,ws=spans(raw);return {k:val(v) for k,v in fields(0).items() if k in allowed}
def metadata(ctx,np):
 rows={}
 for rec in ctx.payloads[:5]:
  raw=ctx.read_source(rec,'L0');ctx.decode_begin(rec,'L0')
  if rec['id']=='FR00.cache_row_order':
   text,fields,items,val,ws=spans(raw);top=fields(0);roles={}
   rf=fields(top['roles'][0])
   for role in ('MODEL_FIT','MODEL_GATE'):
    roles[role]=[]
    for p in items(rf[role][0]):
     fs=fields(p[0]);roles[role].append({k:val(fs[k]) for k in IDS+('v3_3_role','smiles_sha256','target_sequence_key')})
   rows['roles']=roles
  else:
   result=[]
   for line in raw.splitlines():
    if rec['id'] in ('FR20.reference_weights.jsonl','FR20.corner_map.jsonl'):v=json.loads(line);need(len(v)==6,'METADATA_COLUMNS')
    elif rec['id']=='FR20.needed_corner_alias_manifest.jsonl':
     text,fields,items,val,ws=spans(line);fs=fields(0);v={k:val(fs[k]) for k in ('key_id','smiles_sha256','target_sequence_key','endpoint')};v['aliases']=[]
     for p in items(fs['aliases'][0]):
      ff=fields(p[0]);v['aliases'].append({k:val(ff[k]) for k in ('role','row_index0','record_id')})
    else:v=projected_object(line,rec['allowed_fields'])
    result.append(v)
   rows[rec['id']]=result
  ctx.decode_end(rec)
 panel=rows['FR20.panel_manifest.jsonl'];needed=rows['FR20.needed_corner_alias_manifest.jsonl'];weights=rows['FR20.reference_weights.jsonl'];corners=rows['FR20.corner_map.jsonl'];roles=rows['roles']
 need(len(panel)==2242 and len(needed)==19748 and len(weights)==len(corners)==322659,'FROZEN_COUNTS_CONFLICT')
 need(len(roles['MODEL_FIT'])==46140 and len(roles['MODEL_GATE'])==5697,'ROLE_COUNTS')
 need(collections.Counter(p['endpoint'] for p in panel)=={'Kd':206,'Ki':2036},'SOURCE_ENDPOINT_COUNTS')
 need(all(set(p['case_ids'])=={x['case_id'] for x in ctx.contracts['asset_bindings.json']['cases']} for p in panel),'ALL10_CASE_PANEL_SUPPORT')
 need(panel==sorted(panel,key=lambda r:tuple(x.encode('utf8') for x in key(r))),'PANEL_ORDER')
 source_index={p['source_id']:i for i,p in enumerate(panel)};need(len(source_index)==2242,'SOURCE_ID_DUPLICATE')
 aliases=[];cache_rows=[];ordered_keys=[]
 for i,row in enumerate(needed):
  need(row['key_id']==i,'NEEDED_KEY_ORDER');ordered_keys.append(ikey(row));physical=[]
  for a in row['aliases']:
   role=a['role'];j=a['row_index0'];need(role in roles and 0<=j<len(roles[role]),'ALIAS_ROLE_OR_INDEX')
   rr=roles[role][j];need(key(rr)==tuple(a['record_id']) and ikey(rr)==ikey(row) and rr['v3_3_role']==role,'ALIAS_EXACT_JOIN')
   physical.append(j if role=='MODEL_FIT' else 51265+j)
  need(physical,'NO_ALIAS');aliases.append(physical);cache_rows.append(min(physical))
 need(ordered_keys==sorted(ordered_keys,key=lambda v:tuple(x.encode('utf8') for x in v)) and len(set(ordered_keys))==19748,'INPUT_KEY_CANONICAL')
 # Every alias must be validated; never select a representative to hide a conflict.
 physical_rows=sorted({j for group in aliases for j in group})
 need(len(physical_rows)==19748,'CACHE_ALIAS_DECODE_COUNT_EXCEEDS_FROZEN19748')
 operations=[];by_source=[[] for p in panel]
 for l,(w,c) in enumerate(zip(weights,corners)):
  sid,j,d0,d1,w0,w1=w;need(c[:2]==[sid,j],'REFERENCE_CORNER_ORDER')
  need(sid in source_index and isinstance(j,int) and 0<=j<46140,'REFERENCE_IDENTITY')
  source=panel[source_index[sid]];keys=c[2:];need(all(isinstance(k,int) and 0<=k<19748 for k in keys),'CORNER_KEY_ID')
  A,B,C,D=[ordered_keys[k] for k in keys];sr=roles['MODEL_GATE'][sid];dr=roles['MODEL_FIT'][j]
  need(key(sr)==key(source) and A==ikey(source) and D==ikey(dr),'SOURCE_DONOR_JOIN')
  need(B==(A[0],D[1],A[2]) and C==(D[0],A[1],A[2]) and all(k[2]==source['endpoint'] for k in (A,B,C,D)),'FOUR_CORNER_SUPPORT')
  need(A[0]!=D[0] and A[1]!=D[1],'BRANCH_IDENTITY_NONTRIVIAL')
  need(d0>0 and d1>0 and w0==1/d0 and w1==1/d1,'WEIGHT_DENOMINATOR')
  op=[sid,j,EPS.index(source['endpoint']),*keys,*[cache_rows[k] for k in keys],d0,d1,1];operations.append(op);by_source[source_index[sid]].append(l)
 for i,p in enumerate(panel):
  L=by_source[i];need(len(L)==p['n_reference_rows'] and L,'SOURCE_SUPPORT_COUNT')
  donors=[roles['MODEL_FIT'][operations[l][1]] for l in L];proteins=collections.Counter(r['target_sequence_key'] for r in donors);K=len(proteins)
  need(K==p['n_donor_proteins'],'PROTEIN_REFERENCE_COUNT')
  need([operations[l][1] for l in L]==sorted(operations[l][1] for l in L),'FIT_REFERENCE_ORDER')
  for l,d in zip(L,donors):need(operations[l][11]==len(L) and operations[l][12]==K*proteins[d['target_sequence_key']],'EXACT_REFERENCE_WEIGHTS')
 for role in roles:
  for r in roles[role]:need(r['v3_3_role']==role,'ROLE_CONFLICT')
 need(collections.Counter(EPS[o[2]] for o in operations)=={'Kd':8150,'Ki':314509},'REFERENCE_ENDPOINT_COUNTS')
 mapping={'source_counts':dict(collections.Counter(p['endpoint'] for p in panel)),'link_counts':dict(collections.Counter(EPS[o[2]] for o in operations)),'panel':panel,'needed':needed,'aliases':aliases,'cache_rows':cache_rows,'physical_rows':physical_rows,'keys':ordered_keys,'ops':np.array(operations,dtype=np.int64),'by_source':by_source,'roles':roles}
 ctx.mapping_ready(mapping);return mapping
def cache_load(ctx,np,rec,M):
 raw=ctx.read_source(rec,'L1');ctx.decode_begin(rec,'L1')
 f=io.BytesIO(raw);need(np.lib.format.read_magic(f)==(1,0),'CACHE_NPY_VERSION')
 shape,fortran,dtype=np.lib.format.read_array_header_1_0(f);offset=f.tell()
 need(shape==(56962,1024) and not fortran and dtype==np.dtype('float32') and offset+56962*1024*4==len(raw),'CACHE_SCHEMA')
 V=np.empty((19748,1024),dtype=np.float32);values={}
 for j in M['physical_rows']:
  row=np.frombuffer(raw,dtype='<f4',count=1024,offset=offset+j*4096);finite(np,row,'CACHE_NONFINITE');values[j]=row
 for k,group in enumerate(M['aliases']):
  row=values[group[0]]
  for j in group:need(bool(np.array_equal(row,values[j])),'CACHE_ALIAS_NOT_IDENTIFIABLE')
  V[k]=row
 ctx.decode_end(rec);return V
def composability(ctx,np,V,M,case):
 O=M['ops']
 for i,p in enumerate(M['panel']):
  L=M['by_source'][i];c=O[L,3:7];ctx.work('SOURCE_QUAL_WORK',case,p['endpoint'],i,len(M['panel']),{'links':len(L)})
  a,b,c_,d=[V[c[:,j]] for j in range(4)]
  for x,y,lo,hi in [(a,b,0,256),(a,c_,256,1024),(c_,d,0,256),(b,d,256,1024)]:
   need(bool(np.array_equal(x[:,lo:hi],y[:,lo:hi])),'SUPPORT_NOT_EVALUABLE_CACHE_BLOCK:'+case+':'+str(p['source_id']))
  ctx.work_done()
def calibrators(ctx,np,rec):
 raw=ctx.read_source(rec,'L1');ctx.decode_begin(rec,'L1');text,fields,items,val,ws=spans(raw);root=fields(0);out={}
 for slot in items(root['slots'][0]):
  f=fields(slot[0]);family=val(f['family'])
  if family!='affine':continue
  row={k:val(f[k]) for k in ('case_id','endpoint','family','mode','coefficients','slot_sha256')}
  need(len(row['coefficients'])==2 and isinstance(row['slot_sha256'],str),'AFFINE_SCHEMA')
  a,b=map(float,row['coefficients']);need(math.isfinite(a) and a>0 and math.isfinite(b),'AFFINE_COEFFICIENT')
  k=(row['case_id'],row['endpoint']);need(k not in out,'AFFINE_DUPLICATE');out[k]=(a,b)
 need(len(out)==20,'AFFINE20');ctx.decode_end(rec);return out
def checkpoint(ctx,torch,np,rec,case,higher,schema):
 raw=ctx.read_source(rec,'L1');ctx.decode_begin(rec,'L1');shapes=schema['head_state_shapes'] if higher else schema['state_shapes'][case['arm']]
 cap=schema['per_h1'] if higher else schema['per_original'][case['arm']]
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  names=z.infolist();need(len(names)<=cap['zip_entries_max'] and all(not x.flag_bits&1 and x.compress_type==0 and '..' not in x.filename.split('/') for x in names),'CHECKPOINT_ZIP_LAYOUT')
  storage=[x.file_size for x in names if re.fullmatch(r'[^/]+/data/[0-9]+',x.filename)]
  sizes=[math.prod(v)*4 for v in shapes.values()]
  need(sorted(storage)==sorted(sizes) and sum(storage)==cap['storage_bytes'],'STORAGE_BYTE_MULTISET')
  others=[x for x in names if not re.fullmatch(r'[^/]+/data/[0-9]+',x.filename)]
  need(len(others)<=cap['nonstorage_entries_max'] and sum(x.file_size for x in others)<=cap['nonstorage_payload_bytes_max'],'CHECKPOINT_METADATA_BUDGET')
  need(all('/'.join(x.filename.split('/')[1:]) in schema['zip_metadata_names'] for x in others),'CHECKPOINT_METADATA_NAMES')
 obj=torch.load(io.BytesIO(raw),weights_only=True,map_location='cpu',mmap=False)
 roots=schema['roots'][case['arm']+'_h1'] if higher else schema['roots']['original']
 need(set(obj)==set(roots) and obj['arm']==case['arm'] and obj['seed']==case['seed'],'CHECKPOINT_ROOT_IDENTITY')
 need(isinstance(obj['epoch'],int) and 1<=obj['epoch']<=150,'CHECKPOINT_EPOCH')
 if higher:
  need(obj['LR']==.0006 and obj['cache_sha256']==case['cache']['sha256'] and obj[roots[-1]] is True,'HIGH_LR_HEAD_IDENTITY')
 else:need(obj['epoch']==case['h0_original_epoch'],'ORIGINAL_EPOCH')
 state=obj['state_dict'];need(set(state)==set(shapes),'STATE_KEYS');ptrs=set()
 for k,shape in shapes.items():
  t=state[k];need(isinstance(t,torch.Tensor) and list(t.shape)==shape and t.dtype==torch.float32 and t.storage_offset()==0 and t.is_contiguous() and t.untyped_storage().nbytes()==t.numel()*4,'TENSOR_METADATA')
  ptr=t.untyped_storage().data_ptr();need(ptr not in ptrs,'STORAGE_ALIAS');ptrs.add(ptr)
 head={k:state[k if higher else 'fusion.'+k] for k in schema['head_state_shapes']}
 for t in head.values():need(bool(torch.isfinite(t).all()),'HEAD_PARAMETER_NONFINITE')
 ctx.decode_end(rec);return head
def scores(ctx,np,rec,M):
 raw=ctx.read_source(rec,'L1');ctx.decode_begin(rec,'L1');out={}
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  for member,info in rec['allowed_fields']['decode_members'].items():
   f=io.BytesIO(z.read(member));need(np.lib.format.read_magic(f)==(1,0),'SCORE_NPY_VERSION');shape,fort,dtype=np.lib.format.read_array_header_1_0(f)
   need(list(shape)==info['shape'] and not fort and dtype==np.dtype('float32'),'SCORE_SCHEMA');v=np.frombuffer(f.getbuffer(),dtype='<f4',count=math.prod(shape),offset=f.tell());finite(np,v,'SCORE_NONFINITE')
   out['MODEL_FIT' if 'FIT' in member else 'MODEL_GATE']=v
 result=np.empty(19748,dtype=np.float32)
 for i,row in enumerate(M['needed']):
  vals=[out[a['role']][a['row_index0']] for a in row['aliases']];need(all(v==vals[0] for v in vals),'SAVED_SCORE_ALIAS_CONFLICT');result[i]=vals[0]
 ctx.decode_end(rec);return result
def make_head(ctx,torch,state,dtype,case,head):
 class Fusion(torch.nn.Module):
  def __init__(self):
   super().__init__();self.first=torch.nn.Linear(1024,256,dtype=dtype);self.second=torch.nn.Linear(256,128,dtype=dtype);self.Kd=torch.nn.Linear(128,1,dtype=dtype);self.Ki=torch.nn.Linear(128,1,dtype=dtype)
  def forward(self,x,e,projection=False):
   p1=self.first(x);p2=self.second(torch.relu(p1));v=torch.relu(p2);both=torch.cat((self.Kd(v),self.Ki(v)),dim=-1)
   route=torch.nn.functional.one_hot(torch.tensor([e],dtype=torch.int64),num_classes=2).to(both.dtype);y=(both*route).sum(dim=-1)
   return (y,p1,p2) if projection else y
 ctx.purpose('HEAD_CONSTRUCT',case,head,str(dtype));obj=Fusion();ctx.purpose_done()
 ctx.purpose('STATE_APPLY',case,head,str(dtype));obj.load_state_dict(state,strict=True);obj.eval()
 for p in obj.parameters():p.requires_grad_(False)
 ctx.purpose_done();return obj
def regions(np,p1,p2):
 a=np.concatenate((p1.detach().numpy().ravel(),p2.detach().numpy().ravel()));finite(np,a,'REGION_NONFINITE');sg=np.sign(a).astype(np.int8);code=(sg+1).astype(np.uint8);packed=((code[0::4]<<6)|(code[1::4]<<4)|(code[2::4]<<2)|code[3::4]).tobytes()
 return sg,{'minimum_abs_preactivation':float(np.min(np.abs(a))),'zero_unit_count':int(np.sum(a==0)),'computed_region_sha256':sha(packed)}

def native_phase(ctx,torch,np,M,cases,Vbank):
 source_keys={int(M['ops'][L[0],3]) for L in M['by_source']}
 first={ep:next(i for i,p in enumerate(M['panel']) if p['endpoint']==ep) for ep in EPS}
 first_keys={int(M['ops'][M['by_source'][i][0],3]) for i in first.values()}
 bank={};qualification_points=[];fd_rows=[];chain_rows=[];AD_receipts=[]
 ctx.qualification_state=(qualification_points,fd_rows,chain_rows)
 for case in cases:
  cid=case['case_id'];V=Vbank[cid];ctx.progress('L1_HEAD_REPLAY',case_id=cid)
  desc=ctx.case_descriptors(cid)
  states={h:checkpoint(ctx,torch,np,desc[h+'_checkpoint' if h=='h0' else h+'_head'],case,h=='h1',ctx.contracts['checkpoint_schema.json']) for h in ('h0','h1')}
  entry={'heads':{},'heads32':{},'values':{},'graphs':{},'gradients':{},'qualification_masks':{}}
  for h in ('h0','h1'):
   f32=make_head(ctx,torch,states[h],torch.float32,cid,h);f64=make_head(ctx,torch,states[h],torch.float64,cid,h);entry['heads'][h]=f64;entry['heads32'][h]=f32
   saved=scores(ctx,np,desc[h+'_parity_scores'],M);values=np.empty(19748,dtype=np.float64)
   entry['graphs'][h]={};entry['gradients'][h]={}
   for k,row in enumerate(M['needed']):
    ep=EPS.index(row['endpoint']);x64=torch.tensor(V[k:k+1],dtype=torch.float64,requires_grad=k in source_keys);project=k in first_keys
    if project:ctx.fd_classify_early(cid,h,row['endpoint'])
    ctx.model_begin(cid,h,'float64',row['endpoint'],k,19748,False)
    returned=f64(x64,ep,project);y,p1,p2=returned if project else (returned,None,None);v64=float(y.detach().item());need(math.isfinite(v64),'MAIN_FORWARD_NONFINITE');values[k]=v64;ctx.model_done()
    if k in source_keys:entry['graphs'][h][k]=(x64,y)
    if project:
     sg,r=regions(np,p1,p2);entry['qualification_masks'][(h,k)]=(sg,(p1.detach()>0,p2.detach()>0));qualification_points.append({'case_id':cid,'head':h,'endpoint':row['endpoint'],'point_id':'SOURCE_'+str(k),'output':v64,**r})
    x32=torch.tensor(V[k:k+1],dtype=torch.float32)
    ctx.model_begin(cid,h,'float32',row['endpoint'],k,19748,False)
    with torch.no_grad():v32=float(f32(x32,ep).item())
    need(math.isfinite(v32),'PARITY_FORWARD_NONFINITE');ctx.model_done()
    need(close(np,v32,float(saved[k]),1e-5,1e-5) and close(np,v64,v32,1e-5,1e-5) and close(np,v64,float(saved[k]),1e-5,1e-5),'NECESSARY_REPLAY_MISMATCH')
   entry['values'][h]=values
  bank[cid]=entry
  for h in ('h0','h1'):
   model=entry['heads'][h]
   for endpoint in EPS:
    i=first[endpoint];p=M['panel'][i];l=M['by_source'][i][0];ids=M['ops'][l,3:7];k=int(ids[0]);x,y=entry['graphs'][h][k]
    ctx.ad_begin(cid,h,endpoint,i,True)
    grad=torch.autograd.grad(y,x,retain_graph=True,create_graph=False)[0].detach();finite(np,grad.numpy(),'QUAL_AD_NONFINITE');entry['gradients'][h][i]=grad.numpy().ravel().copy();ctx.ad_done()
    ctx.chain_begin(cid,h,endpoint)
    sg,(m1,m2)=entry['qualification_masks'][(h,k)];we=getattr(model,endpoint).weight.detach().reshape(-1)
    z=model.second.weight.detach().T@(m2.reshape(-1).to(torch.float64)*we);g=model.first.weight.detach().T@(m1.reshape(-1).to(torch.float64)*z)
    gn=g.numpy();ga=grad.numpy().ravel();finite(np,gn,'CHAIN_NONFINITE');allow=1e-6+1e-5*np.maximum(np.abs(gn),np.abs(ga));res=gn-ga
    chain_rows.append({'case_id':cid,'native_head':h,'endpoint':endpoint,'compared_coordinates':1024,'failed_coordinates':int(np.sum(np.abs(res)>allow)),'finite_coordinates':int(np.isfinite(gn).sum()),'max_abs_residual':float(np.max(np.abs(res))),'max_tolerance_normalized_residual':float(np.max(np.abs(res)/allow)),'atol':1e-6,'rtol':1e-5,'AD_rule_id':'AD_F64_RELU_ZERO0_ALL_SOURCES'})
    need(close(np,gn,ga,1e-6,1e-5),'CHAIN_AD_MISMATCH');ctx.chain_done()
    source=V[k].astype(np.float64)
    for oi,op in enumerate(OPS):
     target=V[int(ids[(2,1,3)[oi]])].astype(np.float64);delta=target-source
     ctx.fd_classify_begin(cid,h,endpoint,oi)
     row={'case_id':cid,'native_head':h,'endpoint':endpoint,'operation':op,'source_id':p['source_id'],'donor_FIT_index0':int(M['ops'][l,1]),'eta':2**-10,'delta_norm':None,'f_source':float(entry['values'][h][k]),'f_plus':None,'f_minus':None,'FD':None,'AD':None,'signed_residual':None,'allowed_error':None,'whole_vector_distinct':[None,None,None],'executed_perturbed_points':0,'execution_state':'COMPLETED'}
     if bool(np.all(delta==0)):
      row.update(classification='TRIVIAL_ZERO_DISPLACEMENT',delta_norm=0.);ctx.fd_skip(cid,h,endpoint,oi)
     else:
      norm=float(np.linalg.norm(delta));need(math.isfinite(norm) and norm>0,'FD_INVALID_NORM');direction=delta/norm;plus=source+2**-10*direction;minus=source-2**-10*direction;finite(np,direction,'FD_DIRECTION_NONFINITE');finite(np,plus,'FD_PLUS_NONFINITE');finite(np,minus,'FD_MINUS_NONFINITE');booleans=[bool(np.any(plus!=source)),bool(np.any(minus!=source)),bool(np.any(plus!=minus))];row.update(delta_norm=norm,whole_vector_distinct=booleans)
      if not all(booleans):row['classification']='UNRESOLVED_UNREPRESENTABLE_STEP';ctx.fd_skip(cid,h,endpoint,oi)
      else:
       outputs=[];signs=[]
       for sign,point in enumerate((plus,minus)):
        ctx.fd_forward_begin(cid,h,endpoint,oi*2+sign)
        with torch.no_grad():out,a,b=model(torch.tensor(point.reshape(1,1024),dtype=torch.float64),EPS.index(endpoint),True)
        value=float(out.item());need(math.isfinite(value),'FD_FORWARD_NONFINITE');ctx.fd_forward_done();rg,pr=regions(np,a,b);outputs.append(value);signs.append(rg);qualification_points.append({'case_id':cid,'head':h,'endpoint':endpoint,'point_id':op+('PLUS' if sign==0 else 'MINUS'),'output':value,**pr})
       fd=(outputs[0]-outputs[1])/(2*2**-10);ad=float(np.dot(ga,direction));need(math.isfinite(fd) and math.isfinite(ad),'FD_AD_NONFINITE')
       residual=fd-ad;allowance=1e-6+1e-5*max(abs(fd),abs(ad));eligible=not bool(np.any(sg==0)) and not bool(np.any(signs[0]==0)) and not bool(np.any(signs[1]==0)) and bool(np.array_equal(sg,signs[0])) and bool(np.array_equal(sg,signs[1]))
       status=('EMPIRICAL_SMOOTH_MATCH' if abs(residual)<=allowance else 'EMPIRICAL_SMOOTH_MISMATCH') if eligible else 'UNRESOLVED_KINK_OR_CROSS_REGION'
       row.update(f_plus=outputs[0],f_minus=outputs[1],FD=fd,AD=ad,signed_residual=residual,allowed_error=allowance,executed_perturbed_points=2,classification=status)
       fd_rows.append(row);ctx.fd_classify_done();need(status!='EMPIRICAL_SMOOTH_MISMATCH','EMPIRICAL_SMOOTH_FD_MISMATCH');continue
     fd_rows.append(row);ctx.fd_classify_done()
  ctx.partial_qualification(qualification_points,fd_rows,chain_rows)
 need(len(fd_rows)==120 and len(chain_rows)==40,'QUALIFICATION_GROUP_COUNTS')
 return bank,first,qualification_points,fd_rows,chain_rows
def math_controls(ctx,torch,np):
 points=[(1.,1.),(1.,-1.),(-1.,1.),(-1.,-1.)];rows=[]
 def fun(i,x):
  d,p=x[0],x[1]
  if i==0:
   g=torch.relu(d)+torch.relu(p);return g-g
  if i==1:return x.sum()*0+1
  if i==2:return (2-1)*(torch.relu(d)+torch.relu(p))+1
  if i==3:return d
  if i==4:return p
  if i==5:return d+p
  if i==6:return torch.relu(d)+torch.relu(p)
  return d*p
 for i in range(8):
  values=[];input_source=None;source_y=None
  for j,p in enumerate(points):
   ctx.math_forward_begin(i,j);x=torch.tensor(p,dtype=torch.float64,requires_grad=j==0);y=fun(i,x);v=float(y.detach().item());need(math.isfinite(v),'MATH_NONFINITE');values.append(v);ctx.math_forward_done()
   if j==0:input_source=x;source_y=y
  ctx.math_ad_begin(i);g=torch.autograd.grad(source_y,input_source)[0].detach().numpy();ctx.math_ad_done();finite(np,g,'MATH_AD_NONFINITE')
  a=np.array([-2*g[0],-2*g[1],-2*g[0]-2*g[1]],dtype=np.float64);r=np.array([values[2]-values[0],values[1]-values[0],values[3]-values[0]]);eps=r-a;I=values[3]-values[2]-values[1]+values[0];dp=eps[2]-eps[0]-eps[1]-I;lb=abs(I)-float(np.abs(eps).sum());algebra(np,dp,[eps[2],eps[0],eps[1],I]);lower(np,lb,[I,*eps])
  for j,op in enumerate(OPS):rows.append({'function_id':i,'operation':op,'A':float(a[j]),'r':float(r[j]),'epsilon':float(eps[j]),'I_src':I,'signed_DP_identity_residual':float(dp),'signed_lower_bound_residual':float(lb)})
 ctx.math_receipt(rows);return rows
def ratio(E,B):
 need(math.isfinite(E) and E>=0 and math.isfinite(B) and B>=0,'R_REQUIRED_ABSOLUTE_INVALID')
 if B<=1e-6:return None,'UNDEFINED_LOW_RESPONSE'
 try:r=E/B
 except OverflowError:r=math.inf
 return (r,'DEFINED_COMPUTED_EMPIRICAL') if math.isfinite(r) else (None,'UNDEFINED_RATIO_NONFINITE')
def permutation(arm,seed,endpoint,branch,n):
 ns='SGC-FR40-PERM-v1'
 return sorted(range(n),key=lambda k:(hashlib.sha256(f'{ns}|{arm}|{seed}|{endpoint}|{branch}|{k}'.encode()).digest(),k))
def analyze_case(ctx,torch,np,M,case,V,entry,affine,first):
 cid=case['case_id'];ops=M['ops'];native=entry['values'];n=19748;values=np.empty((n,4),dtype=np.float64)
 for k,record in enumerate(M['needed']):
  ctx.vertex_begin(cid,record['endpoint'],k,n);a,b=affine[(cid,record['endpoint'])];h0=float(native['h0'][k]);h1=float(native['h1'][k]);ha=a*h0+b;values[k]=[h1-ha,h0,ha,h1];finite(np,values[k],'DERIVED_VERTEX_NONFINITE');ctx.vertex_done()
 out=ctx.array('cases/'+cid+'/operation_values.npy',(322659,4,12),'<f8')
 permout=ctx.array('cases/'+cid+'/permutation_approximations.npy',(322659,4,3),'<f8')
 lowout=ctx.array('cases/'+cid+'/source_lower_bounds.npy',(2242,4,2,5),'<f8')
 per_source={};rawcollect=collections.defaultdict(list);spancollect=collections.defaultdict(list);adcounts=collections.Counter()
 for i,p in enumerate(M['panel']):
  ctx.guard();endpoint=p['endpoint'];L=M['by_source'][i];idx=ops[L,3:7];Akey=int(idx[0,0]);N=len(L)
  grads={}
  for h in ('h0','h1'):
   if i in entry['gradients'][h]:g=entry['gradients'][h][i]
   else:
    x,y=entry['graphs'][h][Akey];ctx.ad_begin(cid,h,endpoint,i,False)
    g=torch.autograd.grad(y,x,retain_graph=True,create_graph=False)[0].detach().numpy().ravel().copy();finite(np,g,'SOURCE_AD_NONFINITE');ctx.ad_done()
   grads[h]=g;adcounts[(h,endpoint)]+=1
  ctx.source_work_begin(cid,endpoint,i,len(M['panel']),N)
  a,b=affine[(cid,endpoint)];g0=grads['h0'];g1=grads['h1'];ga=a*g0;gq=g1-ga;G=np.stack((gq,g0,ga,g1));finite(np,G,'DERIVED_GRADIENT_NONFINITE')
  gp=G.copy();pd=permutation(case['arm'],case['seed'],endpoint,'D',256);pp=permutation(case['arm'],case['seed'],endpoint,'P',768)
  # Native gradients share one permutation; derived gradients follow same linear definitions.
  gp[:,0:256]=G[:,pd];gp[:,256:]=G[:,np.array(pp)+256]
  DD=V[idx[:,2]].astype(np.float64)[:,:256]-V[Akey,:256].astype(np.float64)
  DP=V[idx[:,1]].astype(np.float64)[:,256:]-V[Akey,256:].astype(np.float64)
  d=np.sqrt(np.sum(DD*DD,axis=1));p_norm=np.sqrt(np.sum(DP*DP,axis=1));joint=np.sqrt(d*d+p_norm*p_norm);finite(np,np.column_stack((d,p_norm,joint)),'SPAN_NONFINITE')
  zD=np.all(DD==0,axis=1);zP=np.all(DP==0,axis=1);norms=(d,p_norm,joint);zeros=(zD,zP,zD&zP)
  for fi in range(4):
   Dapprox=DD@G[fi,:256];Papprox=DP@G[fi,256:];approx=np.column_stack((Dapprox,Papprox,Dapprox+Papprox))
   Dperm=DD@gp[fi,:256];Pperm=DP@gp[fi,256:];perm=np.column_stack((Dperm,Pperm,Dperm+Pperm))
   v=values[idx,fi];response=np.column_stack((v[:,2]-v[:,0],v[:,1]-v[:,0],v[:,3]-v[:,0]));eps=response-approx;I=v[:,3]-v[:,2]-v[:,1]+v[:,0];identity=eps[:,2]-eps[:,0]-eps[:,1]-I;lowerres=np.abs(I)-np.abs(eps).sum(axis=1)
   finite(np,np.column_stack((approx,response,eps,perm,I,identity,lowerres)),'RESPONSE_NONFINITE')
   algebra(np,identity,[eps[:,2],eps[:,0],eps[:,1],I]);lower(np,lowerres,[I,eps[:,0],eps[:,1],eps[:,2]])
   out[L,fi,:]=np.column_stack((approx,response,eps,I,identity,lowerres));permout[L,fi,:]=perm
   for pi,pol in enumerate(POLICIES):
    weights=1/ops[L,11+pi].astype(np.float64)
    mean=lambda z:math.fsum(float(w)*float(x) for w,x in zip(weights,z))
    U=mean(np.abs(I));E=[mean(np.abs(eps[:,oi])) for oi in range(3)];source_res=U-sum(E);lower(np,source_res,[U,*E]);lowout[i,fi,pi]=[U,*E,source_res]
    per_source[(i,fi,pi)]=[U,*E,source_res]
    for oi,op in enumerate(OPS):
     r=response[:,oi];A=approx[:,oi];er=eps[:,oi];epm=r-perm[:,oi];Ee=E[oi];B=mean(np.abs(r));Eperm=mean(np.abs(epm));R,Rs=None,'NOT_A_RAW_SLOT';Rp,Rps=None,'NOT_A_RAW_SLOT'
     stat={'E':Ee,'B':B,'Skill':B-Ee,'R':R,'mean_r':mean(r),'mean_A':mean(A),'mean_epsilon':mean(er),'mean_abs_A':mean(np.abs(A)),'r_min':float(r.min()),'r_max':float(r.max()),'A_min':float(A.min()),'A_max':float(A.max()),'epsilon_min':float(er.min()),'epsilon_max':float(er.max()),'E_perm':Eperm,'Skill_perm':B-Eperm,'R_perm':Rp,'R_status':Rs,'R_perm_status':Rps}
     for tag,z in [('r',r),('A',A),('epsilon',er)]:
      for name,mask in [('positive',z>0),('negative',z<0),('zero',z==0)]:stat[tag+'_'+name+'_fraction']=mean(mask)
     rawcollect[(endpoint,oi,fi,pi)].append(stat)
     if fi==0:
      nz=norms[oi];spancollect[(endpoint,oi,pi)].append({'mean_drug_norm':mean(d) if oi!=1 else 0.,'mean_protein_norm':mean(p_norm) if oi!=0 else 0.,'mean_joint_norm':mean(nz),'zero_displacement_fraction':mean(zeros[oi]),'min_drug_norm':float(d.min()) if oi!=1 else 0.,'max_drug_norm':float(d.max()) if oi!=1 else 0.,'min_protein_norm':float(p_norm.min()) if oi!=0 else 0.,'max_protein_norm':float(p_norm.max()) if oi!=0 else 0.,'min_joint_norm':float(nz.min()),'max_joint_norm':float(nz.max())})
  ctx.source_work_done()
  if i%64==0:ctx.progress('L2',case_id=cid,source_ordinal=i,sources=2242)
 def avg(rows,fields,raw=True):
  out={}
  for k in fields:
   if k in ('R','R_perm'):continue
   mn=k.endswith('_min') or k.startswith('min_');mx=k.endswith('_max') or k.startswith('max_')
   out[k]=(min(r[k] for r in rows) if mn else max(r[k] for r in rows)) if raw and (mn or mx) else math.fsum(r[k] for r in rows)/len(rows)
  return out
 fields=ctx.contracts['output_schema.json']['schemas']['summary']['float_fields'];raw={}
 for slot,rows in rawcollect.items():
  r=avg(rows,fields);r['R'],r['R_status']=ratio(r['E'],r['B']);r['R_perm'],r['R_perm_status']=ratio(r['E_perm'],r['B']);raw[slot]=r
 span={slot:avg(rows,list(rows[0])) for slot,rows in spancollect.items()}
 lower_raw={}
 for endpoint in EPS:
  inds=[i for i,p in enumerate(M['panel']) if p['endpoint']==endpoint]
  for fi in range(4):
   for pi in range(2):lower_raw[(endpoint,fi,pi)]=np.mean(np.array([per_source[(i,fi,pi)] for i in inds]),axis=0).tolist()
 AD_receipts=[{'case_id':cid,'native_head':h,'endpoint':ep,'expected_source_count':sum(p['endpoint']==ep for p in M['panel']),'actual_source_count':adcounts[(h,ep)],'input_backward_calls':adcounts[(h,ep)],'gradient_coordinates_checked':adcounts[(h,ep)]*1024,'AD_rule_id':'AD_F64_RELU_ZERO0_ALL_SOURCES','full_source_activation_scan':False} for h in ('h0','h1') for ep in EPS]
 ctx.commit_array(out);ctx.commit_array(permout);ctx.commit_array(lowout);ctx.ad_group_receipt(AD_receipts)
 return raw,span,lower_raw

def summarize(ctx,np,M,cases,rawbank,spanbank,lowerbank):
 ctx.final_begin();summary=[];paired=[];sensitive=[];lowerrows=[];spanrows=[];affine=[]
 fields=ctx.contracts['output_schema.json']['schemas']['summary']['float_fields'];raw_by={c['case_id']:c for c in cases}
 def subset(arm,seed,ep,op):
  cs=[c for c in cases if c['arm']==arm and (seed=='MEAN5' or str(c['seed'])==seed)];ee=EPS if ep=='KD_KI_EQUAL' else (ep,);oo=range(3) if op=='ALL3_EQUAL' else (OPS.index(op),)
  return [(c,e,o) for c in cs for e in ee for o in oo]
 def average_raw(rows,names,aggregate):
  result={}
  for k in names:
   if k in ('R','R_perm'):
    vals=[r[k] for r in rows];result[k]=None if any(v is None for v in vals) else math.fsum(vals)/len(vals)
   elif aggregate and (k.endswith('_min') or k.endswith('_max') or k.startswith('min_') or k.startswith('max_')):result[k]=None
   else:result[k]=math.fsum(r[k] for r in rows)/len(rows)
  return result
 def scope(arm,seed,ep,op,fi,pi):return {'arm':arm,'seed_layer':seed,'endpoint_layer':ep,'operation_layer':op,'function_id':FUNCS[fi],'policy_id':POLICIES[pi]}
 lookup={}
 for arm in ('S','G'):
  for seed in ('17','29','43','71','101','MEAN5'):
   for ep in ('Kd','Ki','KD_KI_EQUAL'):
    for op in (*OPS,'ALL3_EQUAL'):
     slots=subset(arm,seed,ep,op);aggregate=seed=='MEAN5' or ep=='KD_KI_EQUAL' or op=='ALL3_EQUAL'
     for fi in range(4):
      for pi in range(2):
       rows=[rawbank[c['case_id']][(e,o,fi,pi)] for c,e,o in slots];r=average_raw(rows,fields,aggregate);meta=scope(arm,seed,ep,op,fi,pi)
       for ratio_field in ('R','R_perm'):
        missing=[r0[ratio_field+'_status'] for r0 in rows if r0[ratio_field] is None];r[ratio_field+'_status']='AGGREGATE_INCOMPLETE_RAW_R' if aggregate and missing else (missing[0] if missing else 'DEFINED_COMPUTED_EMPIRICAL')
        r[ratio_field+'_expected_raw_slots']=len(rows);r[ratio_field+'_defined_raw_slots']=len(rows)-len(missing);r[ratio_field+'_missing_reasons']=dict(collections.Counter(missing))
       endpoints=EPS if ep=='KD_KI_EQUAL' else (ep,);r.update(source_denominator=sum(M['source_counts'][e] for e in endpoints),link_count=sum(M['link_counts'][e] for e in endpoints),numeric_target_id='FROZEN_F64_SAME_IMPLEMENTATION',AD_rule_id='AD_F64_RELU_ZERO0_ALL_SOURCES',exact_sign_fraction_convention='computed_float64_positive_negative_zero',numerical_realization_id=ctx.runtime_id,evidence_tier='FINITE_EMPIRICAL_DEVELOPMENT_ONLY')
       row={**meta,**r};summary.append(row);lookup[(arm,seed,ep,op,fi,pi)]=row
     for pi in range(2):
      names=list(next(iter(spanbank[slots[0][0]['case_id']].values())))
      rows=[spanbank[c['case_id']][(e,o,pi)] for c,e,o in slots]
      spanrows.append({k:v for k,v in scope(arm,seed,ep,op,0,pi).items() if k!='function_id'}|average_raw(rows,names,aggregate))
    for fi in range(4):
     for pi in range(2):
      slots=subset(arm,seed,ep,'D');vals=[lowerbank[c['case_id']][(e,fi,pi)] for c,e,_ in slots];mean=[math.fsum(x[j] for x in vals)/len(vals) for j in range(5)];res=mean[0]-sum(mean[1:4]);lower(np,res,mean[:4]);terms=sum(abs(x) for x in mean[:4])
      lowerrows.append({k:v for k,v in scope(arm,seed,ep,'D',fi,pi).items() if k!='operation_layer'}|dict(zip(('U_src','E_D','E_P','E_DP','signed_lower_bound_residual'),mean[:4]+[res]))|{'allowed_excess':1e-12+1e-12*terms})
 for k,h1 in lookup.items():
  arm,seed,ep,op,fi,pi=k
  if fi==3:
   for comparator,ci in (('h0',1),('h_aff',2)):
    other=lookup[(arm,seed,ep,op,ci,pi)];de=h1['E']-other['E'];dr=None if h1['R'] is None or other['R'] is None else h1['R']-other['R']
    direction=lambda x:'NOT_EVALUABLE' if x is None else 'OBSERVED_LOWER' if x<0 else 'OBSERVED_HIGHER' if x>0 else 'EXACT_COMPUTED_ZERO'
    paired.append({k0:v for k0,v in scope(arm,seed,ep,op,fi,pi).items() if k0!='function_id'}|{'comparator_id':comparator,'DeltaE':de,'DeltaR':dr,'E_observed_direction':direction(de),'R_observed_direction':direction(dr),'pair_R_status':'NOT_EVALUABLE' if dr is None else 'DEFINED','missing_ratio_reasons':[] if dr is not None else [h1['R_status'],other['R_status']],'empirical_check_state':'COMPLETED_REQUIRED_CHECKS'})
  if pi==0:
   alt=lookup[(arm,seed,ep,op,fi,1)];sensitive.append({k0:v for k0,v in scope(arm,seed,ep,op,fi,0).items() if k0!='policy_id'}|{'sensitive_minus_main_'+q:None if alt[q] is None or h1[q] is None else alt[q]-h1[q] for q in ('E','B','Skill','R')})
 for case in cases:
  for ep in EPS:
   a,_=ctx.affine[(case['case_id'],ep)]
   for oi,op in enumerate(OPS):
    for pi,pol in enumerate(POLICIES):
     r0=rawbank[case['case_id']][(ep,oi,1,pi)];ra=rawbank[case['case_id']][(ep,oi,2,pi)];r1=rawbank[case['case_id']][(ep,oi,3,pi)];rec={'case_id':case['case_id'],'endpoint':ep,'operation':op,'policy_id':pol}
     for f in ('E','B','Skill'):
      residual=ra[f]-a*r0[f];scale=abs(ra[f])+abs(a*r0[f]);allow=1e-12+1e-12*scale;need(abs(residual)<=allow,'AFFINE_'+f+'_MISMATCH');rec['residual_'+f]=residual;rec['allowance_'+f]=allow
     validR=all(r['R'] is not None for r in (r0,ra));validDelta=validR and r1['R'] is not None;rec['R_identity_status']='CHECKED' if validR else 'SKIPPED_UNDEFINED_R';rec['DeltaR_identity_status']='CHECKED' if validDelta else 'SKIPPED_UNDEFINED_R';rec['R_missing_reasons']=[r['R_status'] for r in (r0,ra,r1) if r['R'] is None]
     for tag,res,terms in [('R',None if not validR else ra['R']-r0['R'],[] if not validR else [ra['R'],r0['R']]),('DeltaR',None if not validDelta else (r1['R']-ra['R'])-(r1['R']-r0['R']),[] if not validDelta else [r1['R'],ra['R'],r1['R'],r0['R']])]:
      tol=None if res is None else 1e-5+1e-6*sum(abs(t) for t in terms);need(res is None or abs(res)<=tol,'AFFINE_RATIO_MISMATCH');rec['residual_'+tag]=res;rec['allowance_'+tag]=tol
     affine.append(rec)
 need((len(summary),len(paired),len(sensitive),len(lowerrows),len(spanrows),len(affine))==(1152,576,576,288,288,120),'OUTPUT_ROW_COUNTS')
 for name,rows in [('summary',summary),('paired_comparisons',paired),('reference_sensitivity',sensitive),('lower_bound_summary',lowerrows),('span_summary',spanrows)]:ctx.jsonl('analysis/'+name+'.jsonl',rows)
 ctx.jsonl('qualification/affine_identities.jsonl',affine);ctx.final_done();return summary
def execute(ctx):
 import numpy as np,torch
 ctx.progress('L0_METADATA');M=metadata(ctx,np);ctx.M=M;cases=ctx.contracts['asset_bindings.json']['cases'];ctx.configure_streams(M)
 ctx.jsonl('operations/needed_keys.jsonl',M['needed']);ctx.jsonl('operations/panel_manifest.jsonl',M['panel'])
 index=ctx.array('operations/operation_indices.npy',(322659,14),'<i8');index[:]=M['ops'];ctx.commit_array(index)
 ctx.progress('L1_COMPOSABILITY');Vbank={}
 for case in cases:
  cid=case['case_id'];rec=ctx.case_descriptors(cid)['cache'];V=cache_load(ctx,np,rec,M);Vbank[cid]=V;composability(ctx,np,V,M,cid)
 # Do not decode heads or call models until everycase passes support.
 ctx.cache_gate_passed=True;ctx.affine=calibrators(ctx,np,ctx.descriptor('FR10.affine20'))
 ctx.math_rows=math_controls(ctx,torch,np)
 bank,first,points,fds,chains=native_phase(ctx,torch,np,M,cases,Vbank)
 ctx.jsonl('qualification/point_regions.jsonl',points);ctx.jsonl('qualification/FD_checks.jsonl',fds);ctx.jsonl('qualification/chain_AD.jsonl',chains)
 ctx.qualification_complete(fds,chains)
 raws={};spans_={};low={};ctx.final_begin()
 for case in cases:
  cid=case['case_id'];ctx.progress('L2',case_id=cid);raws[cid],spans_[cid],low[cid]=analyze_case(ctx,torch,np,M,case,Vbank[cid],bank[cid],ctx.affine,first)
 ctx.progress('L2_FINALIZATION');summary=summarize(ctx,np,M,cases,raws,spans_,low)
 ctx.research_complete(summary)
