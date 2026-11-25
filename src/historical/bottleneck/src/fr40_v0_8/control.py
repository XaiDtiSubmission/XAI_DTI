"""FR40 v0.8 fixed-budget owner and compact durable control journal."""
import collections,datetime,hashlib,importlib.util,io,json,math,os,stat,sys,time,traceback
from pathlib import Path
FP=('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns','st_nlink')
def enc(x):return (json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def sha(x):return hashlib.sha256(x).hexdigest()
def need(v,m):
 if not v:raise RuntimeError(m)
def fp(s):return {k:str(getattr(s,k)) for k in FP}
class Context:
 def __init__(self,cfg):
  self.cfg=cfg;self.root=Path(cfg['root']);self.audit=Path(cfg['audit']);self.out=Path(cfg['out']);self.contracts=cfg['contracts'];self.clock=cfg['clock'];self.runtime_id=cfg['runtime_id'];self.payloads=self.contracts['asset_bindings.json']['payload_descriptors'];self.descs={x['id']:x for x in self.payloads}
  self.raws={};self.opened=set();self.decoded=set();self.counter=collections.Counter();self.reserved=collections.Counter();self.streams={};self.stack=[];self.seq=0;self.prev='0'*64;self.commit_seq=0;self.commit_prev='0'*64;self.mapping_ref='NOT_YET_DERIVED';self.arrays={};self.qual=None;self.cache_gate_passed=False;self.qualification_state=None;self.events_active={};self.ad_records=[];self.closed=False;self.last_guard=0
  self.cap={r['budget_pointer']:r['cap'] for r in self.contracts['cost_attribution.json']['counter_map']}
  self.counter_map={r['budget_pointer']:r for r in self.contracts['cost_attribution.json']['counter_map']};self.counter_ids={p:i for i,p in enumerate(sorted(self.cap))};self.final_token=None;self.log_acks={};self.log_segments={};self.repair_index=0
  self.codeids=cfg['codeids'];self.plan=cfg['root_plan'];self.plan_sha=sha(enc(self.plan));self.audit.mkdir(exist_ok=False,parents=True);self.out.mkdir(exist_ok=False,parents=True)
  self.control('frozen_plan.json',{'frozen_plan':self.plan,'root_plan_sha256':self.plan_sha,'numeric_code_id':cfg['codeids']['numeric'],'writer_control_code_id':cfg['codeids']['control'],'runtime_realization_id':self.runtime_id,'initial_space_allocation':{'supervision':67108864,'recovery':67108864,'other':16777216},'clock':self.clock})
  self.source_order={phase:[r for r in self.payloads if r['phase']==phase] for phase in ('L0','L1')}
  # Fixed source-order algorithm:metadata,thenallcaches,affine,thenper-caseheads/scores.
  self.source_order['L1']=[r for r in self.payloads if r['kind']=='FROZEN_REPRESENTATIONS']+[r for r in self.payloads if r['kind']=='FROZEN_CALIBRATION']+[r for r in self.payloads if r['phase']=='L1' and r['kind'] not in ('FROZEN_REPRESENTATIONS','FROZEN_CALIBRATION')]
  self.source_ord={r['id']:(phase,i) for phase,rs in self.source_order.items() for i,r in enumerate(rs)}
  self.events_since_progress=0;self.allocation={'supervision':67108864,'recovery':67108864,'other':16777216};self.allocation_version=0
 def files(self):return sorted({p for base in (self.audit,self.out,Path(self.cfg['admission'])) for p in base.rglob('*') if p.is_file()}) # ownedexclusiveoutputsonly
 def category(self,p):
  return 'supervision' if 'resources' in p.name else 'recovery' if p.is_relative_to(self.audit) else 'other'
 def allocate(self,path,n):
  if path.suffix=='.npy':return
  category=self.category(path);used=collections.Counter()
  for p in self.files():
   if p.suffix!='.npy':used[self.category(p)]+=p.stat().st_size
  required=used[category]+n
  if required<=self.allocation[category]:return
  deficit=required-self.allocation[category];floors={'recovery':37748736,'supervision':67108864,'other':16777216}
  donor=next((d for d in ('recovery','supervision','other') if d!=category and self.allocation[d]-max(used[d],floors[d])>=deficit),None)
  need(donor is not None,'NO_UNUSED_SPACE_FOR_REQUIRED_OUTPUTS')
  before=dict(self.allocation);self.allocation[donor]-=deficit;self.allocation[category]+=deficit;self.allocation_version+=1
  row={'transfer_id':self.allocation_version,'from':donor,'to':category,'bytes':deficit,'prior':before,'after':self.allocation,'used':dict(used),'inflight':n,'temporary_peak_bytes':0,'required_remaining_recovery_upper':37748736,'root_plan_sha256':self.plan_sha,'writer_control_code_id':'C0','reason':'recipient_prewrite_reservation'}
  raw=enc(row);path_=self.audit/'space_transfers.jsonl'
  # Pre-reserved <=4KiB emergencycontrolslot,not scientific event recursion.
  with path_.open('ab') as f:need(f.write(raw)==len(raw),'TRANSFER_SHORT_WRITE');f.flush();os.fsync(f.fileno())
  need(sum(self.allocation.values())==150994944,'POOL_ALLOCATION_SUM')
 def reserve_output(self,n,new=0):
  files=self.files();need(len(files)+new<=96,'OUTPUT96_STOP');
  non_data=[p for p in files if not p.is_relative_to(self.out)];need(len(non_data)+(new if getattr(self,'_new_control_target',True) else 0)<=40,'OTHER40_CONTROL_FILE_LIMIT');need(sum(p.stat().st_size for p in files)+n<=8589934592,'OUTPUT8GiB_STOP')
  control=sum(p.stat().st_size for p in files if p.suffix not in ('.npy',))
  need(control+(n if self._control_write else 0)<=150994944,'CONTROL144MiB_STOP')
 def write(self,path,data,new=True,control=True,journal=False):
  self._control_write=control;path=Path(path);self._new_control_target=not path.is_relative_to(self.out);path.parent.mkdir(parents=True,exist_ok=True);self.reserve_output(len(data),int(new and not path.exists()))
  if hasattr(self,'allocation'):self.allocate(path,len(data))
  with path.open('xb' if new else 'ab') as f:
   wrote=f.write(data)
   if journal:need(wrote==len(data),'SHORT_WRITE_CONTROL_BARRIER')
   else:
    while wrote<len(data):
     n=f.write(data[wrote:]);need(n>0,'SHORT_OUTPUT_WRITE_CANNOT_PROGRESS');wrote+=n
   f.flush();os.fsync(f.fileno())
  fd=os.open(path.parent,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
 def control(self,name,x):self.write(self.audit/name,enc(x))
 def append(self,name,x,proof=None):
  raw=enc(x);need(len(raw)<=2048,'JOURNAL_ROW_ENCODING_EXCEEDS2048');path=self.log_segments.get(name,self.audit/name)
  try:self.write(path,raw,new=not path.exists(),journal=True)
  except (OSError,RuntimeError) as exc:
   if not (isinstance(exc,OSError) or 'SHORT_WRITE' in str(exc)):raise
   self.guard()
   if name=='commit_ledger.jsonl':need(proof is not None,'OUTPUT_COMMIT_PROOF_REQUIRED');proof()
   previous=path.read_bytes() if path.exists() else b'';ack=self.log_acks.get(name,{'end':0,'seq':-1,'digest':'0'*64});cut=ack['end'];need(cut<=len(previous),'ACK_PREFIX_MISSING');self.repair_index+=1;new=self.audit/(Path(name).stem+'_segment_'+str(self.repair_index)+'.jsonl')
   header={'segment_id':'S'+str(self.repair_index),'previous_segment_id':path.name,'previous_segment_full_bytes':len(previous),'previous_segment_full_sha256':sha(previous),'acknowledged_prefix_end':cut,'acknowledged_prefix_sha256':sha(previous[:cut]),'last_ack_seq':ack['seq'],'last_ack_digest':ack['digest'],'failed_suffix_offset':cut,'failed_suffix_bytes':len(previous)-cut,'failed_suffix_sha256':sha(previous[cut:]),'pending_logical_record_id_or_null':str(x.get('seq'))+':'+str(x.get('record_type',x.get('commit_id'))),'pending_intended_record_sha256_or_null':sha(raw),'owner_identity_ref':'SAME_AUTHENTICATED_LIVE_OWNER','owner_cursor_digest':sha(enc({'spent':dict(self.counter),'reserved':dict(self.reserved)})),'root_plan_sha256':self.plan_sha,'numeric_code_id':'N0','writer_control_code_id':'C0','runtime_realization_id':'R0','L0_mapping_ref':self.mapping_ref,'new_segment_first_seq':x['seq'],'reason_code':type(exc).__name__,'control_space_allocation_version':'INITIAL_OR_TRANSFERRED'}
   hr=enc(header);need(len(hr)<=2048,'CONTINUATION_HEADER_TOO_LARGE');self.write(new,hr+raw,new=True,journal=True);path=new;self.log_segments[name]=new
  self.log_acks[name]={'end':path.stat().st_size,'seq':x['seq'],'digest':sha(raw)}
 def eventrow(self,typ,stream,block,**kw):
  row={'seq':self.seq,'prev_digest':self.prev,'episode_id':self.clock['episode_id'],'plan_sha256':self.plan_sha,'root_plan_sha256':self.plan_sha,'numeric_code_id':'N0','writer_control_code_id':'C0','runtime_realization_id':'R0','L0_mapping_ref':self.mapping_ref,'segment_id':'E0','record_type':typ,'stream_id':stream,'block_start':block['start'],'block_count':block['count'],'wall':time.time(),'monotonic':time.monotonic(),**kw}
  self.append('event_ledger.jsonl',row);self.prev=sha(enc(row));self.seq+=1
 def guard(self):
  import psutil
  need(time.time()<self.clock['wall_deadline_epoch'] and time.monotonic()<self.clock['monotonic_deadline'],'ORIGINAL24H_STOP')
  need(not Path(self.cfg['watchdog_stop']).exists(),'WATCHDOG_STOP')
  if time.monotonic()-self.last_guard<2:return
  self.last_guard=time.monotonic();rss=0
  for pid in self.cfg['pids']:
   try:rss+=psutil.Process(pid).memory_info().rss
   except psutil.NoSuchProcess:pass
  need(rss<=36*2**30 and psutil.virtual_memory().available>=4*2**30 and psutil.disk_usage(str(self.root)).free>=20*2**30,'PHYSICAL_RESOURCE_STOP')
 def add(self,cost,sign=1,reserved=False):
  target=self.reserved if reserved else self.counter
  for p,n in cost.items():
   need(isinstance(n,int) and n>=0,'COUNTER_QUANTUM');target[p]+=sign*n;need(target[p]>=0 and self.counter[p]+self.reserved[p]<=self.cap[p],'BUDGET_STOP:'+p)
 def start(self,stream,ordinal,n,price,skip=False):
  self.guard();key='|'.join(map(str,stream));s=self.streams.get(key)
  if s is None:
   s={'id':'J'+str(len(self.streams)),'n':n,'blocks':{},'price':price};self.streams[key]=s
  need(s['n']==n and 0<=ordinal<n,'STREAM_ID_OR_RANGE');bs=ordinal//256*256;block=s['blocks'].get(bs)
  if block is None:
   need(sum(len(v['blocks']) for v in self.streams.values())+1<=8192,'RESERVATION8192_STOP');count=min(256,n-bs);cost=collections.Counter()
   for o in range(bs,bs+count):cost.update(price(o))
   self.add(cost,reserved=True);block={'start':bs,'count':count,'cost':dict(cost),'spent':collections.Counter(),'cancelled':collections.Counter(),'processed':set(),'returned':set(),'skipped':set(),'terminal':False};s['blocks'][bs]=block
   self.eventrow('BLOCK_RESERVED',s['id'],block,reserved_cost_vector=self.compact(cost))
  need(ordinal not in block['processed'],'SCIENTIFIC_EVENT_DUPLICATE')
  cost=price(ordinal);self.add(cost,sign=-1,reserved=True)
  if not skip:self.add(cost);block['spent'].update(cost);block['processed'].add(ordinal)
  else:block['skipped'].add(ordinal);block['cancelled'].update(cost)
  token=(s,block,ordinal);self.stack.append(token);return token
 def compact(self,cost):return [[self.counter_ids[p],n] for p,n in sorted(cost.items()) if n]
 def finish(self,token=None):
  if token is None:token=self.stack.pop()
  elif token in self.stack:self.stack.remove(token)
  s,b,o=token;b['returned'].add(o)
  if len(b['returned'])+len(b['skipped'])==b['count'] and not b['terminal']:
   b['terminal']=True
   self.eventrow('BLOCK_TERMINAL',s['id'],b,processed_ordinal_prefix=b['count'],returned_ordinal_prefix=b['count'],FD_skip_bitmap_or_null=sorted(x-b['start'] for x in b['skipped']) or None,actual_dispatched_count=len(b['processed']),actual_returned_count=len(b['returned']),unknown_range_or_null=None,proven_never_dispatched_tail_or_null=None,actual_cost_or_null=self.compact(b['spent']),budget_charge=self.compact(b['spent']),certainty='KNOWN_DISPATCH_COST_QUANTA',reason_code='COMPLETE')
 def skip(self,stream,o,n,price):
  token=self.start(stream,o,n,price,skip=True);s,b,oo=token;self.stack.remove(token)
  # FD call streams have predeclared optional positions; no cost is spent ifneverdispatched.
  if len(b['returned'])+len(b['skipped'])==b['count'] and not b['terminal']:
   b['terminal']=True;self.eventrow('BLOCK_TERMINAL',s['id'],b,processed_ordinal_prefix=b['count'],returned_ordinal_prefix=b['count'],FD_skip_bitmap_or_null=sorted(x-b['start'] for x in b['skipped']),actual_dispatched_count=len(b['processed']),actual_returned_count=len(b['returned']),unknown_range_or_null=None,actual_cost_or_null=self.compact(b['spent']),budget_charge=self.compact(b['spent']),certainty='KNOWN_WITH_PROVEN_FD_SKIPS',reason_code='COMPLETE')
 def descriptor(self,identity):return self.descs[identity]
 def case_descriptors(self,cid):return {x['id'].split('.',1)[1]:x for x in self.payloads if x['id'].startswith(cid+'.')}
 def source_price(self,rec,decode=False):
  out={};prefix='/scientific_sources/';kind=rec['kind'];B=self.contracts['budget.json']['scientific_sources']
  if not decode:
   for p in ('unique_files','opens_max','opaque_sha_passes_max'):out[prefix+p]=1
   out[prefix+'opaque_bytes']=rec['bytes']
   for cond,k in [('METADATA','metadata_bytes'),('FROZEN_REPRESENTATIONS','cache_opaque_bytes'),('ORIGINAL_FULL_CHECKPOINT_APPROVED_INCIDENTAL_DECODE','original_full_checkpoint_archive_bytes'),('FROZEN_HIGH_LR_HEAD','h1_head_archive_bytes')]:
    if kind==cond:out[prefix+k]=rec['bytes']
  elif kind=='METADATA':out[prefix+'metadata_files']=1
  elif kind=='FROZEN_CALIBRATION':out.update({prefix+'calibrator_files':1,prefix+'calibrator_affine_slots':20,prefix+'calibrator_coefficient_floats':40})
  elif kind=='FROZEN_REPRESENTATIONS':
   for k,n in [('cache_files',1),('cache_decoded_rows',19748),('cache_decoded_coordinates',19748*1024),('necessary_cache_coordinate_finite_checks',19748*1024)]:out[prefix+k]=n
  elif kind=='SAVED_SCORE_NUMERIC_QUALIFICATION':
   for k,n in [('prediction_archives',1),('prediction_members',2),('prediction_member_decoded_scalars',51837),('prediction_necessary_parity_scalars',19748),('prediction_incidental_finite_schema_only',32089)]:out[prefix+k]=n
  elif 'CHECKPOINT' in kind or kind=='FROZEN_HIGH_LR_HEAD':
   original=kind!='FROZEN_HIGH_LR_HEAD';arm=rec['id'].split('.')[0][-1];cap=self.contracts['checkpoint_schema.json']['per_original'][arm] if original else self.contracts['checkpoint_schema.json']['per_h1']
   for k,n in [('original_full_checkpoints' if original else 'h1_head_files',1),('unique_used_head_parameters',295554),('research_used_head_scalar_finite_checks',295554),('all_checkpoint_storage_bytes_max',cap['storage_bytes']),('all_checkpoint_storages_max',cap['storages'])]:out[prefix+k]=n
   if original:out[prefix+'original_nonfusion_storage_incidental_bytes_cap']=cap['nonfusion_bytes'];out['/scientific_calls/original_checkpoint_decode']=1
   else:out['/scientific_calls/h1_head_decode']=1
  return out
 def read_source(self,rec,stage):
  need(rec['phase']==stage and rec['id'] not in self.opened,'SOURCE_PHASE_OR_REOPEN');phase,i=self.source_ord[rec['id']];rs=self.source_order[phase];self.start((stage,'SOURCE_OPEN_HASH'),i,len(rs),lambda o:self.source_price(rs[o]))
  self.opened.add(rec['id']);p=Path(rec['path']);a=p.lstat();need(stat.S_ISREG(a.st_mode) and a.st_size==rec['bytes'],'SOURCE_METADATA_CONFLICT')
  if rec.get('fingerprint'):need(all(fp(a)[k]==v for k,v in rec['fingerprint'].items()),'SOURCE_HISTORICAL_FINGERPRINT_CONFLICT')
  with os.fdopen(os.open(p,os.O_RDONLY|os.O_NOFOLLOW),'rb') as f:
   b=os.fstat(f.fileno());chunks=[];total=0
   while True:
    data=f.read(8*1024*1024)
    if not data:break
    chunks.append(data);total+=len(data);need(total<=rec['bytes'],'SOURCE_BYTE_LIMIT');self.guard()
   c=os.fstat(f.fileno())
  d=p.lstat();raw=b''.join(chunks);need(fp(a)==fp(b)==fp(c)==fp(d) and len(raw)==rec['bytes'] and sha(raw)==rec['sha256'],'SOURCE_INTEGRITY_CONFLICT:'+rec['id'])
  self.raws[rec['id']]=raw;self.finish();return raw
 def decode_begin(self,rec,stage):
  need(rec['id'] in self.opened and rec['id'] not in self.decoded,'DECODE_DUPLICATE');self.decoded.add(rec['id']);phase,i=self.source_ord[rec['id']];rs=self.source_order[phase];self.start((stage,'SOURCE_DECODE'),i,len(rs),lambda o:self.source_price(rs[o],True))
 def decode_end(self,rec):self.finish()
 def mapping_ready(self,M):
  self.counter_ids={p:i for i,p in enumerate(sorted(self.cap))}
  self.mapping_ref='M0';self.control('L0_mapping_identity.json',{'root_plan_sha256':self.plan_sha,'L0_mapping_sha256':sha(enc({'panel':M['panel'],'keys':M['keys'],'aliases':M['aliases'],'operation_identity_sha256':sha(M['ops'].tobytes())})),'sources':2242,'links':322659,'needed_keys':19748,'source_counts':M['source_counts'],'link_counts':M['link_counts']})
 def configure_streams(self,M):
  self.M=M;self.byep={ep:[i for i,p in enumerate(M['panel']) if p['endpoint']==ep] for ep in ('Kd','Ki')};self.keyep={ep:[i for i,p in enumerate(M['needed']) if p['endpoint']==ep] for ep in ('Kd','Ki')};self.first={ep:v[0] for ep,v in self.byep.items()};self.source_pos={ep:{i:j for j,i in enumerate(v)} for ep,v in self.byep.items()};self.key_pos={ep:{i:j for j,i in enumerate(v)} for ep,v in self.keyep.items()}
 def local(self,seq,ordinal):return seq.index(ordinal)
 def counter_for(self,kind,mult=1,links=None,ordinal=0):
  cost={}
  for r in self.counter_map.values():
   if r['owner']!=kind or r['cap']==0:continue
   p=r['budget_pointer'];cap=r['cap'];leaf=p.split('/')[-1]
   if r['kind'].startswith(('SCHEMA_','ALIAS_')) or 'bound_numeric_output' in p or leaf in ('sufficient_statistic_fields_per_vector','physical_array_float_cells','physical_operation_int64_cells'):continue
   if kind=='SOURCE_QUAL_WORK':value=(4 if 'block' in leaf else 2048)*links
   elif kind=='DERIVED_VERTEX_WORK':value=1
   elif kind=='DERIVED_SOURCE_WORK':
    if cap%3226590==0:value=cap//3226590*links
    elif cap%22420==0:value=cap//22420
    else:raise RuntimeError('UNBOUND_SOURCE_COST:'+p)
   elif kind=='DERIVED_FINALIZATION':value=cap
   elif kind=='CHAIN_WORK':value=cap//40
   else:continue
   cost[p]=value
  if kind=='DERIVED_SOURCE_WORK':cost['/scientific_work/algebra_allowance_evaluations_max']=8*links+8
  if kind=='DERIVED_FINALIZATION':cost['/scientific_work/algebra_allowance_evaluations_max']=888
  return cost
 def work(self,kind,cid,ep,i,n,extra):
  inds=self.byep[ep];self.start(('L1',cid,ep,kind),self.source_pos[ep][i],len(inds),lambda o:self.counter_for(kind,links=len(self.M['by_source'][inds[o]])))
 def work_done(self):self.finish()
 def purpose(self,kind,cid,h,dtype):
  price={'/scientific_calls/head_constructions_max' if kind=='HEAD_CONSTRUCT' else '/scientific_calls/state_apply_max':1}
  self.start(('L1',cid,h,dtype,kind),0,1,lambda o:price)
 def purpose_done(self):self.finish()
 def model_price(self,dtype,fd=False):
  pre='/scientific_calls/';return {pre+('FD_neural_scalar_forward' if fd else 'main_neural_scalar_forward' if dtype=='float64' else 'float32_qualification_neural_scalar_forward'):1,pre+'total_neural_scalar_forward':1,pre+'actual_module_forward_calls_max':1,pre+'internal_two_endpoint_linear_output_scalars':2}
 def model_begin(self,cid,h,dtype,ep,k,n,fd):
  ks=self.keyep[ep];self.start(('L1',cid,h,dtype,ep,'NATIVE_FORWARD'),self.key_pos[ep][k],len(ks),lambda o:self.model_price(dtype))
 def model_done(self):self.finish()
 def ad_begin(self,cid,h,ep,i,qualification):
  inds=[self.first[ep]] if qualification else self.byep[ep][1:];stage='L1' if qualification else 'L2'
  price={'/scientific_calls/research_input_backward':1,'/scientific_work/native_source_gradient_coordinates':1024,'/scientific_work/native_input_gradient_coordinate_finite_checks':1024}
  self.start((stage,cid,h,ep,'INPUT_AD'),0 if qualification else self.source_pos[ep][i]-1,len(inds),lambda o:price)
 def ad_done(self):self.finish()
 def chain_begin(self,cid,h,ep):self.start(('L1',cid,h,ep,'CHAIN_WORK'),0,1,lambda o:self.counter_for('CHAIN_WORK'))
 def chain_done(self):self.finish()
 def fd_price(self,o):
  source=int(o==0);points=2+source;D={'/scientific_calls/FD_derivative_checks':1};pre='/scientific_work/'
  for k,v in [('FD_check_records',1),('FD_point_records_max',points),('FD_ReLU_unit_observations_max',384*points),('FD_ternary_mask_hash_bytes_max',96*points),('FD_region_unit_pair_comparisons_max',768),('FD_perturbed_coordinate_representability_checks_max',3072),('FD_normalization_and_dot_checks',1),('qualification_point_float_field_views_max',points*2),('qualification_point_integer_field_views_max',points),('FD_whole_vector_comparison_events_max',3),('FD_representability_boolean_fields_max',3)]:D[pre+k]=v
  return D
 def fd_classify_early(self,cid,h,ep):
  t=self.start(('L1',cid,h,ep,'FD_CLASSIFY_WORK'),0,3,self.fd_price);self.events_active[(cid,h,ep)]=t
 def fd_classify_begin(self,cid,h,ep,o):
  if o==0:
   t=self.events_active[(cid,h,ep)]
   if t in self.stack:self.stack.remove(t)
   self.stack.append(t)
  else:self.start(('L1',cid,h,ep,'FD_CLASSIFY_WORK'),o,3,self.fd_price)
 def fd_classify_done(self):self.finish()
 def fd_forward_begin(self,cid,h,ep,o):self.start(('L1',cid,h,ep,'FD_FORWARD'),o,6,lambda i:self.model_price('float64',True))
 def fd_forward_done(self):self.finish()
 def fd_skip(self,cid,h,ep,o):
  for j in (o*2,o*2+1):self.skip(('L1',cid,h,ep,'FD_FORWARD'),j,6,lambda i:self.model_price('float64',True))
 def math_forward_begin(self,i,j):self.start(('L1',i,'MATH_FORWARD'),j,4,lambda o:{'/scientific_calls/math_control_scalar_forward':1})
 def math_forward_done(self):self.finish()
 def math_ad_begin(self,i):self.start(('L1',i,'MATH_AD'),0,1,lambda o:{'/scientific_calls/math_control_input_derivatives':1})
 def math_ad_done(self):self.finish()
 def vertex_begin(self,cid,ep,k,n):
  ks=self.keyep[ep];self.start(('L2',cid,ep,'DERIVED_VERTEX_WORK'),self.key_pos[ep][k],len(ks),lambda o:self.counter_for('DERIVED_VERTEX_WORK'))
 def vertex_done(self):self.finish()
 def source_work_begin(self,cid,ep,i,n,links):
  inds=self.byep[ep];self.start(('L2',cid,ep,'DERIVED_SOURCE_WORK'),self.source_pos[ep][i],len(inds),lambda o:self.counter_for('DERIVED_SOURCE_WORK',links=len(self.M['by_source'][inds[o]])))
 def source_work_done(self):self.finish()
 def final_begin(self):
  if self.final_token is None:self.final_token=self.start(('L2','DERIVED_FINALIZATION'),0,1,lambda o:self.counter_for('DERIVED_FINALIZATION'))
 def final_done(self):self.finish(self.final_token)
 def progress(self,stage,**kw):
  row={'stage':stage,'wall':time.time(),'mono':time.monotonic(),'root_plan_sha256':self.plan_sha,'scientific_source_opens':len(self.opened),'scientific_decodes':len(self.decoded),'forward':self.counter['/scientific_calls/total_neural_scalar_forward'],'backward':self.counter['/scientific_calls/research_input_backward'],**kw}
  self.write(self.audit/'science_progress.jsonl',enc(row),new=not (self.audit/'science_progress.jsonl').exists())
 def jsonl(self,name,rows):
  raw=b''.join(enc(x) for x in rows)
  if name=='operations/needed_keys.jsonl':need(len(raw)<=12582912,'NEEDED_KEYS12MiB_STOP')
  if name=='operations/panel_manifest.jsonl':need(len(raw)<=2097152,'PANEL2MiB_STOP')
  self.write(self.out/name,raw);self.commit_file(self.out/name)
 def array(self,name,shape,dtype):
  import numpy as np
  path=self.out/name;path.parent.mkdir(parents=True,exist_ok=True);self._control_write=False;self._new_control_target=False;self.reserve_output(math.prod(shape)*np.dtype(dtype).itemsize+4096,1)
  need(not path.exists(),'ARRAY_OUTPUT_EXISTS');a=np.lib.format.open_memmap(path,mode='w+',dtype=dtype,shape=shape,fortran_order=False,version=(1,0));self.arrays[id(a)]=path;return a
 def commit_array(self,a):a.flush();self.commit_file(self.arrays[id(a)])
 def commit_file(self,path):
  import numpy as np
  with path.open('rb') as f:
   os.fsync(f.fileno());offset=0
   if path.suffix=='.npy':
    need(np.lib.format.read_magic(f)==(1,0),'OWN_NPY_HEADER');np.lib.format.read_array_header_1_0(f);header_end=f.tell();f.seek(0);lengths=[header_end];remaining=path.stat().st_size-header_end
    while remaining:lengths.append(min(4194304,remaining));remaining-=lengths[-1]
   else:lengths=[path.stat().st_size]
   for size in lengths:
    raw=f.read(size);need(len(raw)==size,'COMMIT_OUTPUT_SHORT_READ')
    row={'seq':self.commit_seq,'prev_digest':self.commit_prev,'episode_id':self.clock['episode_id'],'plan_sha256':self.plan_sha,'root_plan_sha256':self.plan_sha,'numeric_code_id':'N0','writer_control_code_id':'C0','runtime_realization_id':'R0','L0_mapping_ref':self.mapping_ref,'segment_id':'K0','commit_id':'K'+str(self.commit_seq),'artifact_id':str(path.relative_to(self.out)),'byte_range':[offset,offset+len(raw)],'shape_dtype_id':'SCHEMA','bytes':len(raw),'sha256':sha(raw),'input_rule_id':'ROOT_LAYOUT','event_range_ref':'FIXED_PLAN_MAPPING','validity':'OUTPUT_BYTES_FSYNC_COMPLETE'}
    self.append('commit_ledger.jsonl',row,proof=lambda:need(sha(os.pread(f.fileno(),len(raw),offset))==sha(raw),'ALREADY_FSYNC_OUTPUT_RANGE_CONFLICT'));self.commit_prev=sha(enc(row));self.commit_seq+=1;need(self.commit_seq<=8192,'COMMIT_COUNT_LIMIT');offset+=len(raw)
 def partial_qualification(self,points,fds,chains):self.qual=(points,fds,chains)
 def math_receipt(self,rows):self.jsonl('qualification/math_controls.jsonl',rows)
 def ad_group_receipt(self,rows):self.ad_records.extend(rows)
 def qualification_complete(self,fds,chains):
  counts=collections.Counter(r['classification'] for r in fds)
  self.write(self.out/'qualification/FD_coverage.json',enc({'planned_directions':120,'groups':40,'completed':len(fds),'classifications':dict(counts),'eligible':counts['EMPIRICAL_SMOOTH_MATCH']+counts['EMPIRICAL_SMOOTH_MISMATCH'],'status':'NO_ELIGIBLE_FD_NOT_A_PASS' if not counts['EMPIRICAL_SMOOTH_MATCH'] else 'EMPIRICAL_CLASSIFICATIONS_RECORDED','no_minimum_coverage_gate':True}));self.commit_file(self.out/'qualification/FD_coverage.json')
  groups=[];names={'EMPIRICAL_SMOOTH_MATCH':'empirical_smooth_match','EMPIRICAL_SMOOTH_MISMATCH':'empirical_smooth_mismatch','UNRESOLVED_KINK_OR_CROSS_REGION':'unresolved_kink_or_cross_region','UNRESOLVED_UNREPRESENTABLE_STEP':'unresolved_unrepresentable_step','TRIVIAL_ZERO_DISPLACEMENT':'trivial_zero_displacement','INVALID_NUMERIC':'invalid_numeric'}
  for case in self.contracts['asset_bindings.json']['cases']:
   for h in ('h0','h1'):
    for ep in ('Kd','Ki'):
     rows=[r for r in fds if (r['case_id'],r['native_head'],r['endpoint'])==(case['case_id'],h,ep)];tally=collections.Counter(r['classification'] for r in rows)
     groups.append({'case_id':case['case_id'],'native_head':h,'endpoint':ep,'planned_directions':3,**{v:tally[k] for k,v in names.items()},'executed_perturbed_points':sum(r['executed_perturbed_points'] for r in rows),'has_nontrivial_direction':int(any(r['classification']!='TRIVIAL_ZERO_DISPLACEMENT' for r in rows)),'chain':'EMPIRICAL_MATCH'})
  self.jsonl('qualification/group_acceptance.jsonl',groups)

 def research_complete(self,summary):
  self.jsonl('qualification/all_source_AD.jsonl',self.ad_records);self.closed=True;self.summary=summary
 def stop_evidence(self,exc):
  unknown={};never_dispatched={}
  for stream in self.streams.values():
   for b in stream['blocks'].values():
    if b['terminal']:continue
    not_returned=b['processed']-b['returned'];remaining={p:n-b['spent'].get(p,0)-b['cancelled'].get(p,0) for p,n in b['cost'].items()};remaining={p:n for p,n in remaining.items() if n}
    for p,n in remaining.items():never_dispatched[p]=never_dispatched.get(p,0)+n
    if remaining:self.add(remaining,sign=-1,reserved=True)
    for o in not_returned:
     for p,n in stream['price'](o).items():unknown[p]=unknown.get(p,0)+n
    processed=b['processed']|b['skipped'];returned=b['returned']|b['skipped'];end=b['start']+b['count'];prefix=b['start']
    while prefix in processed:prefix+=1
    ret=b['start']
    while ret in returned:ret+=1
    self.eventrow('BLOCK_TERMINAL',stream['id'],b,processed_ordinal_prefix=prefix-b['start'],returned_ordinal_prefix=ret-b['start'],FD_skip_bitmap_or_null=sorted(o-b['start'] for o in b['skipped']) or None,actual_dispatched_count=len(b['processed']),actual_returned_count=len(b['returned']),unknown_range_or_null=[min(not_returned),max(not_returned)+1] if not_returned else None,proven_never_dispatched_tail_or_null=[prefix,end] if prefix<end else None,actual_cost_or_null=None if not_returned else self.compact(b['spent']),budget_charge=self.compact(b['spent']),certainty='UNKNOWN_FOR_UNRETURNED_ELSE_KNOWN' if not_returned else 'KNOWN',reason_code='TERMINAL_STOP_NO_REPLAY')
    b['terminal']=True
  return {'exception':type(exc).__name__,'reason':str(exc),'unknown_dispatched_unreturned_cost_vector':unknown,'cancelled_proven_never_dispatched_reservations':never_dispatched,'actual_unknown_fields':None,'no_spent_refund':True,'replay_allowed':False}
