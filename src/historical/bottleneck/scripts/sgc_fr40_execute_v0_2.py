"""FR40 scientific phase,one use,original24h clock,independent watchdog."""
import hashlib,importlib.util,json,os,platform,stat,subprocess,sys,time,traceback
from pathlib import Path
ROOT=Path('historical_sources/DTI_XAI_Bottleneck')
PROTO=ROOT/'protocol/smiles_gine_bottleneck_fr40_execution_v0_2'
ADM=ROOT/'audit/smiles_gine_bottleneck_fr40_execution_v0_2/run_001'
AUD=ADM/'science'
OUT=ROOT/'results/smiles_gine_bottleneck_fr40_v0_8/run_001'
ENTRY='historical_sources/DTI_XAI/.venv-v3_2-wp4-v0_1/bin/python'
SCRIPT=ROOT/'scripts/sgc_fr40_execute_v0_2.py'
def enc(x):return (json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def sha(x):return hashlib.sha256(x).hexdigest()
def need(v,m):
 if not v:raise RuntimeError(m)
def put(p,x,append=False):
 raw=enc(x);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('ab' if append else 'xb') as f:need(f.write(raw)==len(raw),'CONTROL_SHORT_WRITE');f.flush();os.fsync(f.fileno())
def desc(p):
 s=p.lstat();need(stat.S_ISREG(s.st_mode),'CONTROL_NOT_REGULAR')
 with os.fdopen(os.open(p,os.O_RDONLY|os.O_NOFOLLOW),'rb') as f:r=f.read();a=os.fstat(f.fileno())
 e=p.lstat();need(all(getattr(s,k)==getattr(a,k)==getattr(e,k) for k in ('st_ino','st_size','st_mtime_ns','st_ctime_ns')),'CONTROL_UNSTABLE')
 return r,{'path':str(p),'bytes':len(r),'sha256':sha(r)}
def identity(ps,pid):
 p=ps.Process(pid)
 with p.oneshot():return {'pid':pid,'birth':p.create_time(),'command':p.cmdline(),'UID':p.uids().real,'cwd':p.cwd(),'PGID':os.getpgid(pid),'RSS':p.memory_info().rss}
def resource(ps,pids,clock):
 rows=[]
 for pid in pids:
  try:rows.append(identity(ps,pid))
  except ps.NoSuchProcess:rows.append({'pid':pid,'alive':False})
 return {'wall':time.time(),'mono':time.monotonic(),'processes':rows,'RSS':sum(r.get('RSS',0) for r in rows),'available':ps.virtual_memory().available,'disk':ps.disk_usage(str(ROOT)).free,'MPS_driver':'CPU_ONLY_NOT_INITIALIZED','wall_remaining':clock['wall_deadline_epoch']-time.time(),'mono_remaining':clock['monotonic_deadline']-time.monotonic()}
def good(s):return s['RSS']<=36*2**30 and s['available']>=4*2**30 and s['disk']>=20*2**30 and min(s['wall_remaining'],s['mono_remaining'])>0
def watch():
 cfg=json.load(sys.stdin);import psutil
 try:
  while not (ADM/'science_finish.signal').exists():
   s=resource(psutil,cfg['pids']+[os.getpid()],cfg['clock']);put(ADM/'science_watchdog_resources.jsonl',s,True);need(good(s),'RESOURCE_OR_TIME_STOP');time.sleep(10)
  return 0
 except Exception as exc:put(ADM/'science_watchdog_stop.json',{'reason':str(exc)});return 3
def module(rec,name):
 raw,actual=desc(Path(rec['path']));need(all(actual[k]==rec[k] for k in ('bytes','sha256')),'CODE_IDENTITY_CONFLICT')
 obj=importlib.util.module_from_spec(importlib.util.spec_from_file_location(name,rec['path']));obj.__file__=rec['path'];exec(compile(raw,rec['path'],'exec'),obj.__dict__);return obj
def owner():
 cfg=json.load(sys.stdin);ctx=None;result={'status':'STARTED','science_evaluated':False}
 try:
  import psutil,numpy,torch
  versions={'python':platform.python_version(),'torch':str(torch.__version__).split('+')[0],'numpy':numpy.__version__,'psutil':psutil.__version__};need(versions==cfg['contracts']['runtime_policy.json']['versions'],'RUNTIME_CHANGED')
  torch.set_num_threads(1);torch.set_num_interop_threads(1);torch.use_deterministic_algorithms(True,warn_only=False)
  for rec in cfg['runtime_identity_entries']:
   p=Path(rec['actual_path']);s=p.lstat();need(all(str(getattr(s,k))==v for k,v in rec['current_fingerprint'].items()),'RUNTIME_IDENTITY_CHANGED_SINCE_ADMISSION')
  core=module(cfg['control_code'],'fr40core');numeric=module(cfg['numeric_code'],'fr40numeric');ctx=core.Context(cfg);numeric.execute(ctx);need(ctx.closed,'RESEARCH_INCOMPLETE')
  need(len(ctx.opened)==len(ctx.decoded)==56,'FINAL_SOURCE56_COUNTS')
  need(ctx.counter['/scientific_calls/main_neural_scalar_forward']==394960 and ctx.counter['/scientific_calls/float32_qualification_neural_scalar_forward']==394960 and ctx.counter['/scientific_calls/research_input_backward']==44840,'FINAL_CALL_COUNTS')
  result.update(status='COMPLETE_FINITE_EMPIRICAL_DEVELOPMENT_RESEARCH',science_evaluated=True,all10cases=True,practical_gate='NOT_ADJUDICATED')
 except Exception as exc:
  reason=str(exc);status='SUPPORT_NOT_EVALUABLE' if reason.startswith('SUPPORT_NOT_EVALUABLE') else 'INTEGRITY_STOP' if any(k in reason for k in ('CONFLICT','INTEGRITY','SCHEMA','IDENTITY','ALIAS','SOURCE_','FROZEN_COUNTS')) else 'EMPIRICAL_NUMERICAL_CHECK_STOP' if any(k in reason for k in ('NONFINITE','MISMATCH','FD_INVALID','R_REQUIRED')) else 'ENGINEERING_STOP_NO_REPLAY'
  result.update(status=status,reason=reason,exception=type(exc).__name__,traceback=traceback.format_exc())
  if ctx:result['stop_evidence']=ctx.stop_evidence(exc)
 finally:
  if ctx:
   result.update(source_purpose_dispatches=len(ctx.opened),purpose_decodes=len(ctx.decoded),charged_cost_quanta=dict(ctx.counter),still_reserved=dict(ctx.reserved),charged_quantum_not_CPU_instruction_counts=True,observed_native_call_dispatches={k:ctx.counter[k] for k in ('/scientific_calls/main_neural_scalar_forward','/scientific_calls/float32_qualification_neural_scalar_forward','/scientific_calls/FD_neural_scalar_forward','/scientific_calls/research_input_backward','/scientific_calls/math_control_scalar_forward','/scientific_calls/math_control_input_derivatives')},root_plan_sha256=ctx.plan_sha,cache_gate_passed=ctx.cache_gate_passed,opened_ids=sorted(ctx.opened),decoded_ids=sorted(ctx.decoded),no_parameter_gradients=True,no_training=True,MODEL_GATE='EXPOSED_DEVELOPMENT_ONLY')
   ctx.control('execution_receipt.json',result)
  else:put(ADM/'science_owner_early_stop.json',result)
 return 0 if result['status']=='COMPLETE_FINITE_EMPIRICAL_DEVELOPMENT_RESEARCH' else 2
def supervise(expected):
 need(not AUD.exists() and not OUT.exists(),'ONE_USE_OUTPUT_EXISTS_NO_RETRY')
 raw,bid=desc(PROTO/'scientific_binding.json');need(sha(raw)==expected,'BINDING_SHA_CONFLICT');b=json.loads(raw)
 for rec in b['controls']+b['code']+b['contract_files']:
  raw,actual=desc(Path(rec['path']));need(all(actual[k]==rec[k] for k in ('bytes','sha256')),'BOUND_CONTROL_CHANGED')
 clock=json.loads(desc(ADM/'clock_anchor.json')[0]);runtime=json.loads(desc(ADM/'runtime_identity_receipt.json')[0]);need(runtime['status']=='RUNTIME_AND_SOURCE_METADATA_READY_NO_L0_BODY','RUNTIME_NOT_READY');need(clock==b['clock'],'CLOCK_MISMATCH');need(min(clock['wall_deadline_epoch']-time.time(),clock['monotonic_deadline']-time.monotonic())>0,'ORIGINAL24H_STOP')
 contracts={Path(r['path']).name:json.loads(desc(Path(r['path']))[0]) for r in b['contract_files']}
 import psutil
 for p in psutil.process_iter(['pid','name']):
  if p.pid==os.getpid() or 'python' not in (p.info['name'] or '').lower():continue
  try:need(str(SCRIPT) not in p.cmdline(),'DUPLICATE_DISPATCHER')
  except (psutil.NoSuchProcess,psutil.AccessDenied):pass
 env=dict(os.environ);env.update(contracts['runtime_policy.json']['environment']);env.update(PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1');proc=watchdog=None;receipt={'status':'STARTED','clock':clock,'binding':bid}
 try:
  proc=subprocess.Popen([ENTRY,'-I','-B',str(SCRIPT),'owner'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=ROOT,env=env)
  watchdog=subprocess.Popen([ENTRY,'-I','-B',str(SCRIPT),'watchdog'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=ROOT,env=env)
  pids=[os.getpid(),proc.pid,watchdog.pid];cfg={'root':str(ROOT),'audit':str(AUD),'out':str(OUT),'admission':str(ADM),'contracts':contracts,'clock':clock,'runtime_id':runtime['runtime_realization_id'],'runtime_identity_entries':runtime['runtime_identity_entries'],'pids':pids,'watchdog_stop':str(ADM/'science_watchdog_stop.json'),'control_code':b['control_code'],'numeric_code':b['numeric_code'],'codeids':{'numeric':b['numeric_realization_id'],'control':b['control_code']['sha256']},'root_plan':b['root_plan']}
  put(ADM/'science_process_start.json',resource(psutil,pids,clock));need(good(resource(psutil,pids,clock)),'INITIAL_RESOURCE_STOP')
  proc.stdin.write(enc(cfg));proc.stdin.close();proc.stdin=None;watchdog.stdin.write(enc({'pids':[os.getpid(),proc.pid],'clock':clock}));watchdog.stdin.close();watchdog.stdin=None
  last=0
  while proc.poll() is None:
   s=resource(psutil,pids,clock);need(good(s),'PHYSICAL_RESOURCE_OR_TIME_STOP');need(watchdog.poll() is None and not (ADM/'science_watchdog_stop.json').exists(),'WATCHDOG_STOP')
   if time.monotonic()-last>=10:put(ADM/'science_supervisor_resources.jsonl',s,True);last=time.monotonic()
   time.sleep(.5)
  _,err=proc.communicate();receipt.update(owner_exit_code_actual=proc.returncode,owner_stderr=err.decode(errors='replace')[:12000],status='OWNER_COMPLETED' if proc.returncode==0 else 'OWNER_STOPPED')
 except Exception as exc:receipt.update(status='SUPERVISION_STOP',reason=str(exc),traceback=traceback.format_exc())
 finally:
  if proc and proc.poll() is None:proc.terminate();proc.wait(timeout=20);receipt['owner_exit_code_actual']=proc.returncode
  put(ADM/'science_finish.signal',{'done':True})
  if watchdog:
   try:_,err=watchdog.communicate(timeout=20);receipt['watchdog_exit_code_actual']=watchdog.returncode;receipt['watchdog_stderr']=err.decode(errors='replace')[:4000]
   except subprocess.TimeoutExpired:watchdog.terminate();watchdog.wait(timeout=20);receipt['watchdog_exit_code_actual']=watchdog.returncode
  put(ADM/'science_supervision_receipt.json',receipt);artifacts=[]
  for base in (ADM,OUT):
   if base.exists():
    for p in sorted(base.rglob('*')):
     if p.is_file() and p.name!='run_seal.json':artifacts.append(desc(p)[1])
  put(ADM/'run_seal.json',{'status':receipt['status'],'artifacts':artifacts,'self_excluded':True,'MODEL_GATE':'EXPOSED_DEVELOPMENT_ONLY','practical_gate':'NOT_ADJUDICATED','no_retry':True,'original_clock':clock})
 print(json.dumps({'status':receipt['status'],'run':str(ADM),'owner_exit':receipt.get('owner_exit_code_actual')},ensure_ascii=False));return 0 if receipt['status']=='OWNER_COMPLETED' else 2
if __name__=='__main__':
 need(len(sys.argv)>=2 and sys.argv[1] in ('supervisor','owner','watchdog'),'ROLE')
 sys.exit(supervise(sys.argv[2]) if sys.argv[1]=='supervisor' else owner() if sys.argv[1]=='owner' else watch())
