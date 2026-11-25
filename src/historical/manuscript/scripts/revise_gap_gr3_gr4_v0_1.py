"""GR3/GR4 authoring only: static draft, existing vector plots, DOCX and navigation.
Never imports research producers or opens model, row, checkpoint or array targets.
"""
from pathlib import Path
import ast, copy, datetime, difflib, hashlib, html, io, json, os, re, shutil, subprocess, sys, zipfile
from collections import Counter

R=Path('historical_sources/DTI_Manuscript')
PK=R/'output/revision_gap_v0_1'
F=R/'output/revision_batch_f_v0_1'
A=R/'audit/gap_remediation_gr3_gr4_v0_1'
P=R/'planning/gap_remediation_gr3_gr4_v0_1'
RF=R/'references/gap_remediation_gr3_gr4_v0_1'
D=R/'drafts/full_manuscript_v0_3'
MAIN=D/'manuscript_v0_3_gr3_gr4_v0_1.md'
SUP=D/'supplement_v0_3_gr3_gr4_v0_1.md'
RT=Path('historical_sources/.cache/codex-runtimes/codex-primary-runtime/dependencies')
PY=RT/'python/bin/python3'
BIN=RT/'bin/override'
LO=RT/'native/libreoffice-headless/libreoffice/LibreOfficeDev.app/Contents/MacOS/soffice'
RENDER=Path('historical_sources/.codex/plugins/cache/openai-primary-runtime/documents/26.1017.22136/skills/documents/render_docx.py')

def sha(b): return hashlib.sha256(b).hexdigest()
def read(p):return p.read_text()
def load(p):return json.loads(read(p))
def desc(p):
    h=hashlib.sha256();n=0
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b);n+=len(b)
    return {'path':str(p.relative_to(R)),'bytes':n,'sha256':h.hexdigest()}
def put(p,text):
    p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists():
        old=p.read_text()
        if old==text:return
        patch='*** Begin Patch\n*** Update File: '+str(p)+'\n'
        for l in list(difflib.unified_diff(old.splitlines(True),text.splitlines(True),n=2))[2:]:
            patch+=('@@\n' if l.startswith('@@') else l.rstrip('\n')+'\n')
        patch+='*** End Patch\n'
    else:patch='*** Begin Patch\n*** Add File: '+str(p)+'\n'+''.join('+'+x+'\n' for x in text.splitlines())+'*** End Patch\n'
    subprocess.run([shutil.which('apply_patch')],input=patch,text=True,cwd=R,capture_output=True,check=True)
def save(p,obj):put(p,json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def anchored(text,aid):
    start=text.index('<a id="'+aid+'"></a>');m=re.search(r'<a id="',text[start+1:])
    end=start+1+m.start() if m else len(text)
    return text[start:end]
def replace_paragraph(text,aid,new,ops,task):
    old=anchored(text,aid);lines=old.splitlines();start=next(i for i,x in enumerate(lines[1:],1) if x.strip() and not x.startswith('#'))
    end=start+1
    while end<len(lines) and lines[end].strip():end+=1
    before='\n'.join(lines[start:end]);after=old.replace(before,new,1)
    ops.append({'anchor':aid,'task':task,'old_block_sha256':sha(old.encode()),'new_block_sha256':sha(after.encode()),'old_paragraph':before,'new_paragraph':new})
    return text.replace(old,after,1)

def prepare():
    assert not PK.exists() and not MAIN.exists() and not SUP.exists(),'New output version required'
    for q in [A,P,RF,PK]:q.mkdir(parents=True,exist_ok=True)
    parentseal=R/'audit/gap_remediation_gr1_gr2_v0_1/run_seal.json'
    for x in load(parentseal)['artifacts']:
        assert desc(R/x['path'])==x,x['path']
    baseline=[desc(parentseal),desc(R/'audit/gap_remediation_gr1_gr2_v0_1/final_verification.json'),desc(F/'package_manifest.json')]
    for name in ['manuscript_v0_3.md','supplement_v0_3.md']:baseline.append(desc(D/name))
    baseline+=load(parentseal)['artifacts']
    save(A/'baseline.json',{'scope':'GR1/GR2 sealed writing and F manifest; no old science-target traversal','records':baseline})
    # Reuse the accepted static F package opaquely. Do not parse the 21 result tables.
    skipped={'manuscript.md','supplement.md','manuscript.html','supplement.html','index.html','package_manifest.json','README_PRIVATE.md','revision_report.md','main_figures.pdf','supplement_figures.pdf','manuscript_v0_2.docx','manuscript_v0_2.pdf','supplement_v0_2.docx','supplement_v0_2.pdf'}
    reused=[]
    for x in load(F/'package_manifest.json')['files']:
        rel=x['path']
        if rel in skipped:continue
        dst=PK/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(F/rel,dst)
        now=desc(dst);assert now['bytes']==x['bytes'] and now['sha256']==x['sha256'],rel
        reused.append({'source':str((F/rel).relative_to(R)),**now})
    save(A/'opaque_static_asset_reuse.json',{'records':reused,'science_table_values_interpreted':False,'sample_payloads_copied':False,'source':'Accepted F writing package only; no original source tree scan'})
    main=read(D/'manuscript_v0_3.md');sup=read(D/'supplement_v0_3.md');ops=[]
    main=re.sub(r'\*\*Private manuscript v0\.3, GR1/GR2.*?\*\*','**Private manuscript v0.3. Author details, target journal and submission declarations remain pending confirmation.**',main,count=1)
    sup=sup.replace('# Supplementary material v0.3 — GR1/GR2 stage','# Supplementary material v0.3')
    replacements={
      'abs-p01':r'''Predictive improvement after readout refitting does not determine whether a local gradient can anticipate finite responses. We studied SMILES and graph affinity pipelines while holding each case's drug and protein representations fixed. A newly fitted readout \(h_1\) was compared with two FIT-only scalar calibration procedures; its change from affine calibration was \(q=h_1-h_{\mathrm{aff}}\). Finite-response approximation value was signed \(\operatorname{Skill}=B-E\), comparing first-order error \(E\) with zero-response error \(B\). On an exposed development panel of 5,697 label groups, the graph readout reduced five-seed, equal-endpoint mean absolute error beyond the calibrators by 0.03318 and 0.03244. For complete drug, protein and joint representation replacements, the graph \(q\) approximation outperformed a fixed coordinate permutation but underperformed zero response. Direction errors and harmful overshoot contributed to these losses. On 32 fixed sources and 64 links, aggregate \(\operatorname{Skill}(q)\) was positive at the four shortest measured spans and negative at the complete displacement under the main restricted reference. The predictor's own signed Skill and its paired advantage over a comparator gave distinct judgments. On complete observed-source support, positive deletion gains for reference-composed \(\bar q_I\) coexisted with negative Skill for the entire \(q\). These comparisons connect a predictively relevant update to the span and function boundaries of local response approximation in frozen latent coordinates.''',
      'intro-p01':r'''A local explanation used to anticipate a finite model response has a concrete benchmark: it should reduce error relative to predicting no response. For a gradient projection, that judgment depends on the function, operation and displacement. Here the operations replace or interpolate drug and protein representation blocks while the trained encoders remain fixed. They probe model responses in latent coordinates, rather than measured chemical or biological interventions. A reduction in affinity prediction error motivates studying a readout, but leaves its response approximation to be tested against the response magnitude on the same links.''',
      'intro-p07':r'''The study asks where a fixed source-point gradient has positive finite-response approximation value in a predictively relevant update. Fixed-link span comparisons and function-self versus paired Skill identify the relevant boundaries. Complete-operation controls and diagnostic accounts then describe how the approximation loses value, while anchored deletion connects the change function to label loss without identifying a causal mechanism. These are complementary uses of the same frozen readout case; each retains its own function, support and reference.''',
      'd43-p01':r'''The empirical contribution is the connection between a predictively relevant frozen-readout change and explicit finite-response benchmarks. The same-link span audit complements path-integrated attribution and Taylor-based explanation methods ([Sundararajan et al., 2017](#ref-sundararajan2017axiomatic); [Ortigossa et al., 2025](#ref-ortigossa2025texplainer)); the absolute-error comparison with zero response complements perturbation-based fidelity evaluation ([Yeh et al., 2019](#ref-yeh2019infidelity)). Together they expose differences between explanation objects, baselines and measured spans. The study does not rank those explanation algorithms.''',
      'conclusion-p01':r'''In this exposed development case, a predictively useful graph-readout update had positive aggregate local-response approximation value at shorter measured spans and negative value at complete representation replacements. Improvement over a fixed permutation did not imply improvement over zero response. The updated predictor's own signed Skill and its paired advantage over a comparator also gave different judgments. Anchored deletion linked the update to label loss, while positive deletion gains and negative Skill for the entire change function could coexist.''',
      'conclusion-p02':r'''Finite-response evaluation should specify the function, support, reference, operation and measured span, and report approximation error beside zero-response error. Component amplitude, deletion gain, function-self Skill and paired Skill differences serve distinct purposes. The present results support this evaluation practice within frozen latent coordinates; independent predictive transfer, explanation-span transfer and explanation-guided decision benefit remain separate validation questions.'''
    }
    for aid,new in replacements.items():main=replace_paragraph(main,aid,new,ops,'W06')
    # GR2's static semantic closure is propagated to all remaining current text.
    main=main.replace('together with unresolved checks and unconfirmed positional labels, retain their documented meanings (S8).','together with unresolved checks and producer-bound positional definitions, retain their documented meanings (S8).')
    main=main.replace('Unverified sign-transition array labels remain native positions in the complete tables.','Native sign-transition positions remain unchanged; their producer-bound definitions are given in S8.5.1.')
    sup=sup.replace('The nine-position reference sign-transition vector remains in its native order because its sign-pair labels are unconfirmed.','The nine-position reference sign-transition vector keeps its original order; its producer-bound sign-pair definitions are in S8.5.1.')
    # SI deduplication: keep the first precise class definition and consolidate unique qualifications.
    b=anchored(sup,'s5-reused-methods')
    oldparas=b.split('\n\n')
    dup=next(x for x in oldparas if x.startswith('The eight classes retain exact'))
    source=next(x for x in oldparas if x.startswith('Source g belongs'))
    first=next(x for x in oldparas if x.startswith('The completed diagnostic'))
    firstnew=first.replace('and mass differs from harm magnitude (S5).','and mass differs from harm magnitude.') if 'and mass differs' in first else first
    firstnew=firstnew.replace('Class mass M differs from component amplitude \\(M_I\\), and mass differs from harm magnitude (S5).','Class mass M differs from component amplitude \\(M_I\\); M, P/H and net contribution N are separate, and N is not an endpoint count.')
    if 'N is not an endpoint count' not in firstnew:firstnew+=' Net contribution N is not an endpoint count. Class mass and helpful/harmful magnitudes remain separate.'
    newb=b.replace(first,firstnew).replace(dup+'\n\n','')
    join=next(x for x in oldparas if x.startswith('The source-level join'))
    joinnew=join+' The g grouping has no operation axis. Conditional means retain their original cell normalization. The unchanged native positions of harm_proportions and class_MPH are defined from the bound producer in S8.5.1.'
    newb=newb.replace(join,joinnew).replace(source+'\n\n','')
    ops.append({'anchor':'s5-reused-methods','task':'W05','old_block_sha256':sha(b.encode()),'new_block_sha256':sha(newb.encode()),'unique_information_retained':['Exact C0-C7 rules','r/A zeros and neutral boundary','saved versus diagnostic residuals','M/P/H/N distinction','g versus whole-q Skill objects','g has no operation axis','cell normalization','native arrays and static semantics','paired-account and causal limits']})
    sup=sup.replace(b,newb,1)
    # Consolidate S2 component/amplitude versus S3 risk definitions; move exact formulas, do not delete them.
    b=anchored(sup,'s2-reused-methods')
    st=b.index('For \\(h_{-I,i}');en=b.index('\\(M_I\\) averages',st)
    moved=b[st:en];newb=b[:st]+b[en:]
    sup=sup.replace(b,newb,1)
    ins=sup.index('<a id="fig-s3a"></a>')
    addition='<a id="s3-anchored-loss"></a>\n### S3.4 Anchored deletion on identical support\n\n'+moved
    sup=sup[:ins]+addition+sup[ins:]
    ops.append({'anchor':'s2-reused-methods','task':'W05','old_block_sha256':sha(b.encode()),'new_block_sha256':sha(newb.encode()),'exact_information_moved_to':'s3-anchored-loss','moved_text_sha256':sha(moved.encode())})
    sup=sup.replace('The completed diagnostic reused saved responses','The completed diagnostic reused saved responses',1)
    b=anchored(sup,'s7-reused-methods');old='Increment cancellation, projected-area cancellation and cancellation between the readouts forming q remained separate; signed Q32−Q16 and r(1)−Q32 described saved discretization differences (S7). '
    assert old in b;newb=b.replace(old,'The three cancellation accounts and signed discretization differences are defined in S7.2. ')
    sup=sup.replace(b,newb,1);ops.append({'anchor':'s7-reused-methods','task':'W05','old_block_sha256':sha(b.encode()),'new_block_sha256':sha(newb.encode()),'unique_information_destination':'S7.2 retains all three accounts and signed Q32−Q16/r(1)−Q32'})
    # Move process-only provenance out of the scientific Methods; the source report stays in the package.
    old='This revision checked the 6,904-byte settings control against its sealed SHA256, without opening the referenced HiGHS binary or calibration coefficients and without running a solver. The exact settings, source locations and superseded-spec fields are registered in the GR1/GR2 method dossier.'
    assert old in sup;sup=sup.replace(old,'The exact settings, source locations and superseded-spec fields are registered in the linked method dossier.')
    ops.append({'anchor':'s3-runtime-completion','task':'W05','private_process_text_moved_to':'provenance/gr1_gr2/ and GR3/GR4 migration register','old_text':old,'scientific_method_removed':False})
    # Main layouts: A + B1/B2 on one plate, full B and C retained as explicit SI views.
    old=anchored(main,'fig-3')
    label='**A**, approximation error E, zero-response error B and fixed-permutation error for q on complete O_SRC, together with its signed Skill. **B**, helpful/harmful contributions and named direction-error, overshoot and false-response harm. The complete diagnostic view, including saved-versus-diagnostic Skill and all class masses, is preserved in Fig. S6G; the source-level g×Skill coexistence view is Fig. S7G. g concerns bar_qI deletion, whereas Skill concerns the entire CPU-float64 q. G, original five-seed/equal-endpoint summaries and the full row-uniform reference are retained. Full arrays and nulls remain unchanged; co-occurrence is not causal mediation.'
    new='<a id="fig-3"></a>\n### Figure 3. Complete-operation controls and harm\n\n'+label+'\n\n[Vector Figure 3](figures/Fig3.pdf)\n\n![Fig.3](figures/current/Fig3.png)\n\n'
    main=main.replace(old,new,1)
    # The unique lower/paired h1 view is brought beside q span results.
    old=anchored(main,'fig-4')
    label='**A**, q\'s signed Skill across all six measured spans. **B**, upper: paired ΔSkill of h1 versus h0 or h_aff; lower: h1\'s own signed Skill only. Corresponding E/B and E/λ/Skill/λ views are retained in Fig. S9D and S9C. The main view is G under the restricted row-uniform reference, with original five-seed/equal-endpoint aggregation. All other functions and layers remain in S6. Markers are saved measurements; lines do not establish an interpolated threshold or a certified radius.'
    new='<a id="fig-4"></a>\n### Figure 4. Measured span and function dependence\n\n'+label+'\n\n[Vector Figure 4](figures/Fig4.pdf)\n\n![Fig.4](figures/current/Fig4.png)\n\n'
    main=main.replace(old,new,1)
    # Only figure/navigation labels change in Results; every observed numerical token is preserved.
    main=main.replace('Fig. 3C','Fig. S7G').replace('Fig. 3B','Fig. 3B; full view in Fig. S6G')
    main=main.replace('Fig. 4B','Fig. S9D').replace('Fig. 4C','Fig. 4B')
    main=main.replace('figures/main_figures/page-05.png','supplement_v0_3.md#fig-s7g')
    main=main.replace('figures/main_figures/page-07.png','supplement_v0_3.md#fig-s9d')
    main=main.replace('figures/main_figures/page-08.png','figures/current/Fig4.png')
    # Three complete legacy views are added to SI; no plotted item is discarded.
    additions=[('fig-s6g','Figure S6G. Complete diagnostic view retained from main Figure 3B','main',4,
                'Original complete O_SRC G/q diagnostic view: helpful/harmful contributions, named harm, diagnostic versus saved Skill and all C0–C7 class masses. The upper two accounts appear in main Fig. 3B. No value, zero or class is removed from this complete view.'),
               ('fig-s7g','Figure S7G. Source-level deletion gain and approximation coexistence','main',5,
                'Original main Figure 3C, complete O_SRC, G and original seed/endpoint/reference layers. Gain g concerns bar_qI deletion; Skill_diag concerns the entire q. Zero cells, full-denominator contributions and conditional nulls retain their original meanings. This is not a causal mediation table.'),
               ('fig-s9d','Figure S9D. Span-specific approximation error and zero-response error','main',7,
                'Original main Figure 4B on the fixed 32-source/64-link panel. G/q, all six spans, D/P/DP, original five-seed/equal-endpoint summaries and restricted row-uniform reference. This full E/B view complements main Figure 4; it is not the complete O_SRC mean.')]
    for aid,title,kind,page,caption in additions:
        marker={'fig-s6g':'<a id="fig-s7a"></a>','fig-s7g':'## S6.','fig-s9d':'## S7.'}[aid]
        idx=sup.index(marker)
        block='<a id="'+aid+'"></a>\n### '+title+'\n\n![Fig.'+aid[4:].upper()+'](figures/current/'+aid+'.png)\n\n'+caption+'\n\n'
        sup=sup[:idx]+block+sup[idx:]
    def portable(text):
        text=text.replace('../../output/revision_batch_f_v0_1/','')
        text=text.replace('../../references/gap_remediation_gr1_gr2_v0_1/','provenance/gr1_gr2/references/')
        # A move may have left a parent-root prefix before an intra-package pointer.
        return text.replace('supplement_v0_3.md','supplement.md').replace('manuscript_v0_3.md','manuscript.md')
    main,sup=portable(main),portable(sup)
    put(PK/'manuscript.md',main);put(PK/'supplement.md',sup)
    # Draft sources use the same content, with local package assets rebased explicitly.
    def project_version(text):
        def link(m):
            t=m.group(1)
            if t.startswith(('#','http:','https:','mailto:')):return m.group(0)
            if t.startswith('supplement.md'):t=t.replace('supplement.md',SUP.name,1)
            elif t.startswith('manuscript.md'):t=t.replace('manuscript.md',MAIN.name,1)
            else:t='../../output/revision_gap_v0_1/'+t
            return ']('+t+')'
        return re.sub(r'\]\(([^)]+)\)',link,text)
    put(MAIN,project_version(main));put(SUP,project_version(sup))
    # Reuse source/method overlays from the sealed GR1/GR2 stage, not their code execution.
    for oldroot,rel in [(R/'references/gap_remediation_gr1_gr2_v0_1','references'),(R/'planning/gap_remediation_gr1_gr2_v0_1','planning')]:
        for source in oldroot.rglob('*'):
            if source.is_file():
                dst=PK/'provenance/gr1_gr2'/rel/source.relative_to(oldroot);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dst)
    save(P/'editorial_patch_and_migration.json',{'schema':'gr3-gr4-edit-patch-migration-v0.1','parent':baseline[:5],'operations':ops,
       'figure_moves':[{'old':'Fig3B full','new':'FigS6G; upper B1/B2 reused in main Fig3B'},{'old':'Fig3C','new':'FigS7G'},{'old':'Fig4A','new':'Fig4A'},{'old':'Fig4C','new':'Fig4B'},{'old':'Fig4B','new':'FigS9D'}],
       'all_scientific_points_and_full_views_preserved':True,'scientific_inputs_read':0,'new_scientific_values':0})
    save(P/'stage_state.json',{'scope':'GR3/GR4 authorized static editorial and export work','stage':'PREPARED_NOT_FINAL_ACCEPTED','W04':'LAYOUT_PLANNED','W05_W06':'TEXT_REVISED_PENDING_CHECK','W07':'PENDING_RENDER_AND_VISUAL_QA','new_science_calls':0,
       'old_unknowns':['M01','M03','M04','M06','source uncertainty and impact','original FD unresolved','authors/journal'],'runtime':{'python':str(PY),'bundled_soffice':str(LO)},'old_budgets_clocks':'UNCHANGED'})
    print('PREPARED',len(reused),'opaque static assets;',len(ops),'tracked editorial operations')

def figures():
    from pypdf import PdfReader,PdfWriter,Transformation,PageObject
    from pypdf.generic import RectangleObject
    from reportlab.pdfgen import canvas
    registry=load(F/'provenance/publication_map.json')['bindings']
    byid={x['publication_id']:x for x in registry}
    mainpdf=PdfReader(F/'main_figures.pdf');sipdf=PdfReader(F/'supplement_figures.pdf')
    current=PK/'figures/current';current.mkdir(parents=True,exist_ok=True)
    records=[]
    def part(pubid,clip=None):
        b=byid[pubid];book=sipdf if pubid.startswith('Fig.S') else mainpdf
        pg=copy.deepcopy(book.pages[b['publication_pdf_page_1based']-1]);ph=float(pg.mediabox.height)
        box=b['source_box_points'];scale=b['scale_for_layout_only'];w=(box[2]-box[0])*scale;h=(box[3]-box[1])*scale
        l,t,rr,bb=clip or [0,0,w,h]
        assert 0<=l<rr<=w and 0<=t<bb<=h,(pubid,clip,w,h)
        x=32+l;y=ph-64-bb;cw=rr-l;ch=bb-t
        pg.cropbox=RectangleObject([x,y,x+cw,y+ch])
        return pg,(x,y,cw,ch),b
    def compose(name,size,parts,labels=(),caption='',orientation='portrait',anchor=None):
        page=PageObject.create_blank_page(width=size[0],height=size[1]);uses=[]
        for pid,clip,x,y,scale in parts:
            pg,box,b=part(pid,clip);sx,sy,w,h=box
            page.merge_transformed_page(pg,Transformation().translate(-sx,-sy).scale(scale).translate(x,y),over=True)
            uses.append({'parent_publication_id':pid,'source_pdf':str((F/('supplement_figures.pdf' if pid.startswith('Fig.S') else 'main_figures.pdf')).relative_to(R)),
                         'source_page_1based':b['publication_pdf_page_1based'],'source_plot_clip_top_left_points':clip,'source_absolute_clip_pdf_points':[sx,sy,sx+w,sy+h],
                         'destination_xy_scale':[x,y,scale],'complete_original_view_preserved':pid in ['Fig.3A','Fig.3B'] and name=='Fig3' and 'Fig.S6G' or 'Original full view retained in parent static package',
                         'source_values_or_graphics_reconstructed':False})
        if labels:
            buf=io.BytesIO();c=canvas.Canvas(buf,pagesize=size)
            for text,x,y in labels:c.setFont('Helvetica-Bold',12);c.drawString(x,y,text)
            c.save();page.merge_page(PdfReader(io.BytesIO(buf.getvalue())).pages[0])
        out=current/(name+'.pdf');wr=PdfWriter();wr.add_page(page)
        with out.open('wb') as f:wr.write(f)
        subprocess.run([str(BIN/'pdftoppm'),'-singlefile','-r','220','-png',str(out),str(current/name)],capture_output=True,check=True)
        rec={'id':name,'anchor':anchor,'pdf':str(out.relative_to(PK)),'png':str((current/(name+'.png')).relative_to(PK)),
             'page_size_points':list(size),'orientation':orientation,'caption':caption,'source_parts':uses,'new_observations':0,'scientific_content_change':False}
        records.append(rec)
    # All existing plot content; only the redundant outer wrapper is removed.
    for name,pid,ori in [('Fig1','Fig.1','landscape'),('Fig2','Fig.2','portrait'),('Fig5A','Fig.5A','portrait'),('Fig5B','Fig.5B','portrait')]:
        _,(_,_,w,h),b=part(pid)
        compose(name,(w,h),[(pid,None,0,0,1)],caption=b['caption'],orientation=ori,anchor='plate-'+name)
    compose('Fig3',(640,447),[
       ('Fig.3A',[0,32,496.8,218],72,237,1),
       ('Fig.3B',[0,52,619.2,245],10,18,1)],
       [('A  Complete-operation controls',12,432),('B  Helpful and harmful accounts',12,220)],
       'Complete O_SRC; G/q; original five-seed/equal-endpoint and full row-uniform reference. Full diagnostic and source-coexistence views are retained in Fig. S6G and S7G.',
       'landscape','plate-Fig3')
    compose('Fig4',(520,640),[
       ('Fig.4A',[0,24,496.8,252],12,382,1),
       ('Fig.4C',[0,24,496.8,374.4],12,10,1)],
       [('A  Signed Skill of q',12,626),('B  Paired contrast and h1 self-Skill',12,372)],
       'Fixed 32-source/64-link panel; G; original five-seed/equal-endpoint and restricted row-uniform reference. B lower shows h1 only. The corresponding E/B view is Fig. S9D.',
       'portrait','plate-Fig4')
    for b in registry:
        pid=b['publication_id']
        if not pid.startswith('Fig.S'):continue
        name=pid.replace('Fig.','');_,(_,_,w,h),_=part(pid)
        compose(name,(w,h),[(pid,None,0,0,1)],caption=b['caption'].replace('Conditional null is shown as an x, never zero.','Original conditional null is retained rather than converted to zero.'),
                orientation='landscape' if w/h>1.13 else 'portrait',anchor='plate-'+name)
    for name,pid in [('fig-s6g','Fig.3B'),('fig-s7g','Fig.3C'),('fig-s9d','Fig.4B')]:
        _,(_,_,w,h),b=part(pid)
        compose(name,(w,h),[(pid,None,0,0,1)],caption='Complete original view retained; see current SI caption for object and scope.',
                orientation='landscape' if w/h>1.13 else 'portrait',anchor='plate-'+name)
    ordermain=['Fig1','Fig2','Fig3','Fig4','Fig5A','Fig5B']
    ordersi=[x['id'] for x in records if x['id'].startswith('S')]
    # Insert the moved views beside their original scientific SI groups.
    for moved,before in [('fig-s6g','S7A'),('fig-s7g','S8A'),('fig-s9d','S10A')]:ordersi.insert(ordersi.index(before),moved)
    for filename,order in [('main_figures.pdf',ordermain),('supplement_figures.pdf',ordersi)]:
        w=PdfWriter()
        for name in order:w.append(current/(name+'.pdf'))
        with (PK/filename).open('wb') as f:w.write(f)
    changes={'figures/main_figures/page-01.png':'figures/current/Fig1.png','figures/main_figures/page-02.png':'figures/current/Fig2.png',
             'figures/main_figures/page-03.png':'figures/current/Fig3.png','figures/main_figures/page-04.png':'figures/current/Fig3.png','figures/main_figures/page-06.png':'figures/current/Fig4.png',
             'figures/main_figures/page-09.png':'figures/current/Fig5A.png','figures/main_figures/page-10.png':'figures/current/Fig5B.png',
             'figures/Fig3.pdf':'figures/current/Fig3.pdf','figures/Fig4.pdf':'figures/current/Fig4.pdf'}
    for b in registry:
        if b['publication_id'].startswith('Fig.S'):
            changes['figures/supplement_figures/page-'+str(b['publication_pdf_page_1based']).zfill(2)+'.png']='figures/current/'+b['publication_id'].replace('Fig.','')+'.png'
    for p in [PK/'manuscript.md',PK/'supplement.md',MAIN,SUP]:
        text=read(p)
        for old,new in changes.items():text=text.replace(old,new)
        put(p,text)
    save(RF/'publication_map_v0_7.json',{'schema':'GR3-GR4-layout-only-publication-map/0.7','parent':desc(F/'provenance/publication_map.json'),
       'main_order':ordermain,'supplement_order':ordersi,'records':records,'main_plate_pages':len(ordermain),'SI_plate_pages':len(ordersi),
       'points_recreated':False,'original_complete_figures_retained_in_static_assets':True,
       'trim_scope':'Main Fig3: select existing complete control axes and B1/B2; all original diagnostics and coexistence explicitly in SI. Main Fig4: trim outer source title only, add new publication labels; all six spans and curves retained.'})
    print('FIGURES',len(ordermain),'main pages;',len(ordersi),'SI pages; no new scientific points')

def pure_typesetting():
    """Reuse only the publication parser, never the old prepare/build entry points."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tree=ast.parse(read(R/'scripts/integrate_batch_f_v0_1.py'))
    names={'mr','me','MathParser','equation'}
    body=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
    ns={'OxmlElement':OxmlElement,'qn':qn,'SYMBOLS':{'in':'∈','geq':'≥','leq':'≤','lambda':'λ','varepsilon':'ε','epsilon':'ϵ','Delta':'Δ','delta':'δ','eta':'η','ell':'ℓ','pi':'π','top':'⊤','ldots':'…','sum':'∑','odot':'⊙','qquad':'   ','quad':'  ','log':'log','max':'max','min':'min','theta':'θ','kappa':'κ'}}
    exec(compile(ast.Module(body=body,type_ignores=[]),'registered_publication_notation_only','exec'),ns)
    def eq(text,display=False):return ns['equation'](text.replace(r'\;',' '),display)
    return eq

def bname(s):return 'b_'+re.sub(r'[^A-Za-z0-9_]','_',s)[:37]

def export(only=None):
    from docx import Document
    from docx.shared import Inches,Pt,RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.section import WD_SECTION_START,WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT,WD_CELL_VERTICAL_ALIGNMENT
    from PIL import Image
    equation=pure_typesetting()
    catalog=load(RF/'publication_map_v0_7.json')
    images={x['png']:x for x in catalog['records']}
    def section(sec,land=False):
        sec.orientation=WD_ORIENT.LANDSCAPE if land else WD_ORIENT.PORTRAIT
        sec.page_width=Inches(11 if land else 8.5);sec.page_height=Inches(8.5 if land else 11)
        sec.top_margin=sec.bottom_margin=Inches(.5 if land else .65)
        sec.left_margin=sec.right_margin=Inches(.65 if land else .7)
        sec.header_distance=Inches(.22);sec.footer_distance=Inches(.22)
    def link(p,label,url):
        el=OxmlElement('w:hyperlink')
        if url.startswith('#'):el.set(qn('w:anchor'),bname(url[1:]))
        else:
            url=url.replace('manuscript.md','manuscript.html').replace('supplement.md','supplement.html')
            rid=p.part.relate_to(url,'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',is_external=True);el.set(qn('r:id'),rid)
        run=OxmlElement('w:r');pr=OxmlElement('w:rPr');col=OxmlElement('w:color');col.set(qn('w:val'),'234A65');pr.append(col);run.append(pr)
        t=OxmlElement('w:t');t.text=label;run.append(t);el.append(run);p._p.append(el)
    def rich(p,text,size=None):
        pat=r'\\\((.*?)\\\)|\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|(?<!\*)\*([^*]+)\*(?!\*)|`([^`]+)`';pos=0
        for m in re.finditer(pat,text,re.S):
            p.add_run(text[pos:m.start()]);math,label,url,bold,italic,code=m.groups()
            if math is not None:p._p.append(equation(math.strip()))
            elif label is not None:link(p,label,url)
            elif bold is not None:p.add_run(bold).bold=True
            elif italic is not None:p.add_run(italic).italic=True
            else:p.add_run(code)
            pos=m.end()
        p.add_run(text[pos:])
        if size:
            for run in p.runs:run.font.size=Pt(size)
    for is_sup,stem in [(False,'manuscript_v0_3'),(True,'supplement_v0_3')]:
        if only is not None and stem!=only:continue
        text=read(PK/('supplement.md' if is_sup else 'manuscript.md'))
        if not is_sup:
            image_lines=re.findall(r'^!\[[^\]]*\]\([^)]+\)$',text,re.M)
            text=re.sub(r'^!\[[^\]]*\]\([^)]+\)\s*$','',text,flags=re.M)
            text=text.rstrip()+'\n\n## Figures\n\n'+'\n\n'.join(image_lines)+'\n'
        doc=Document();section(doc.sections[0]);normal=doc.styles['Normal'];normal.font.name='Cambria';normal.font.size=Pt(11)
        normal.paragraph_format.line_spacing=1.12;normal.paragraph_format.space_after=Pt(6)
        for sty,size in [('Title',20),('Heading 1',14),('Heading 2',12),('Heading 3',11),('Caption',9)]:
            s=doc.styles[sty];s.font.name='Cambria';s.font.size=Pt(size);s.font.color.rgb=RGBColor(0,0,0)
            s.paragraph_format.keep_with_next=sty!='Caption';s.paragraph_format.space_before=Pt(10 if 'Heading' in sty else 0)
            s.paragraph_format.space_after=Pt(5)
            rp=s._element.get_or_add_rPr()
            for node in rp.iter():
                for k in list(node.attrib):
                    if 'theme' in k.lower():del node.attrib[k]
            for node in list(s._element.iter(qn('w:pBdr'))):node.getparent().remove(node)
        doc.styles['Caption'].font.italic=False
        doc.styles['Caption'].font.bold=False
        for s in doc.styles:
            if s.type==1:s.paragraph_format.widow_control=True
        doc.core_properties.title=('Supplementary material — ' if is_sup else '')+read(PK/'manuscript.md').splitlines()[0][2:]
        doc.core_properties.author='';doc.core_properties.last_modified_by='';doc.core_properties.comments=''
        # Version identity is on the title and filenames; keep all page heads blank.
        # This avoids duplicated or inconsistent running headers across section changes.
        h=doc.sections[0].header.paragraphs[0];h.text=''
        footer=doc.sections[0].footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.RIGHT
        fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE');footer._p.append(fld)
        mp=OxmlElement('m:mathPr');mf=OxmlElement('m:mathFont');mf.set(qn('m:val'),'Cambria Math');mp.append(mf);doc.settings._element.append(mp)
        lines=text.splitlines();i=0;pending=[];bookmarks=[];plates=[];mathlog=[];land=False;next_figure_title=None;pending_caption=False
        def mark(p,aid):
            idx=len(bookmarks)+1;b=OxmlElement('w:bookmarkStart');b.set(qn('w:id'),str(idx));b.set(qn('w:name'),bname(aid));p._p.insert(0,b)
            e=OxmlElement('w:bookmarkEnd');e.set(qn('w:id'),str(idx));p._p.append(e)
            bookmarks.append({'anchor':aid,'bookmark':bname(aid),'paragraph_text_excerpt':p.text[:180]})
        def marks(p):
            for aid in pending:mark(p,aid)
            pending.clear()
        while i<len(lines):
            raw=lines[i];line=raw.strip();i+=1
            if not line:continue
            m=re.fullmatch(r'<a id="([^"]+)"></a>',line)
            if m:pending.append(m.group(1));continue
            if line.startswith('<!--'):continue
            m=re.match(r'^(#{1,4}) (.*)',line)
            if m:
                level=len(m.group(1));label=m.group(2)
                if label=='Figures' and not is_sup:continue
                if is_sup and re.match(r'Figure S\d+[A-Z]',label):next_figure_title=label;continue
                if land:section(doc.add_section(WD_SECTION_START.NEW_PAGE));land=False
                if level==2 and label in ['Tables','Figure legends'] and not is_sup:doc.add_page_break()
                p=doc.add_paragraph(label,'Title' if level==1 else 'Heading '+str(min(level-1,3)));marks(p);continue
            if line.startswith('|'):
                rows=[line]
                while i<len(lines) and lines[i].strip().startswith('|'):rows.append(lines[i].strip());i+=1
                vals=[[c.strip() for c in row.strip('|').split('|')] for row in rows if not re.fullmatch(r'[|\s:\-]+',row)]
                n=len(vals[0]);t=doc.add_table(rows=0,cols=n);t.alignment=WD_TABLE_ALIGNMENT.CENTER;t.autofit=False
                w=(doc.sections[-1].page_width-doc.sections[-1].left_margin-doc.sections[-1].right_margin)/914400
                weights={2:[1.8,5.0],3:[1.3,2.2,3.5],4:[1.2,2.2,1.5,2.1],6:[1.1,1.1,.55,.55,.8,2.9]}.get(n,[1]*n)
                widths=[w*x/sum(weights) for x in weights]
                for col,wi in zip(t.columns,widths):col.width=Inches(wi)
                borders=OxmlElement('w:tblBorders')
                for side in ['top','left','bottom','right','insideH','insideV']:
                    b=OxmlElement('w:'+side);b.set(qn('w:val'),'single');b.set(qn('w:sz'),'4');b.set(qn('w:color'),'D9D9D9');borders.append(b)
                t._tbl.tblPr.append(borders)
                for ri,row in enumerate(vals):
                    assert len(row)==n
                    cells=t.add_row().cells
                    for ci,value in enumerate(row):
                        c=cells[ci];c.width=Inches(widths[ci]);c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
                        pr=c._tc.get_or_add_tcPr();margin=OxmlElement('w:tcMar')
                        for side in ['top','bottom','left','right']:
                            q=OxmlElement('w:'+side);q.set(qn('w:w'),'70');q.set(qn('w:type'),'dxa');margin.append(q)
                        pr.append(margin);fill=OxmlElement('w:shd');fill.set(qn('w:fill'),'E9EDF1' if ri==0 else 'FFFFFF');pr.append(fill)
                        p=c.paragraphs[0];p.paragraph_format.space_before=Pt(1);p.paragraph_format.space_after=Pt(1);p.paragraph_format.line_spacing=1.04
                        rich(p,value,9 if n>=4 else 9.5)
                        if ri==0:
                            for run in p.runs:run.bold=True
                            p.paragraph_format.keep_with_next=True
                    tr=t.rows[-1]._tr.get_or_add_trPr();tr.append(OxmlElement('w:cantSplit'))
                    if ri==0:tr.append(OxmlElement('w:tblHeader'))
                marks(t.cell(0,0).paragraphs[0]);doc.add_paragraph().paragraph_format.space_after=Pt(1);continue
            if line==r'\[':
                tex=[]
                while i<len(lines) and lines[i].strip()!=r'\]':tex.append(lines[i]);i+=1
                i+=1;expr='\n'.join(tex).strip()
                for part in re.split(r'\\qquad\s*',expr):
                    p=doc.add_paragraph();p.alignment=WD_ALIGN_PARAGRAPH.CENTER;p._p.append(equation(part,True));marks(p)
                mathlog.append({'display':True,'source':expr});continue
            m=re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)',line)
            if m:
                rel=m.group(2);b=images[rel];needland=b['orientation']=='landscape'
                break_before=needland==land
                if needland!=land:section(doc.add_section(WD_SECTION_START.NEW_PAGE),needland);land=needland
                title=next_figure_title or ('Figure '+b['id'][3:]);next_figure_title=None
                p=doc.add_paragraph(title,'Heading 2');p.paragraph_format.space_before=Pt(0);p.paragraph_format.page_break_before=break_before;marks(p);mark(p,b['anchor'])
                sec=doc.sections[-1];aw=(sec.page_width-sec.left_margin-sec.right_margin)/914400;ah=(sec.page_height-sec.top_margin-sec.bottom_margin)/914400
                wi,hi=b['page_size_points'];maxh=ah-.75
                picw=min(aw,maxh*wi/hi)
                p=doc.add_paragraph();p.alignment=WD_ALIGN_PARAGRAPH.CENTER;p.paragraph_format.space_after=Pt(3);p.paragraph_format.keep_with_next=True
                shape=p.add_run().add_picture(str(PK/rel),width=Inches(picw),height=Inches(picw*hi/wi))
                shape._inline.docPr.set('descr',title+'; existing source plot, no scientific values reconstructed')
                if not is_sup:
                    caption=b['caption']
                    if b['id']=='Fig1':caption='Frozen representations and named readout functions connect separate predictive and finite-response branches. Corners, operations, panels and numerical objects are defined in the Figure 1 legend and Table 1.'
                    p=doc.add_paragraph(style='Caption');p.paragraph_format.keep_together=True;rich(p,caption,9)
                else:pending_caption=True
                plates.append({'id':b['id'],'title':title,'bookmark':bname(b['anchor']),'image':rel,'orientation':b['orientation'],'width_inches':picw,'scale_points_per_source_point':picw*72/wi})
                continue
            if raw.startswith('    '):
                code=[raw[4:]]
                while i<len(lines) and lines[i].startswith('    '):code.append(lines[i][4:]);i+=1
                p=doc.add_paragraph();marks(p)
                run=p.add_run('\n'.join(code));run.font.name='Courier New';run.font.size=Pt(8.5);continue
            while i<len(lines) and lines[i].strip() and not lines[i].strip().startswith(('#','<a ','|',r'\[','![')) and not lines[i].startswith('    '):
                line+=' '+lines[i].strip();i+=1
            p=doc.add_paragraph(style='Caption' if pending_caption else None);marks(p)
            rich(p,line,9 if pending_caption else None);pending_caption=False
            mathlog.extend({'display':False,'source':x} for x in re.findall(r'\\\((.*?)\\\)',line))
            if re.fullmatch(r'\*\*[^*]+\*\*',line):p.paragraph_format.keep_with_next=True
        assert not pending,pending
        doc.save(PK/(stem+'.docx'))
        save(A/(stem+'_export.json'),{'source':str((PK/('supplement.md' if is_sup else 'manuscript.md')).relative_to(R)),
             'bookmarks':bookmarks,'plates':plates,'equation_sources':mathlog,'equations':'Native editable OMML, structural conversion only; no expression evaluation',
             'page_design':'Letter portrait prose with landscape sections for wide scientific plates; body 11pt'})
        print('EXPORTED',stem,len(bookmarks),'bookmarks',len(plates),'plates')

def render(only=None):
    for stem in ['manuscript_v0_3','supplement_v0_3']:
        if only is not None and stem!=only:continue
        out=A/'render'/stem;out.mkdir(parents=True,exist_ok=True)
        log=A/(stem+'_render.log')
        with log.open('w') as f:
            subprocess.run([str(PY),str(RENDER),str(PK/(stem+'.docx')),'--output_dir',str(out),'--dpi','110','--emit_pdf'],stdout=f,stderr=subprocess.STDOUT,check=True)
        shutil.copyfile(out/(stem+'.pdf'),PK/(stem+'.pdf'))
        print('RENDERED',stem,len(list(out.glob('page-*.png'))),'pages',flush=True)

def reader_html():
    # Only the bounded presentation parser is loaded; no payload/ETL helpers.
    tree=ast.parse(read(R/'scripts/package_revision_batch_d_v0_1.py'))
    nodes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='TeXPresentation']
    ns={'html':html};exec(compile(ast.Module(body=nodes,type_ignores=[]),'publication_mathml_only','exec'),ns)
    parser=ns['TeXPresentation'];parser.symbols.update({'theta':'θ','kappa':'κ','min':'min'})
    def mathml(text,display=False):
        text=re.sub(r'\\quad(?![A-Za-z])',lambda m:r'\qquad',text.replace(r'\;',' '))
        return '<math xmlns="http://www.w3.org/1998/Math/MathML" display="'+('block' if display else 'inline')+'"><semantics><mrow>'+parser(text).sequence()+'</mrow><annotation encoding="application/x-tex">'+html.escape(text)+'</annotation></semantics></math>'
    def inline(text):
        out=[];pos=0;pat=r'\\\((.*?)\\\)|\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|(?<!\*)\*([^*]+)\*(?!\*)|`([^`]+)`'
        for m in re.finditer(pat,text,re.S):
            out.append(html.escape(text[pos:m.start()]));tex,label,url,bold,italic,code=m.groups()
            if tex is not None:out.append(mathml(tex))
            elif label is not None:
                url=url.replace('manuscript.md','manuscript.html').replace('supplement.md','supplement.html')
                out.append('<a href="'+html.escape(url,quote=True)+'">'+html.escape(label)+'</a>')
            elif bold is not None:out.append('<strong>'+html.escape(bold)+'</strong>')
            elif italic is not None:out.append('<em>'+html.escape(italic)+'</em>')
            else:out.append('<code>'+html.escape(code)+'</code>')
            pos=m.end()
        out.append(html.escape(text[pos:]));return ''.join(out)
    css='body{font:16px/1.65 Georgia,serif;color:#161616;max-width:1040px;margin:36px auto;padding:0 24px}h1,h2,h3,h4{color:#000;line-height:1.25}h1{font-size:28px}h2{margin-top:2em}a{color:#234a65}img{max-width:100%;height:auto}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:9px;border:1px solid #d9d9d9;vertical-align:middle}th{background:#e9edf1}pre{white-space:pre-wrap}math[display=block]{margin:20px 0}nav{font:14px/1.5 system-ui,sans-serif;margin-bottom:26px}'
    def page(title,body):return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>'+css+'</style><body>'+body+'</body></html>\n'
    for name in ['manuscript','supplement']:
        md=read(PK/(name+'.md'));lines=md.splitlines();i=0;out=['<nav><a href="index.html">Package contents</a> · <a href="'+name+'_v0_3.docx">Word</a> · <a href="'+name+'_v0_3.pdf">PDF</a> · <a href="data_index.html">Data and dictionaries</a></nav>']
        while i<len(lines):
            raw=lines[i];s=raw.strip();i+=1
            if not s:continue
            if s.startswith('<a id='):out.append(s);continue
            if s.startswith('<!--'):continue
            m=re.match(r'^(#{1,4}) (.*)',s)
            if m:level=len(m.group(1));out.append('<h'+str(level)+'>'+inline(m.group(2))+'</h'+str(level)+'>');continue
            if s.startswith('|'):
                rows=[s]
                while i<len(lines) and lines[i].strip().startswith('|'):rows.append(lines[i].strip());i+=1
                vals=[[v.strip() for v in row.strip('|').split('|')] for row in rows if not re.fullmatch(r'[|\s:\-]+',row)]
                out.append('<table>')
                for ri,row in enumerate(vals):out.append('<tr>'+''.join('<'+('th' if ri==0 else 'td')+'>'+inline(v)+'</'+('th' if ri==0 else 'td')+'>' for v in row)+'</tr>')
                out.append('</table>');continue
            if s==r'\[':
                parts=[]
                while i<len(lines) and lines[i].strip()!=r'\]':parts.append(lines[i]);i+=1
                i+=1;out.append(mathml('\n'.join(parts),True));continue
            m=re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)',s)
            if m:out.append('<p><img src="'+html.escape(m.group(2),quote=True)+'" alt="'+html.escape(m.group(1),quote=True)+'"></p>');continue
            if raw.startswith('    '):
                code=[raw[4:]]
                while i<len(lines) and lines[i].startswith('    '):code.append(lines[i][4:]);i+=1
                out.append('<pre>'+html.escape('\n'.join(code))+'</pre>');continue
            out.append('<p>'+inline(s)+'</p>')
        put(PK/(name+'.html'),page(name+' v0.3','\n'.join(out)))
    body='<h1>Frozen readout response audit v0.3</h1><p>GR3/GR4 private editorial package. Scientific results and unresolved boundaries are inherited; author and journal confirmation remains pending.</p>'
    for name,label in [('manuscript','Main manuscript'),('supplement','Supplementary material')]:
        body+='<h2>'+label+'</h2><p><a href="'+name+'_v0_3.docx">Word</a> · <a href="'+name+'_v0_3.pdf">PDF</a> · <a href="'+name+'.html">HTML</a> · <a href="'+name+'.md">Markdown</a></p>'
    body+='<h2>Figures and evidence</h2><p><a href="main_figures.pdf">Reorganized main vector plates</a> · <a href="supplement_figures.pdf">Complete SI vector plates</a> · <a href="data_index.html">Six Data groups and 21 tables with dictionaries</a> · <a href="provenance/current/publication_map_v0_7.json">Current figure/source map</a> · <a href="provenance/current/current_locations_v0_7.json">Current document locations</a></p><p><a href="revision_report.md">Revision and verification report</a> · <a href="author_journal_pending.html">Author and journal pending items</a> · <a href="README_PRIVATE.md">Package use</a></p>'
    put(PK/'index.html',page('Private manuscript v0.3',body))
    # The relocated dossier gets local navigation; original bytes remain in the sealed parent.
    dossier=PK/'provenance/gr1_gr2/references/静态方法与位置语义核验_v0.1.md'
    s=read(dossier).replace('../../planning/gap_remediation_gr1_gr2_v0_1/','../planning/')
    put(dossier,s)
    print('HTML native MathML reader documents and package index written')

if __name__=='__main__':
    {'prepare':prepare,'figures':figures,'export':export,'render':render,'html':reader_html,
     'export_si':lambda:export('supplement_v0_3'),'render_si':lambda:render('supplement_v0_3')}[sys.argv[1]]()
