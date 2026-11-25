#!/usr/bin/env python3
"""B3 W03: figures/tables from already checked W02 aggregate slots only.
No inherited table, sample, asset or parameter is opened. No science arithmetic.
Float casts, categorical offsets, axis limits and pagination are display only.
"""
import hashlib,html,itertools,json,math,shutil,subprocess
from collections import Counter
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D

ROOT=Path(__file__).resolve().parents[1]
W02='references/numeric_citations_b3_v0_1'
FIG='figures/w03_b3_v0_1';TAB='tables/w03_b3_v0_1'
REF='references/numeric_citations_b3_w03_v0_1';AUD='audit/w03_b3_v0_1'
ARMS=['G','S'];EPS=['Kd','Ki','KD_KI_EQUAL']
POL=['FIT_ROW_UNIFORM','FIT_PROTEIN_INPUT_UNIFORM']
SEEDS20=['seed_017','seed_029','seed_043','seed_071','seed_101','MEAN5']
SEEDS30=[17,29,43,71,101,'MEAN5']
F4=['h0','h_aff','h_1d','h1'];F5=['h0','h_aff','h_1d','h1_minus_bar_qI','h1']
FUNLABELS={'h0':r'$h_0$','h_aff':r'$h_{aff}$','h_1d':r'$h_{1d}$',
 'h1':r'$h_1$','h1_minus_bar_qI':r'$h_1-\overline{q_I}$'}
CONTRASTS=['J30_aff','J30_1d','C_I','J30_minusI_aff','J30_minusI_1d']
CLABELS=[r'$J30_{aff}$',r'$J30_{1d}$',r'$C_I$',r'$J30_{-I,aff}$',r'$J30_{-I,1d}$']
Q=['q0','qD','qP','qI','rD_obs','rP_obs'];TARGETS=['q','q_bg','q_nl']
COLORS=['#0077BB','#EE7733','#009988','#CC3311','#AA3377','#111111']
MARKERS=['o','s','^','v','P','D']
TRACE=[];ARTIFACTS=[];LAYOUT=[];ROWS={};INDEX={};BYCID={};SOURCES={};LEDGER={}
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.titlesize':10,
 'axes.labelsize':9,'xtick.labelsize':8,'ytick.labelsize':8,'legend.fontsize':8,
 'axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':.65,
 'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'none','savefig.dpi':300,
 'figure.facecolor':'white','axes.unicode_minus':True})

def sha(b):return hashlib.sha256(b).hexdigest()
def loadjson(path):return json.loads((ROOT/path).read_text())
def textfile(path,text):
    assert path.startswith((FIG+'/',TAB+'/',REF+'/',AUD+'/','docs/B3_W03','planning/w03_b3','evidence/w03_b3','README_W03_B3'))
    p=ROOT/path
    if p.exists() and p.read_text()==text:return
    assert not (ROOT/AUD/'receipt.json').exists(),'Completed B3 W03 v0.1 is preserved'
    if p.exists():
        old=p.read_text();patch='*** Begin Patch\n*** Update File: '+path+'\n@@\n'+''.join('-'+x+'\n' for x in old.splitlines())+''.join('+'+x+'\n' for x in text.splitlines())+'*** End Patch\n'
    else:patch='*** Begin Patch\n*** Add File: '+path+'\n'+''.join('+'+x+'\n' for x in text.splitlines())+'*** End Patch\n'
    r=subprocess.run([shutil.which('apply_patch')],input=patch,text=True,cwd=ROOT,capture_output=True)
    assert r.returncode==0,r.stdout+r.stderr
    assert p.read_text()==text,path
def save(path,obj):textfile(path,json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def lines(path,records):textfile(path,''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n' for r in records))
def clean(cell):return {k:v for k,v in cell.items() if k!='value'}
def esc(value):return html.escape(str(value),quote=True)

def read_ledger():
    global LEDGER
    receipt=loadjson('audit/w02_b3_v0_1/receipt.json')
    for item in receipt['artifacts']:
        b=(ROOT/item['path']).read_bytes();assert sha(b)==item['sha256'] and len(b)==item['bytes']
    LEDGER=loadjson(W02+'/ledger.json');SOURCES.update({s['source_id']:s for s in LEDGER['sources']})
    for reg in LEDGER['row_registries']:
        sid=reg['source_id'];rr=[json.loads(x) for x in (ROOT/reg['path']).read_text().splitlines()]
        assert len(rr)==reg['row_count'];ROWS[sid]=rr
        for r in rr:
            key=(sid,r['container'],tuple((k,type(v).__name__,str(v)) for k,v in sorted(r['native_axes'].items())))
            assert key not in INDEX;INDEX[key]=r
            for field,cell in r['values'].items():
                assert cell['citation_id'] not in BYCID
                BYCID[cell['citation_id']]=(r,field,cell)
    assert len(BYCID)==18482 and sum(len(v) for v in ROWS.values())==3469
    assert Counter(c['state'] for r,f,c in BYCID.values())=={'NON_NULL_SAVED_NUMBER':18066,'SAVED_NULL':416}

def row(sid,container,**axes):
    key=(sid,container,tuple((k,type(v).__name__,str(v)) for k,v in sorted(axes.items())))
    return INDEX[key]
def c02(container,arm,seed,ep,pol=None,**extra):
    a=dict(arm=arm,seed_layer=seed,endpoint_layer=ep,**extra)
    if pol is not None:a['reference_scheme']=pol
    sid='C02_REFERENCE' if container=='/complete_six_quantity_summaries' else 'C02_AMPLITUDES'
    return row(sid,container,**a)
def c03(sid,container,arm,seed,ep,pol=None,**extra):
    a=dict(arm=arm,seed_layer=seed,endpoint_layer=ep,target_id='q',**extra)
    if pol is not None:a['reference_scheme']=pol
    return row(sid,container,**a)

def citation(r,field,artifact,page,subplot,export='pdf'):
    cell=r['values'][field];source=SOURCES[r['source_id']]
    resolved=dict(LEDGER['common_citation_context'],inherited_source_path=source['inherited_source_path'],source_sha256=source['source_sha256'])
    resolved.update(r['citation_context']);resolved.update(cell['citation_context'])
    resolved.update(manuscript_version='B3_W03_FIGURES_TABLES_v0.1_NO_BODY',figure_or_supplement_id=artifact,
      transcription_check_status='EXACT_W02_TOKEN_DIRECT_DISPLAY')
    resolved['display_precision']=('SOURCE_JSON_LEXEME_UNROUNDED' if artifact.startswith('Table2.') else
      'DIRECT_W02_COORDINATE; ANNOTATION_4_DECIMALS_OR_EXACT_ZERO_DISPLAY_ONLY; FULL_TOKEN_IN_COMPANION_TABLE'
      if artifact.startswith('Fig.2') else
      'DIRECT_W02_COORDINATE; AXIS_TICK_FORMATTING_DISPLAY_ONLY; FULL_TOKEN_IN_COMPANION_TABLE')
    return {'artifact_id':artifact,'page_1based':page,'subplot_id':subplot,'export':export,
      'W02_citation_id':cell['citation_id'],'source_id':r['source_id'],'row_id':r['row_id'],'field':field,
      'native_axes':r['native_axes'],'original_raw_json':cell['raw_json'],'original_cell_state':cell['state'],
      'resolved_citation':resolved,'original_qualification':r['qualification'],
      'rendering':'DIRECT_TOKEN_FLOAT_CAST' if cell['state']=='NON_NULL_SAVED_NUMBER' else 'EXPLICIT_NULL_OR_UNAVAILABLE_NO_IMPUTATION'}
def value(r,field,artifact,page,subplot,export='pdf'):
    rec=citation(r,field,artifact,page,subplot,export);TRACE.append(rec)
    if rec['original_cell_state']=='NON_NULL_SAVED_NUMBER':
        v=float(rec['original_raw_json']);assert math.isfinite(v)
        if artifact.startswith('Fig.2'):rec['display_annotation']=format(v,'.4f') if v else '0.0'
        return v
    if artifact.startswith('Fig.2'):rec['display_annotation']='null (not applicable; original aggregate count)'
    return math.nan

def style(ax,label,zero=True):
    if zero:ax.axhline(0,lw=.65,color='#777777',zorder=0)
    ax.grid(axis='y',color='#dddddd',lw=.45,alpha=.65);ax.set_axisbelow(True)
    ax.set_ylabel(label)
def seed_legend(fig,y=.92,include_mean=True):
    handles=[Line2D([],[],color=c,marker=m,ls='none',ms=4,label=('MEAN5 (saved)' if i==5 else str(SEEDS30[i]))) for i,(c,m) in enumerate(zip(COLORS,MARKERS)) if include_mean or i<5]
    fig.legend(handles=handles,ncol=6,frameon=False,loc='upper left',bbox_to_anchor=(.075,y),fontsize=8)
def seed_scatter(ax,xx,yy,seedindex):
    # Offsets distinguish categorical computational repeats; they are not data.
    offset=(seedindex-2.5)*.07
    ax.scatter([x+offset for x in xx],yy,c=COLORS[seedindex],marker=MARKERS[seedindex],
      s=20 if seedindex==5 else 12,linewidths=.45,edgecolors='white',alpha=1 if seedindex==5 else .85,zorder=8 if seedindex==5 else 3)
def finish(fig,artifact,page,pdf,stem=None):
    # Remove locator ticks outside the visible axis limits; no data is clipped.
    for ax in fig.axes:
        if not ax.axison:continue
        lo,hi=sorted(ax.get_xlim());ax.set_xticks([t for t in ax.get_xticks() if lo<=t<=hi])
        lo,hi=sorted(ax.get_ylim());ax.set_yticks([t for t in ax.get_yticks() if lo<=t<=hi])
    fig.canvas.draw();w,h=fig.canvas.get_width_height();outside=[]
    for text in fig.findobj(matplotlib.text.Text):
        if not text.get_visible() or not text.get_text():continue
        b=text.get_window_extent(fig.canvas.get_renderer())
        if b.x0 < -1 or b.y0 < -1 or b.x1>w+1 or b.y1>h+1:outside.append(text.get_text())
    LAYOUT.append({'artifact_id':artifact,'page_1based':page,'text_outside_canvas':outside})
    assert not outside,(artifact,page,outside)
    if pdf is not None:pdf.savefig(fig)
    if stem:
        fig.savefig(ROOT/FIG/(stem+'.svg'));fig.savefig(ROOT/FIG/(stem+'.png'),dpi=300)
    plt.close(fig)

def draw_panel(fig,gs,panel,export):
    art='Fig.2'+panel;primary=dict(arm='G',seed_layer='MEAN5',endpoint_layer='KD_KI_EQUAL')
    if panel=='D':
        sub=gs.subgridspec(2,1,hspace=.9);a=fig.add_subplot(sub[0]);b=fig.add_subplot(sub[1])
        r=c03('C03_CONTRASTS','/secondary_composition','G','MEAN5','KD_KI_EQUAL',POL[0])
        for ax,fields,labels in [(a,['A_plus','A_minus'],['Positive part A+','Negative magnitude A-']),
                                (b,['p_plus','p_minus','p_zero'],['Positive p+','Negative p-','Exact-zero p0'])]:
            yy=[value(r,f,art,1,'amplitudes' if ax is a else 'proportions',export) for f in fields]
            ax.barh(range(len(fields)),yy,color=['#0077BB','#EE7733','#777777'][:len(fields)],height=.48,
                    edgecolor='white',hatch=['','//','..'][:len(fields)])
            ax.set_yticks(range(len(fields)),labels,fontsize=7);ax.invert_yaxis()
            ax.set_xlim(0,max(yy)*1.22 if max(yy)>0 else 1)
            ax.grid(axis='x',color='#dddddd',lw=.4);ax.set_axisbelow(True)
            for i,v in enumerate(yy):
                if math.isfinite(v):ax.annotate(format(v,'.4f') if v else '0.0',(v,i),xytext=(3,0),textcoords='offset points',va='center',fontsize=7)
        a.set_title('D  O_SRC: loss composition',loc='left',fontweight='bold',fontsize=9.4,pad=9)
        a.set_xlabel('Mean positive / negative-part magnitude',fontsize=7.7)
        b.set_xlabel('Saved endpoint-weighted, seed-average proportion',fontsize=7.2)
        for f in ['n_plus','n_minus','n_zero','N_e']:
            value(r,f,art,1,'original_aggregate_null_counts',export)
        b.text(0,-.96,'Aggregate n+, n-, n0, N_e: null (not applicable)',transform=b.transAxes,fontsize=6.8,color='#5b4b24')
        return
    ax=fig.add_subplot(gs)
    if panel=='A':
        r=row('C01_SUMMARY','/summary',**primary);funs=F4
        yy=[value(r,'risks/'+f,art,1,'four_risks',export) for f in funs]
        title='A  Full GATE: four saved risks';xlabel='Equal-endpoint MAE | GATE n = 5,697'
        labels=[FUNLABELS[f] for f in funs]
    elif panel=='B':
        contrasts=['J30_aff','J30_1d'];funs=contrasts
        yy=[value(c03('C03_CONTRASTS','/rows','G','MEAN5','KD_KI_EQUAL',POL[0],contrast_id=c),'difference',art,1,'saved_J30',export) for c in contrasts]
        title='B  Same O_SRC: saved J30';xlabel='Saved calibration-risk minus h1-risk'
        labels=CLABELS[:2]
    else:
        funs=F5
        yy=[value(c03('C03_RISKS','/rows','G','MEAN5','KD_KI_EQUAL',POL[0],function_id=f),'risk',art,1,'five_risks',export) for f in funs]
        title='C  Same O_SRC: five saved risks';xlabel='Equal-endpoint MAE | 2,242 sources'
        labels=[FUNLABELS[f] for f in funs]
    if panel=='B':
        ax.barh(range(len(yy)),yy,color=['#0077BB','#EE7733'],height=.42,hatch=['','//'],edgecolor='white')
        ax.set_xlim(0,max(yy)*1.25);ax.axvline(0,lw=.65,color='#777777')
    else:
        markers={'h0':'o','h_aff':'s','h_1d':'^','h1_minus_bar_qI':'v','h1':'D'}
        for i,v in enumerate(yy):ax.scatter([v],[i],color=COLORS[i] if i<len(yy)-1 else '#111111',s=33,marker=markers[funs[i]])
        spread=max(yy)-min(yy);ax.set_xlim(min(yy)-spread*.15,max(yy)+spread*.28)
    for i,v in enumerate(yy):ax.annotate(format(v,'.4f'),(v,i),xytext=(5,0),textcoords='offset points',va='center',fontsize=7.5)
    ax.set_yticks(range(len(yy)),labels);ax.invert_yaxis();ax.set_title(title,loc='left',fontweight='bold',fontsize=9.4,pad=13)
    ax.set_xlabel(xlabel,fontsize=8);ax.grid(axis='x',color='#dddddd',lw=.45);ax.set_axisbelow(True)

def main_figures():
    path=FIG+'/Fig2_main_v0_1.pdf'
    with PdfPages(ROOT/path,metadata={'Title':'Fig.2: saved task risks and anchored deletion','Author':''}) as pdf:
        fig=plt.figure(figsize=(6.9,7.4));gs=fig.add_gridspec(2,2,left=.13,right=.965,bottom=.16,top=.86,wspace=.70,hspace=.72)
        fig.suptitle('Fig.2 | Saved risks, gains and anchored deletion',x=.10,y=.965,ha='left',fontsize=12,fontweight='bold')
        fig.text(.10,.917,'G | saved five-seed mean | Kd/Ki equal | full reference row-uniform for O_SRC',fontsize=7.4,color='#444444')
        for i,panel in enumerate('ABCD'):draw_panel(fig,gs[i//2,i%2],panel,'composite_pdf_svg_png')
        fig.text(.10,.042,'GATE and O_SRC are separate panels. Risk axes use zoomed dot displays.',fontsize=7.2,color='#444444')
        fig.text(.10,.022,'Finite exposed-development summaries; exact values and original status remain in Table2 / S2 / S3.',fontsize=6.9,color='#444444')
        finish(fig,'Fig.2',1,pdf,'Fig2_main_v0_1')
    for panel in 'ABCD':
        fig=plt.figure(figsize=(4.5,4.15 if panel=='D' else 3.3))
        gs=fig.add_gridspec(1,1,left=.30 if panel=='D' else .20,right=.94,bottom=.29 if panel=='D' else .23,top=.85)
        draw_panel(fig,gs[0],panel,'standalone_svg_png')
        fig.text(.08,.025,'G | saved MEAN5 / Kd-Ki equal | finite development scope',fontsize=7)
        finish(fig,'Fig.2'+panel,1,None,'Fig2'+panel+'_v0_1')
    ARTIFACTS.append({'artifact_id':'Fig.2','path':path,'page_count':1,'panel_ids':['Fig.2'+p for p in 'ABCD'],
      'png':FIG+'/Fig2_main_v0_1.png','svg':FIG+'/Fig2_main_v0_1.svg','claim_ids':['C01','C03'],
      'caption':'A: whole-GATE four saved risks. B: original same-O_SRC J30_aff/J30_1d contrasts. C: same-O_SRC five saved risks before/after the specified reference-composed deletion. D: original positive/negative loss-part magnitudes and proportions on separate axes; exact-zero proportion retained and aggregate count nulls stated. G/MEAN5/KD_KI_EQUAL; O_SRC uses full FIT_ROW_UNIFORM. Risk axes are zoomed dot displays; categories are not a training trajectory. Table2 preserves exact saved values; finite exposed-development scope only.'})

def supp_base(title,arm,ep,policy,page,rows=1):
    fig=plt.figure(figsize=(11.7,8.3));fig.suptitle(title,x=.07,y=.965,ha='left',fontsize=13,fontweight='bold')
    pol=('no four-corner reference axis' if title.startswith('S3.GATE') else
         'full reference pair: protein-input-uniform vs row-uniform') if policy is None else ('full FIT row-uniform' if policy==POL[0] else 'full protein-input-uniform')
    panel='whole GATE 5,697' if title.startswith('S3.GATE') else 'O_SRC 2,242 sources / 322,659 links'
    fig.text(.075,.93,f'{panel} | {arm} | {ep} | {pol} | page {page}',fontsize=9)
    seed_legend(fig,.916,include_mean=not title.startswith('S3.GATE') or ep=='KD_KI_EQUAL')
    fig.text(.075,.028,'Saved computational seed layers; no prediction ensemble, new uncertainty interval or scientific PASS. Complete exact values/status: companion table.',fontsize=8,color='#444444')
    return fig

def s2_figures():
    path=FIG+'/S2_figures_v0_1.pdf';page=0
    with PdfPages(ROOT/path,metadata={'Title':'S2: saved functional amplitudes and reference summaries','Author':''}) as pdf:
        for family in ['functional','basic','fixed','reference']:
            policies=[None] if family=='reference' else POL
            for arm,ep,pol in itertools.product(ARMS,EPS,policies):
                page+=1;art='S2.'+family;fig=supp_base(art+' | Original aggregate quantities',arm,ep,pol,page)
                if family in ['functional','reference']:
                    axes=fig.subplots(3,3);fig.subplots_adjust(left=.075,right=.98,bottom=.13,top=.82,wspace=.31,hspace=.64)
                    metrics=['M','U','signed_mean'] if family=='functional' else ['mean_delta','mean_abs_delta','strict_sign_flip_fraction']
                    for a,target in enumerate(TARGETS):
                        for b,field in enumerate(metrics):
                            ax=axes[a,b]
                            for j,seed in enumerate(SEEDS20):
                                yy=[value(c02('/functional' if family=='functional' else '/complete_six_quantity_summaries',arm,seed,ep,pol,target_id=target,quantity_id=q),field,art,page,target+'.'+field) for q in Q]
                                seed_scatter(ax,range(6),yy,j)
                            ax.set_xticks(range(6),Q,fontsize=7);style(ax,field);ax.set_title(target+' | '+field,fontsize=9)
                    fig.text(.075,.077,'M/U: source-component amplitudes, not task gain.' if family=='functional' else 'Sensitivity minus main; D_ref(q/qI) is the original mean_abs_delta subset. Transition vectors remain native positions in the complete table.',fontsize=8)
                elif family=='basic':
                    axes=fig.subplots(1,3);fig.subplots_adjust(left=.075,right=.98,bottom=.19,top=.79,wspace=.28)
                    for ax,field in zip(axes,['M_f','U_f','C_U_MINUS_M']):
                        for j,seed in enumerate(SEEDS20):
                            yy=[value(c02('/basic_amplitudes',arm,seed,ep,pol,function_id=f),field,art,page,field) for f in F4]
                            seed_scatter(ax,range(4),yy,j)
                        ax.set_xticks(range(4),[FUNLABELS[f] for f in F4]);style(ax,field);ax.set_title(field)
                    fig.text(.075,.10,'Original basic-function four-point amplitudes. Raw cancellation differences are retained without clipping or an effect threshold.',fontsize=9)
                else:
                    axes=fig.subplots(1,2);fig.subplots_adjust(left=.09,right=.97,bottom=.19,top=.79,wspace=.28)
                    cmp=['h_aff','h0','h_1d']
                    for ax,field in zip(axes,['delta_M','delta_U']):
                        for j,seed in enumerate(SEEDS20):
                            yy=[value(c02('/fixed_amplitude_comparisons',arm,seed,ep,pol,reference_function_id=f),field,art,page,field) for f in cmp]
                            seed_scatter(ax,range(3),yy,j)
                        ax.set_xticks(range(3),[FUNLABELS[f] for f in cmp]);style(ax,field);ax.set_title('Original saved '+field)
                        ax.set_xlabel('Comparator b for saved '+('M(h1) - M(b)' if field=='delta_M' else 'U(h1) - U(b)'))
                    fig.text(.09,.10,'Saved paired amplitude differences; M(h1 - comparator) is a different object. No amplitude difference was recomputed.',fontsize=9)
                finish(fig,art,page,pdf)
    assert page==42
    ARTIFACTS.append({'artifact_id':'S2.Figures','path':path,'page_count':page,'claim_ids':['C02'],
      'caption':'All saved scalar summaries across S/G, six seed layers, three endpoint layers and both full references where applicable: functional M/U/signed_mean, four basic amplitudes/cancellation, original fixed delta_M/delta_U, and all six-quantity reference scalar summaries. Original cancellation statuses and complete nine-position transition vectors/count nulls remain in companion tables. No sign-pair labels are guessed; M/U are not C_I or task gains.'})

def s3_figures():
    path=FIG+'/S3_figures_v0_1.pdf';page=0
    with PdfPages(ROOT/path,metadata={'Title':'S3: whole-GATE and O_SRC saved risks, contrasts and composition','Author':''}) as pdf:
        for arm,ep in itertools.product(ARMS,EPS):
            page+=1;art='S3.GATE';fig=supp_base(art+' | Four risks and whole-GATE J',arm,ep,None,page)
            axes=fig.subplots(1,2);fig.subplots_adjust(left=.085,right=.975,bottom=.19,top=.79,wspace=.28)
            for j,seed in enumerate(SEEDS20):
                if seed=='MEAN5':
                    if ep!='KD_KI_EQUAL':continue
                    rr=row('C01_SUMMARY','/summary',arm=arm,seed_layer=seed,endpoint_layer=ep)
                    rf=['risks/'+f for f in F4];jf=['J_aff','J_1d']
                else:
                    case='formal_'+seed.split('_')[1]+'_'+arm
                    rr=row('C01_CASES','/cases',case_id=case,arm=arm,seed_layer=seed,endpoint_layer='PER_CELL')
                    rf=[f+('/EBMAE' if ep=='KD_KI_EQUAL' else '/MAE/'+ep) for f in F4]
                    jf=['J_aff','J_1d'] if ep=='KD_KI_EQUAL' else ['endpoint_J/'+ep+'/aff','endpoint_J/'+ep+'/1d']
                seed_scatter(axes[0],range(4),[value(rr,f,art,page,'four_risks') for f in rf],j)
                seed_scatter(axes[1],range(2),[value(rr,f,art,page,'whole_GATE_J') for f in jf],j)
            axes[0].set_xticks(range(4),[FUNLABELS[f] for f in F4]);style(axes[0],'Saved risk',False);axes[0].set_title('Four functions; categorical comparisons')
            axes[1].set_xticks(range(2),[r'$J_{aff}$',r'$J_{1d}$']);style(axes[1],'Whole-GATE J');axes[1].set_title('Original risk-gain slots')
            fig.text(.085,.10,'MEAN5 is saved only for the equal-endpoint summary. It is absent from endpoint-only views; no mean is reconstructed.',fontsize=9)
            finish(fig,art,page,pdf)
        for family in ['risk_contrast','composition','reference']:
            policies=[None] if family=='reference' else POL
            for arm,ep,pol in itertools.product(ARMS,EPS,policies):
                page+=1;art='S3.'+family;fig=supp_base(art+' | Original O_SRC values',arm,ep,pol,page)
                if family=='risk_contrast':
                    axes=fig.subplots(1,2);fig.subplots_adjust(left=.09,right=.975,bottom=.20,top=.79,wspace=.29)
                    for j,seed in enumerate(SEEDS30):
                        yy=[value(c03('C03_RISKS','/rows',arm,seed,ep,pol,function_id=f),'risk',art,page,'five_risks') for f in F5]
                        seed_scatter(axes[0],range(5),yy,j)
                        yy=[value(c03('C03_CONTRASTS','/rows',arm,seed,ep,pol,contrast_id=c),'difference',art,page,'five_saved_contrasts') for c in CONTRASTS]
                        seed_scatter(axes[1],range(5),yy,j)
                    axes[0].set_xticks(range(5),[FUNLABELS[f] for f in F5]);style(axes[0],'Saved risk',False)
                    axes[1].set_xticks(range(5),CLABELS);style(axes[1],'Original saved loss contrast')
                    axes[0].set_title('Same-source five risks');axes[1].set_title('Same-source original J30 / C_I / residuals')
                    fig.text(.09,.10,'All contrasts use their original saved difference field. No risk subtraction, gain fraction, or globally additive interpretation.',fontsize=9)
                elif family=='composition':
                    axes=fig.subplots(1,2);fig.subplots_adjust(left=.09,right=.975,bottom=.20,top=.79,wspace=.27)
                    for j,seed in enumerate(SEEDS30):
                        rr=c03('C03_CONTRASTS','/secondary_composition',arm,seed,ep,pol)
                        seed_scatter(axes[0],range(2),[value(rr,f,art,page,'part_magnitudes') for f in ['A_plus','A_minus']],j)
                        seed_scatter(axes[1],range(3),[value(rr,f,art,page,'proportions') for f in ['p_plus','p_minus','p_zero']],j)
                    axes[0].set_xticks(range(2),['A_plus','A_minus']);style(axes[0],'Saved part magnitude')
                    axes[1].set_xticks(range(3),['p_plus','p_minus','p_zero']);style(axes[1],'Saved proportion')
                    axes[0].set_title('Positive part / negative-part magnitude');axes[1].set_title('Positive / negative / exact-zero proportions')
                    fig.text(.09,.10,'Original integer counts/nulls are retained in the complete table; aggregate proportions are not unique biological-beneficiary fractions.',fontsize=8.6)
                else:
                    axes=fig.subplots(1,3);fig.subplots_adjust(left=.075,right=.98,bottom=.20,top=.79,wspace=.30)
                    delta=['delta_CI','delta_J30_minusI_aff','delta_J30_minusI_1d']
                    for j,seed in enumerate(SEEDS30):
                        seed_scatter(axes[0],range(3),[value(c03('C03_REFERENCE','/risk_difference_views',arm,seed,ep,contrast_id=c),'difference',art,page,'reference_risk_differences') for c in delta],j)
                        seed_scatter(axes[1],range(2),[value(c03('C03_REFERENCE','/M_I',arm,seed,ep,p),'value',art,page,'FR30_M_I') for p in POL],j)
                        seed_scatter(axes[2],[0],[value(c03('C03_REFERENCE','/D_ref',arm,seed,ep),'value',art,page,'FR30_D_ref')],j)
                    axes[0].set_xticks(range(3),['delta_CI','delta_resid_aff','delta_resid_1d'],rotation=12);style(axes[0],'Sensitivity minus main')
                    axes[1].set_xticks(range(2),['row-uniform','protein-input']);style(axes[1],'FR30 saved M_I')
                    axes[2].set_xticks([0],['original policy pair']);style(axes[2],'FR30 saved D_ref')
                    for ax,title in zip(axes,['Risk-difference sensitivity','Before-label amplitude','Before-label reference distance']):ax.set_title(title,fontsize=9)
                    fig.text(.075,.10,'FR30 value fields remain distinct from the FR20 semantic D_ref view. Native policy order and sensitivity-minus-main direction retained.',fontsize=8.5)
                finish(fig,art,page,pdf)
    assert page==36
    ARTIFACTS.append({'artifact_id':'S3.Figures','path':path,'page_count':page,'claim_ids':['C01','C03'],
      'caption':'Whole-GATE four risks/J by arm, seed and endpoint; O_SRC five risks and all five saved contrasts, original positive/negative/exact-zero composition, and original FR30 reference views/M_I/D_ref. Complete S, endpoints, seed layers, references and native null/inapplicable counts remain in companion tables. No endpoint MEAN5 is synthesized for C01; no source membership lists or new loss differences are reconstructed.'})

CSS='''body{font:14px/1.55 system-ui,sans-serif;color:#1d2935;margin:28px;max-width:1650px}h1{font-size:25px}h2{font-size:19px}a{color:#006d9f}table{border-collapse:collapse;font-size:12px;width:100%}th,td{padding:7px 9px;text-align:left;border-bottom:1px solid #d8e0e5;vertical-align:top;white-space:nowrap}th{position:sticky;top:0;background:#edf3f7}code,.num{font-family:ui-monospace,SFMono-Regular,monospace}.num{cursor:pointer;font-size:11px}.num:hover{background:#e9f7ff}.null{background:#fff2d0;color:#6b4f10}.missing{background:#f0f0f0;color:#666}.scroll{overflow:auto;max-height:72vh;border:1px solid #c7d4de}pre{white-space:pre-wrap;word-break:break-word}select,button{padding:6px;margin:5px}dialog{max-width:85vw;max-height:85vh;border:1px solid #94a8b6;border-radius:6px}.note{padding:12px 16px;background:#f2f6f8;border-left:3px solid #648aa3}img{max-width:100%;height:auto}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}.card{padding:16px;border:1px solid #cdd9e1}'''
def shell(title,body,js=''):
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+CSS+'</style><body>'+body+('<script>'+js+'</script>' if js else '')+'</body></html>\n'

def complete_tables():
    tableindex=[];total=0
    for sid,rr in ROWS.items():
        path=TAB+'/'+sid+'_complete_v0_1.html';recordpath=TAB+'/'+sid+'_rows_v0_1.jsonl'
        records=[]
        for r in rr:
            records.append({**{k:v for k,v in r.items() if k not in ['values','metadata_cells','structural_absences']},
              'values':{f:clean(c) for f,c in r['values'].items()},
              'metadata_cells':{f:clean(c) for f,c in r['metadata_cells'].items()},
              'structural_absences':{f:clean(c) for f,c in r.get('structural_absences',{}).items()}})
        axes=list(dict.fromkeys(a for r in rr for a in r['native_axes']))
        cols=list(dict.fromkeys(f for r in rr for f in r['values']))
        count=sum(len(r['values']) for r in rr);total+=count
        body='<h1>'+esc(('S2' if sid.startswith('C02') else 'S3')+'：'+sid+' 完整原聚合表')+'</h1>'
        body+='<p>全部 '+str(len(rr))+' 条原记录／'+str(count)+' 个结果字段；点击数值查看原文件、原字段/行、原token、原状态及18项引用信息。</p>'
        body+='<p class="note">P_GATE与P_OSRC、J与J30、幅度M/U与风险差C_I分别保留。null不替换为0；原行缺失status不补写PASS。符号迁移数组只用原位置0—8，未确认的符号对标签不猜测。数据完整嵌入本地页面，无外部请求。</p>'
        body+='<div id="filters"></div><p id="summary"></p><button id="prev">上一页</button><button id="next">下一页</button><span id="page"></span><div class="scroll"><table><thead id="head"></thead><tbody id="rows"></tbody></table></div><dialog id="detail"><button onclick="this.parentElement.close()">关闭</button><pre id="detailtext"></pre></dialog>'
        payload=json.dumps({'source':SOURCES[sid],'common':LEDGER['common_citation_context'],'axes':axes,'columns':cols,'rows':records},ensure_ascii=False,separators=(',',':')).replace('</','<\\/')
        js='const D='+payload+';\n'+r'''
const $=x=>document.getElementById(x),h=x=>String(x).replace(/[&<>"']/g,z=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[z]));let page=0,chosen={},filtered=D.rows;const size=50;
for(const a of ['container',...D.axes]){const s=document.createElement('select');s.title=a;const values=[...new Set(D.rows.map(r=>a==='container'?r.container:String(r.native_axes[a]??'')))];s.innerHTML='<option value="">'+h(a)+': 全部</option>'+values.map(v=>'<option>'+h(v)+'</option>').join('');s.onchange=()=>{chosen[a]=s.value;page=0;update()};$('filters').appendChild(s)}
$('head').innerHTML='<tr>'+['原对象/数组位置',...D.axes,...D.columns,'原元数据/资格/结构缺失'].map(x=>'<th>'+h(x)+'</th>').join('')+'</tr>';
function inspect(r,c){const resolved=c?{...D.common,inherited_source_path:D.source.inherited_source_path,source_sha256:D.source.source_sha256,...r.citation_context,...c.citation_context}:null;$('detailtext').textContent=JSON.stringify({source:D.source,row:r.row_id,original_locator:r.original_row_locator,native_axes:r.native_axes,cell:c,resolved_18_field_citation:resolved,original_metadata:r.metadata_cells,qualification:r.qualification,structural_absences:r.structural_absences},null,2);$('detail').showModal()}
function update(){filtered=D.rows.filter(r=>Object.entries(chosen).every(([k,v])=>!v||String(k==='container'?r.container:r.native_axes[k]??'')===v));$('summary').textContent='当前 '+filtered.length+' / '+D.rows.length+' 条原记录；每页50行；各向量位置不赋予未确认标签。';$('rows').textContent='';for(const r of filtered.slice(page*size,(page+1)*size)){const tr=document.createElement('tr'),add=(s,cls='')=>{const t=document.createElement('td');t.textContent=s;t.className=cls;tr.appendChild(t);return t};add(r.original_row_locator.json_pointer);for(const a of D.axes)add(r.native_axes[a]??'此容器无该轴');for(const f of D.columns){const c=r.values[f];if(!c){add('该原行未保存此字段','missing');continue}const td=add(c.raw_json===null?c.state:c.raw_json,'num'+(c.state==='SAVED_NULL'?' null':''));td.onclick=()=>inspect(r,c)}const meta=add('查看原状态/分母/null/引用','num');meta.onclick=()=>inspect(r,null);$('rows').appendChild(tr)}$('page').textContent='第 '+(page+1)+' / '+Math.max(1,Math.ceil(filtered.length/size))+' 页';$('prev').disabled=page===0;$('next').disabled=(page+1)*size>=filtered.length}
$('prev').onclick=()=>{page--;update()};$('next').onclick=()=>{page++;update()};update();
'''
        textfile(path,shell(sid+' 完整原表',body,js));lines(recordpath,records)
        tableindex.append({'source_id':sid,'artifact_id':('S2.Table.' if sid.startswith('C02') else 'S3.Table.')+sid,
          'html':path,'row_registry':recordpath,'row_count':len(rr),'result_fields':count,
          'source_ledger':W02+'/ledger.json','all_native_null_status_and_metadata_retained':True})
    assert total==18482
    save(AUD+'/complete_table_coverage.json',{'tables':tableindex,'row_count':3469,'result_fields':total,
      'non_null_numbers':18066,'saved_nulls':416,'new_or_imputed_numeric_values':0,
      'array_position_labels':'Native 0..8 only; no guessed sign-pair mapping'})
    return tableindex

def main_tables():
    blocks=[];tablecitations=[]
    r=row('C01_SUMMARY','/summary',arm='G',seed_layer='MEAN5',endpoint_layer='KD_KI_EQUAL')
    blocks.append(('Table2.GATE','P_GATE | all 5,697 GATE rows | no four-corner policy axis',
      [(f,'L('+('h_aff' if f=='J_aff' else 'h_1d')+') - L(h1)',r,f) for f in ['J_aff','J_1d']],
      'Original fields: CI=null; p=null; practical_margin=null; scientific_PASS=false.'))
    defs={'J30_aff':'L(h_aff) - L(h1)','J30_1d':'L(h_1d) - L(h1)',
          'C_I':'L(h1_minus_bar_qI) - L(h1)',
          'J30_minusI_aff':'L(h_aff) - L(h1_minus_bar_qI)',
          'J30_minusI_1d':'L(h_1d) - L(h1_minus_bar_qI)'}
    blocks.append(('Table2.OSRC','P_OSRC | 2,242 sources / 322,659 links | full FIT_ROW_UNIFORM',
      [(c,defs[c],c03('C03_CONTRASTS','/rows','G','MEAN5','KD_KI_EQUAL',POL[0],contrast_id=c),'difference') for c in CONTRASTS],
      'Original fields: practical_margin=null; scientific_practical_PASS=false.'))
    path=FIG+'/Table2_GATE_OSRC_v0_1.pdf'
    with PdfPages(ROOT/path,metadata={'Title':'Table2: separate GATE and O_SRC blocks','Author':''}) as pdf:
        for page,(art,scope,items,note) in enumerate(blocks,1):
            body='<h1>'+art+'</h1><p>'+esc(scope)+'</p><p>G；原保存MEAN5、KD_KI_EQUAL。原值不舍入，不从风险重算差值。</p><table><thead><tr><th>原量</th><th>原定义</th><th>原保存值</th><th>可追溯引用</th></tr></thead><tbody>'
            data=[]
            for name,definition,rr,f in items:
                c=rr['values'][f];ref=citation(rr,f,art,page,'table');tablecitations.append(ref)
                body+='<tr><th>'+esc(name)+'</th><td>'+esc(definition)+'</td><td class="num">'+esc(c['raw_json'])+'</td><td><details><summary>'+esc(c['citation_id'])+'</summary><pre>'+esc(json.dumps(ref,ensure_ascii=False,indent=2))+'</pre></details></td></tr>'
                data.append([name,definition,c['raw_json']])
            body+='</tbody></table><p class="note">'+esc(note)+' 原有限开发性范围不升级。完整S/seed/端点/参考与风险向量见S3。全GATE J、O_SRC J30和C_I不混用，不生成收益解释比例。</p>'
            textfile(TAB+'/'+art.replace('.','_')+'_v0_1.html',shell(art,body))
            fig,ax=plt.subplots(figsize=(10.8,4.3));ax.axis('off')
            fig.text(.07,.91,art+' | Original saved values',fontweight='bold',fontsize=14)
            fig.text(.07,.83,scope,fontsize=9);fig.text(.07,.77,'G | saved MEAN5 | Kd/Ki equal | exact original JSON tokens',fontsize=9)
            table=ax.table(cellText=data,colLabels=['Saved quantity','Definition','Exact saved value'],cellLoc='left',colLoc='left',
              colWidths=[.19,.50,.31],bbox=[.02,.23,.96,.36 if len(items)==2 else .48])
            table.auto_set_font_size(False);table.set_fontsize(9)
            for (a,b),cell in table.get_celld().items():
                cell.set_edgecolor('#d7e0e5');cell.set_linewidth(.5)
                if a==0:cell.set_facecolor('#edf3f7');cell.set_text_props(fontweight='bold')
            fig.text(.07,.13,note,fontsize=8.3)
            fig.text(.07,.075,'All displayed contrasts use saved fields. GATE J and O_SRC J30 / C_I have different supports; no ratio or recomputation.',fontsize=8)
            finish(fig,art,page,pdf)
    save(REF+'/Table2_GATE_OSRC_citations.json',{'schema':'manuscript-B3-W03-main-table-citations/0.1',
      'pdf':path,'entries':tablecitations,'exact_tokens_unrounded':True,'scientific_arithmetic':False})
    ARTIFACTS.append({'artifact_id':'Table2.GATE_OSRC','path':path,'page_count':2,'claim_ids':['C01','C03'],
      'html_blocks':[TAB+'/Table2_GATE_v0_1.html',TAB+'/Table2_OSRC_v0_1.html'],
      'caption':'Separate whole-GATE dual-J and O_SRC five-contrast blocks, using only original saved fields. G/MEAN5/KD_KI_EQUAL; no four-corner reference axis for GATE; full row-uniform policy for O_SRC. Exact JSON tokens are shown. Original false flags/null margins retained; no new risk subtraction or cross-panel gain ratio. The existing Table2.PATH block is unchanged.'})

def qualifications():
    controls=loadjson(W02+'/source_controls.json')
    original=loadjson('audit/w02_b3_v0_1/field_presence_and_qualification.json')
    body='<h1>S2 / S3：既有资格、原null与不可判范围</h1><p class="note">有限描述性接受不等于科学/实用PASS。原未保存字段不补写false、null或PASS。MODEL_GATE是开发暴露；五seed与参考不是独立生物样本。</p>'
    body+='<h2>原阶段接受与行级状态</h2>'
    for sid,s in SOURCES.items():
        states=Counter(r['qualification']['row_status_field_state'] for r in ROWS[sid])
        body+='<h3>'+sid+'</h3><pre>'+esc(json.dumps({'original_final_qualification_refs':s['qualification_refs'],'row_status_field_presence':dict(states)},ensure_ascii=False,indent=2))+'</pre>'
    body+='<h2>原结果表控制字段</h2><pre>'+esc(json.dumps(controls,ensure_ascii=False,indent=2))+'</pre>'
    body+='<h2>null、缺失、符号位置</h2><p>288个FR20 transition_counts父字段null及128个FR30汇总计数字段null保持。符号迁移数组只显示原位置0—8，不生成带符号对标签的三乘三矩阵。原严格翻转比例是独立已保存字段。</p><pre>'+esc(json.dumps(original,ensure_ascii=False,indent=2))+'</pre>'
    textfile(TAB+'/S2_S3_qualification_v0_1.html',shell('S2 / S3 原资格与null',body))

def write_index(tableindex):
    body='<h1>B3 W03：Fig.2、Table2.GATE / OSRC 与 S2 / S3</h1><p>按v0.4方案及B3 W02已核对聚合值制作。全部为原保存值的显示；未写正文，未读取样本载荷或补算。</p><div class="note">全GATE与O_SRC、J与J30、M/U与C_I分别呈现。null保持原状态。九位符号迁移向量只按原位置登记，未猜测符号对标签。</div>'
    body+='<h2>主图</h2><a href="Fig2_main_v0_1.pdf">打开主图矢量PDF</a><p><img src="Fig2_main_v0_1.png" alt="Fig.2A-D"></p><div class="cards">'
    for a in ARTIFACTS:
        body+='<div class="card"><h3>'+esc(a['artifact_id'])+'</h3><a href="'+Path(a['path']).name+'">PDF：'+str(a['page_count'])+'页</a><p>'+esc(a['caption'])+'</p></div>'
    body+='</div><h2>主表与全部补充原值</h2><ul>'
    for p,label in [(TAB+'/Table2_GATE_v0_1.html','Table2.GATE'),(TAB+'/Table2_OSRC_v0_1.html','Table2.OSRC'),(TAB+'/S2_S3_qualification_v0_1.html','S2/S3 原资格、null和不可判')]+[(x['html'],x['artifact_id']) for x in tableindex]:
        body+='<li><a href="../../'+p+'">'+esc(label)+'</a></li>'
    body+='</ul><p>完整原值与引用无需联网；表格支持原生轴筛选。主图显示舍入仅用于阅读，主表和完整表显示原token。</p>'
    textfile(FIG+'/index_v0_1.html',shell('B3 W03 图表索引',body))
    transform={'script':'scripts/render_w03_b3_v0_1.py','sha256':sha((ROOT/'scripts/render_w03_b3_v0_1.py').read_bytes()),
      'kind':'PRESENTATION_ONLY','scientific_arithmetic':False}
    save(REF+'/figure_citation_index.json',{'schema':'manuscript-B3-W03-figure-table-index/0.1',
      'planning_version':'v0.4','batch_id':'B3','W02_ledger':W02+'/ledger.json',
      'W02_ledger_sha256':sha((ROOT/W02/'ledger.json').read_bytes()),'artifacts':ARTIFACTS,
      'complete_tables':tableindex,'point_citations':REF+'/plot_point_citations.jsonl',
      'main_table_citations':REF+'/Table2_GATE_OSRC_citations.json',
      'qualification_table':TAB+'/S2_S3_qualification_v0_1.html','gallery':FIG+'/index_v0_1.html',
      'transformation':transform,'original_nulls_and_statuses_retained':True,
      'no_guessed_transition_sign_labels':True,'manuscript_body_written':False,'old_artifacts_changed':False})

def verify_trace():
    for p in TRACE:
        r,f,c=BYCID[p['W02_citation_id']]
        assert p['original_raw_json']==c['raw_json'] and p['native_axes']==r['native_axes']
        assert p['original_cell_state']==c['state']
        assert all(k in p['resolved_citation'] for k in LEDGER['required_fields'])
    save(AUD+'/render_verification.json',{'schema':'manuscript-B3-W03-render-check/0.1',
      'plot_cell_occurrences':len(TRACE),'by_artifact':dict(Counter(p['artifact_id'] for p in TRACE)),
      'all_plotted_values_bound_to_original_W02_cells':True,'new_scientific_arithmetic':False,
      'nulls_imputed':0,'text_outside_canvas_count':sum(len(x['text_outside_canvas']) for x in LAYOUT),
      'layout_checks':LAYOUT,'original_scientific_qualification_changed':False})
    lines(REF+'/plot_point_citations.jsonl',TRACE)

def main():
    assert not (ROOT/AUD/'receipt.json').exists(),'Use a new version after completed delivery'
    for p in [FIG,TAB,REF,AUD]:(ROOT/p).mkdir(parents=True,exist_ok=True)
    read_ledger();main_figures();print('Main Fig.2 exported.',flush=True)
    main_tables();print('Table2 blocks exported.',flush=True)
    s2_figures();print('S2 figures: 42 pages.',flush=True)
    s3_figures();print('S3 figures: 36 pages.',flush=True)
    tableindex=complete_tables();qualifications();write_index(tableindex);verify_trace()
    save(AUD+'/runtime_versions.json',{'python':__import__('sys').executable,'matplotlib':matplotlib.__version__,
      'pypdf':__import__('pypdf').__version__,'Pillow':__import__('PIL').__version__,
      'purpose':'Own writing-only plotting runtime; no inherited scientific environment or research script'})
    print('Complete tables: 3469 rows, 18482 result fields; render and source-reference registries written.',flush=True)

if __name__=='__main__':main()
