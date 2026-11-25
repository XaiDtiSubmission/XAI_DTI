#!/usr/bin/env python3
"""Render B1/B2 W03 from the already verified W02 ledger only.
No inherited data read, no scientific arithmetic, no statistics or model calls.
Float casts, axis limits and coordinates are presentation operations only.
"""
import hashlib,html,itertools,json,math,os,shutil,subprocess
from collections import Counter
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT=Path(__file__).resolve().parents[1]
LEDGER="references/numeric_citations_b1_b2_v0_1/ledger.json"
FIG="figures/w03_b1_b2_v0_1"
TAB="tables/w03_b1_b2_v0_1"
AUD="audit/w03_b1_b2_v0_1"
REF="references/numeric_citations_b1_b2_w03_v0_1"
OPS=["D","P","DP"]
SPANS=[0.03125,0.0625,0.125,0.25,0.5,1]
SPAN_LABELS=["1/32","1/16","1/8","1/4","1/2","1"]
SEEDS=["17","29","43","71","101","MEAN5"]
FUNCTIONS=["q","h0","h_aff","h1"]
ENDPOINTS=["Kd","Ki","KD_KI_EQUAL"]
POLICIES=["FIT_ROW_UNIFORM","FIT_PROTEIN_INPUT_UNIFORM"]
COLORS=["#0077BB","#EE7733","#009988","#CC3311","#AA3377","#111111"]
MARKERS=["o","s","^","v","P","D"]
OP_COLORS=["#0077BB","#009988","#EE7733"]
TRACE=[];ARTIFACTS=[];LAYOUT=[];ROWSETS={};INDEX={};SOURCES={}

plt.rcParams.update({"font.family":"DejaVu Sans","font.size":9,"axes.titlesize":10,
 "axes.labelsize":9,"xtick.labelsize":8,"ytick.labelsize":8,"legend.fontsize":8,
 "axes.spines.top":False,"axes.spines.right":False,"axes.linewidth":0.65,
 "pdf.fonttype":42,"ps.fonttype":42,"svg.fonttype":"none","savefig.dpi":300,
 "axes.unicode_minus":True,"figure.facecolor":"white"})

def digest(b): return hashlib.sha256(b).hexdigest()
def textfile(path,content):
    p=ROOT/path
    if p.exists() and p.read_text()==content:return
    if p.exists():
        old=p.read_text()
        patch="*** Begin Patch\n*** Update File: "+path+"\n@@\n"+"".join("-"+x+"\n" for x in old.splitlines())+"".join("+"+x+"\n" for x in content.splitlines())+"*** End Patch\n"
    else:patch="*** Begin Patch\n*** Add File: "+path+"\n"+"".join("+"+x+"\n" for x in content.splitlines())+"*** End Patch\n"
    r=subprocess.run([shutil.which("apply_patch")],input=patch,text=True,cwd=ROOT,capture_output=True)
    if r.returncode:raise RuntimeError(r.stdout+r.stderr)
    assert p.read_text()==content
def save(path,obj):textfile(path,json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
def lines(path,records):textfile(path,"".join(json.dumps(x,ensure_ascii=False,separators=(",",":"),allow_nan=False)+"\n" for x in records))

def load():
    ledger=json.loads((ROOT/LEDGER).read_text())
    SOURCES.update({s["source_id"]:s for s in ledger["shared_source_registry"]})
    entries={e["saved_vector_ref"]:e for e in ledger["entries"]}
    for shard in ledger["vector_registry"]["shards"]:
        b=(ROOT/shard["path"]).read_bytes()
        assert digest(b)==shard["sha256"] and len(b)==shard["bytes"]
        for number,line in enumerate(b.splitlines(),1):
            v=json.loads(line);e=entries[v["vector_id"]]
            assert e["vector_registry_path"]==shard["path"] and e["vector_registry_line_1based"]==number
            sid=v["shared_source_ref"]
            for member in v["members"]:
                assert member["row_state"]=="UNIQUE_ORIGINAL_ROW"
                for native in member["original_rows"]:
                    line_no=native["line_1based"]
                    row=ROWSETS.setdefault(sid,{}).setdefault(line_no,{
                        "source_id":sid,"original_line_1based":line_no,"line_sha256":native["line_sha256"],
                        "native_axes":member["native_axes"],"original_status_fields":native["original_status_fields"],"values":{}})
                    assert row["native_axes"]==member["native_axes"] and row["line_sha256"]==native["line_sha256"]
                    assert row["original_status_fields"]==native["original_status_fields"]
                    for field,cell in native["values"].items():
                        stored=dict(cell)
                        stored["citation_id"]=e["citation_id"];stored["vector_id"]=v["vector_id"]
                        stored["vector_registry_path"]=shard["path"];stored["vector_registry_line_1based"]=number
                        if field in row["values"]: assert row["values"][field]==stored
                        row["values"][field]=stored
    for sid,rows in ROWSETS.items():
        INDEX[sid]={tuple(sorted(r["native_axes"].items())):r for r in rows.values()}
    assert len(entries)==2592
    assert sum(len(r["values"]) for rows in ROWSETS.values() for r in rows.values())==58176
    return ledger

def row(sid,**axes): return INDEX[sid][tuple(sorted(axes.items()))]
def b1(arm,seed,endpoint,function,op,policy,span):
    return row("B1_SPAN",arm=arm,seed_layer=seed,endpoint_layer=endpoint,function_id=function,
        operation_layer=op,reference_scheme=policy,**{"lambda":span})
def b2(arm,seed,endpoint,function,op,policy):
    return row("B2_SUMMARY",arm=arm,seed_layer=seed,endpoint_layer=endpoint,function_id=function,
        operation_layer=op,policy_id=policy)
def value(r,field,artifact,page,subplot,series,point):
    c=r["values"][field]
    state=c["state"]
    rec={"artifact_id":artifact,"page_1based":page,"subplot_id":subplot,"series_id":series,
        "point_index_1based":point,"source_id":r["source_id"],"original_line_1based":r["original_line_1based"],
        "original_line_sha256":r["line_sha256"],"json_pointer":c["json_pointer"],"native_axes":r["native_axes"],
        "raw_json":c["raw_json"],"original_cell_state":state,"W02_citation_id":c["citation_id"],
        "W02_vector_registry_path":c["vector_registry_path"],"W02_vector_registry_line_1based":c["vector_registry_line_1based"],
        "quantity_role":("SAVED_PAIRED_H1_MINUS_COMPARATOR_DELTA_SKILL" if "DeltaSkill" in field else
            "FUNCTION_SELF_SIGNED_SKILL_NO_ABS_TRANSFORM" if field=="Skill" else "ORIGINAL_SAVED_QUANTITY"),
        "rendering":"DIRECT_FLOAT_CAST_FOR_DISPLAY" if state=="NON_NULL_SAVED_NUMBER" else "GAP_OR_STATUS_NO_IMPUTATION"}
    TRACE.append(rec)
    if state=="NON_NULL_SAVED_NUMBER":
        result=float(c["raw_json"]);assert math.isfinite(result)
        return result
    return math.nan

def styled(ax,ylabel=None,span=False):
    ax.axhline(0,color="#666666",lw=0.7,zorder=0)
    ax.grid(axis="y",color="#dddddd",lw=0.45,alpha=0.65)
    ax.set_axisbelow(True)
    if ylabel:ax.set_ylabel(ylabel)
    if span:
        ax.set_xscale("log",base=2)
        ax.set_xticks(SPANS,SPAN_LABELS)
        ax.minorticks_off()
        ax.tick_params(axis="x",labelsize=7.3)
        ax.set_xlim(0.027,1.16)
    return ax

def finish(fig,artifact,page,pdf,stem=None):
    fig.canvas.draw()
    width,height=fig.canvas.get_width_height()
    outside=[]
    for text in fig.findobj(matplotlib.text.Text):
        if not text.get_visible() or not text.get_text():continue
        box=text.get_window_extent(fig.canvas.get_renderer())
        if box.x0 < -1 or box.y0 < -1 or box.x1 > width+1 or box.y1 > height+1:
            outside.append(text.get_text())
    LAYOUT.append({"artifact_id":artifact,"page_1based":page,"text_outside_canvas":outside})
    if outside:raise RuntimeError("Text outside canvas: "+artifact+" "+str(outside))
    pdf.savefig(fig)
    if stem:
        fig.savefig(ROOT/FIG/(stem+".svg"))
        fig.savefig(ROOT/FIG/(stem+".png"),dpi=300)
    plt.close(fig)

def context(arm,endpoint,policy,path=True):
    p="row-uniform" if policy==POLICIES[0] else "protein-input-uniform"
    return f"{arm} | {endpoint} | {p} | "+("restricted path reference" if path else "full reference")
def main_footer(fig,path=True,extra=""):
    base="Path panel: 32 sources / 64 links" if path else "Full support: 2,242 sources / 322,659 links"
    fig.text(0.09,0.025,base+" | G | saved five-seed mean; Kd/Ki equal | "+("restricted" if path else "full")+" row-uniform",
        fontsize=7.3,color="#4a4a4a")
    if extra:fig.text(0.09,0.069,extra,fontsize=7.3,color="#4a4a4a")
def add_artifact(artifact,path,pages,caption,scope):
    ARTIFACTS.append({"artifact_id":artifact,"path":path,"pdf_page_1based":pages if artifact.startswith("Fig.") else None,
        "page_count":1 if artifact.startswith("Fig.") else pages,"caption":caption,"scope":scope,
        "source_ledger":LEDGER,"script":"scripts/render_w03_b1_b2_v0_1.py",
        "manuscript_body_written":False,"planned_claim_id":"C06" if artifact.startswith(("Fig.4","S6")) else "C04",
        "planned_manuscript_location":"v0.4 §3.4" if artifact.startswith(("Fig.4","S6")) else "v0.4 §3.3",
        "scientific_computation":False})

def main_figures():
    path=FIG+"/B1_B2_main_figures_v0_1.pdf"
    with PdfPages(ROOT/path,metadata={"Title":"B1/B2: Fig.3A and Fig.4A-D","Author":"","Subject":"Saved aggregate values; finite exposed-development scope"}) as pdf:
        art="Fig.3A"
        fig,axes=plt.subplots(1,2,figsize=(6.9,3.55),gridspec_kw={"width_ratios":[1.65,1]})
        fig.subplots_adjust(left=.10,right=.975,bottom=.24,top=.80,wspace=.39)
        fig.suptitle("Fig.3A | Complete-operation fidelity of q",x=.10,ha="left",y=.96,fontweight="bold",fontsize=11)
        ax=axes[0]
        specs=[("E","Source AD","#0077BB",""),("B","Zero response","#EE7733","//"),("E_perm","Fixed permutation","#999999","xx")]
        for j,(field,label,color,hatch) in enumerate(specs):
            yy=[value(b2("G","MEAN5","KD_KI_EQUAL","q",op,POLICIES[0]),field,art,1,"errors",label,i+1) for i,op in enumerate(OPS)]
            ax.bar([i+(j-1)*.24 for i in range(3)],yy,width=.23,color=color,hatch=hatch,edgecolor="white",linewidth=.4,label=label)
        ax.set_xticks(range(3),OPS);styled(ax,"Mean absolute response error")
        ax.set_title("Prediction and two saved baselines",fontsize=9)
        ax.legend(frameon=False,fontsize=7.5,loc="upper left")
        ax=axes[1]
        yy=[value(b2("G","MEAN5","KD_KI_EQUAL","q",op,POLICIES[0]),"Skill",art,1,"self_skill","q",i+1) for i,op in enumerate(OPS)]
        ax.bar(range(3),yy,width=.57,color=OP_COLORS,edgecolor="white",linewidth=.4)
        ax.set_xticks(range(3),OPS);styled(ax,"Function-self signed Skill")
        ax.set_title("Saved Skill(q)",fontsize=9)
        main_footer(fig,False,"Fixed permutation is a saved control, not a random null distribution.")
        finish(fig,art,1,pdf,"Fig3A_v0_1")
        add_artifact(art,path,1,"Complete O_SRC: E, zero-response error B and fixed-permutation error E_perm for q; function-self signed Skill is shown separately. All values use the saved G/MEAN5/KD_KI_EQUAL/full row-uniform slots. The fixed permutation is not a null distribution.","P_OSRC; full reference")
        art="Fig.4A"
        fig,ax=plt.subplots(figsize=(6.9,3.5));fig.subplots_adjust(left=.12,right=.975,bottom=.25,top=.83)
        fig.suptitle("Fig.4A | Signed approximation value across measured spans",x=.10,ha="left",y=.96,fontweight="bold",fontsize=11)
        for j,op in enumerate(OPS):
            yy=[value(b1("G","MEAN5","KD_KI_EQUAL","q",op,POLICIES[0],s),"Skill",art,2,"self_skill",op,i+1) for i,s in enumerate(SPANS)]
            ax.plot(SPANS,yy,color=OP_COLORS[j],marker=MARKERS[j],lw=1.4,ms=4,label=op)
        styled(ax,"Function-self signed Skill(q)",True)
        ax.set_xlabel("Fraction of full representation displacement, λ (log2 scale)")
        ax.legend(title="Operation",frameon=False,ncol=3,fontsize=8,loc="best")
        main_footer(fig,True,"Points are measured spans; connecting segments are visual guides only.")
        finish(fig,art,2,pdf,"Fig4A_v0_1")
        add_artifact(art,path,2,"Saved function-self signed Skill(q) at all six measured spans for D, P and DP. Zero indicates the zero-response baseline. The horizontal axis is the fraction of each operation's own full representation displacement; log2 spacing is explicit. Connecting segments are visual guides, not interpolation or a certified radius.","P_PATH; restricted reference")
        art="Fig.4B"
        fig,axes=plt.subplots(1,3,figsize=(6.9,3.5),sharey=True);fig.subplots_adjust(left=.10,right=.98,bottom=.25,top=.80,wspace=.20)
        fig.suptitle("Fig.4B | Saved approximation error and response magnitude",x=.10,ha="left",y=.96,fontweight="bold",fontsize=11)
        for j,op in enumerate(OPS):
            for field,color,marker,label in [("E","#0077BB","o","E: approximation error"),("B","#EE7733","s","B: response magnitude")]:
                yy=[value(b1("G","MEAN5","KD_KI_EQUAL","q",op,POLICIES[0],s),field,art,3,op,field,i+1) for i,s in enumerate(SPANS)]
                axes[j].plot(SPANS,yy,color=color,marker=marker,ms=3,lw=1.2,label=label)
            styled(axes[j],"Saved E or B" if j==0 else None,True);axes[j].set_title(op)
            for tick in axes[j].get_xticklabels():tick.set_rotation(20);tick.set_ha("right")
        axes[0].legend(frameon=False,fontsize=7,loc="upper left")
        fig.supxlabel("Fraction of each operation's full displacement, λ (log2 scale)",y=.13,fontsize=9)
        main_footer(fig,True)
        finish(fig,art,3,pdf,"Fig4B_v0_1")
        add_artifact(art,path,3,"E and B are directly saved error and response magnitude for the same q/path/span slots. Both use the same vertical scale across D/P/DP. A smaller E alone is not evidence of greater Skill; Skill is displayed from its saved field in Fig.4A.","P_PATH; restricted reference")
        art="Fig.4C"
        fig,axes=plt.subplots(2,3,figsize=(6.9,5.2),sharey="row");fig.subplots_adjust(left=.12,right=.98,bottom=.18,top=.83,hspace=.53,wspace=.22)
        fig.suptitle("Fig.4C | Relative change and h1's own signed Skill",x=.10,ha="left",y=.97,fontweight="bold",fontsize=11)
        fig.text(.12,.89,"Relative: saved h1 − comparator ΔSkill",fontsize=9,fontweight="bold")
        for j,op in enumerate(OPS):
            for cmp,color,marker in [("h0","#AA3377","o"),("h_aff","#EE7733","s")]:
                field="h1_paired_differences."+cmp+".DeltaSkill"
                yy=[value(b1("G","MEAN5","KD_KI_EQUAL","h1",op,POLICIES[0],s),field,art,4,"Relative."+op,cmp,i+1) for i,s in enumerate(SPANS)]
                axes[0,j].plot(SPANS,yy,color=color,marker=marker,ms=3,lw=1.2,label="h1 − "+cmp)
            styled(axes[0,j],"Paired ΔSkill" if j==0 else None,True);axes[0,j].set_title(op)
            axes[0,j].tick_params(labelbottom=False)
            yy=[value(b1("G","MEAN5","KD_KI_EQUAL","h1",op,POLICIES[0],s),"Skill",art,4,"Function."+op,"h1_self",i+1) for i,s in enumerate(SPANS)]
            axes[1,j].plot(SPANS,yy,color="#0077BB",marker="D",ms=3,lw=1.3,label="h1 self")
            styled(axes[1,j],"Signed Skill(h1)" if j==0 else None,True)
            for tick in axes[1,j].get_xticklabels():tick.set_rotation(20);tick.set_ha("right")
        axes[0,0].legend(frameon=False,fontsize=7,loc="best")
        fig.text(.12,.493,"Function-self: original signed Skill(h1)",fontsize=9,fontweight="bold")
        fig.supxlabel("Fraction of each operation's full displacement, λ (log2 scale)",y=.103,fontsize=9)
        main_footer(fig,True)
        finish(fig,art,4,pdf,"Fig4C_v0_1")
        add_artifact(art,path,4,"Top: original saved h1-minus-h0 and h1-minus-h_aff paired ΔSkill. Bottom: h1's own saved signed Skill, without an absolute-value transform. Each view has its own zero line and quantity label. Positive ΔSkill does not imply positive Skill(h1), smaller E(h1), or positive Skill(q). Saved paired ΔE and ΔB are retained in the S6 table; no difference was reconstructed.","P_PATH; two distinct quantity roles")
        art="Fig.4D"
        fig,axes=plt.subplots(1,2,figsize=(6.9,3.65));fig.subplots_adjust(left=.10,right=.98,bottom=.25,top=.81,wspace=.36)
        fig.suptitle("Fig.4D | Auxiliary saved span-normalized quantities",x=.10,ha="left",y=.96,fontweight="bold",fontsize=11)
        for ax,field,label in zip(axes,["E_per_lambda","Skill_per_lambda"],["Saved E / λ","Saved signed Skill(q) / λ"]):
            for j,op in enumerate(OPS):
                yy=[value(b1("G","MEAN5","KD_KI_EQUAL","q",op,POLICIES[0],s),field,art,5,field,op,i+1) for i,s in enumerate(SPANS)]
                ax.plot(SPANS,yy,color=OP_COLORS[j],marker=MARKERS[j],lw=1.2,ms=3,label=op)
            styled(ax,label,True)
        axes[0].legend(frameon=False,ncol=3,fontsize=7,loc="best")
        fig.supxlabel("Fraction of each operation's full displacement, λ (log2 scale)",y=.13,fontsize=9)
        main_footer(fig,True,"Original saved normalization fields only; no new ratio or reliability index.")
        finish(fig,art,5,pdf,"Fig4D_v0_1")
        add_artifact(art,path,5,"Original saved E_per_lambda and Skill_per_lambda for q, used only as auxiliary scale-normalized views. These fields were not recomputed and do not replace the signed Skill or paired ΔSkill views.","P_PATH; original saved normalization")

def seed_legend(fig,y=.905):
    handles=[Line2D([],[],color=COLORS[j],marker=MARKERS[j],lw=1.35 if seed=="MEAN5" else .7,ms=3,
        label="saved MEAN5" if seed=="MEAN5" else "seed "+seed) for j,seed in enumerate(SEEDS)]
    fig.legend(handles=handles,loc="upper center",bbox_to_anchor=(.52,y),ncol=6,frameon=False,fontsize=8)
def supp_base(title,arm,endpoint,policy,page,path=True):
    fig=plt.figure(figsize=(11.7,8.3))
    fig.suptitle(title,x=.075,ha="left",y=.98,fontweight="bold",fontsize=13)
    fig.text(.075,.935,context(arm,endpoint,policy,path),fontsize=10)
    seed_legend(fig)
    fig.text(.075,.026,("P_PATH: 32 sources / 64 links | restricted references" if path else "P_OSRC: 2,242 sources / 322,659 links | full references")+
        " | saved layers only; no new averaging or CI",fontsize=8,color="#444444")
    fig.text(.96,.026,str(page),ha="right",fontsize=8)
    return fig

def supplements():
    path=FIG+"/S4_full_operation_v0_1.pdf"
    with PdfPages(ROOT/path) as pdf:
        page=0
        for arm,endpoint,policy in itertools.product(["G","S"],ENDPOINTS,POLICIES):
            page+=1;fig=supp_base("S4 | Full-operation values across all saved seed layers",arm,endpoint,policy,page,False)
            axes=fig.subplots(4,4)
            fig.subplots_adjust(left=.075,right=.97,bottom=.10,top=.835,hspace=.63,wspace=.42)
            for a,fun in enumerate(FUNCTIONS):
                for b,field in enumerate(["E","B","E_perm","Skill"]):
                    ax=axes[a,b]
                    for j,seed in enumerate(SEEDS):
                        yy=[value(b2(arm,seed,endpoint,fun,op,policy),field,"S4",page,fun+"."+field,seed,i+1) for i,op in enumerate(OPS)]
                        ax.plot([i+(j-2.5)*.085 for i in range(3)],yy,linestyle="none",marker=MARKERS[j],color=COLORS[j],
                            ms=4 if seed=="MEAN5" else 3,alpha=1 if seed=="MEAN5" else .8)
                    styled(ax)
                    ax.set_xticks(range(3),OPS);ax.set_xlim(-.43,2.43)
                    ax.set_title(fun+" | "+("signed Skill" if field=="Skill" else field),fontsize=9)
            finish(fig,"S4",page,pdf)
    add_artifact("S4",path,12,"Complete-operation saved E/B/E_perm/Skill for all four functions, arms, endpoint layers, reference schemes and seed layers. Seed offsets are deterministic display positions, not confidence intervals. Saved R/R_perm/Skill_perm, paired ΔE/ΔR, reference sensitivity, lower-bound values, ALL3_EQUAL and all original row statuses are retained in the complete companion tables. No scientific summary was computed.","P_OSRC; all native layers")
    path=FIG+"/S6_Objects_span_skill_v0_1.pdf"
    with PdfPages(ROOT/path) as pdf:
        page=0
        for arm,endpoint,policy in itertools.product(["G","S"],ENDPOINTS,POLICIES):
            page+=1;fig=supp_base("S6.Objects | Function-self signed Skill at all measured spans",arm,endpoint,policy,page)
            axes=fig.subplots(4,3,sharey="row")
            fig.subplots_adjust(left=.075,right=.97,bottom=.12,top=.835,hspace=.65,wspace=.25)
            for a,fun in enumerate(FUNCTIONS):
                for b,op in enumerate(OPS):
                    ax=axes[a,b]
                    for j,seed in enumerate(SEEDS):
                        yy=[value(b1(arm,seed,endpoint,fun,op,policy,s),"Skill","S6.Objects",page,fun+"."+op,seed,i+1) for i,s in enumerate(SPANS)]
                        ax.plot(SPANS,yy,color=COLORS[j],marker=MARKERS[j],lw=1.6 if seed=="MEAN5" else .65,
                            ms=3 if seed=="MEAN5" else 2.2,alpha=1 if seed=="MEAN5" else .75,zorder=6 if seed=="MEAN5" else 2)
                    styled(ax,"Signed Skill("+fun+")" if b==0 else None,True)
                    ax.set_title(fun+" | "+op,fontsize=9)
            fig.supxlabel("Fraction of each operation's full representation displacement, λ (log2 scale)",y=.067,fontsize=10)
            finish(fig,"S6.Objects",page,pdf)
    add_artifact("S6.Objects",path,12,"All four functions, S/G, every original seed layer, both endpoint-specific and saved equal-endpoint layers, both restricted references and all six spans. Every curve is function-self signed Skill, not abs(Skill). Thin curves are original individual seed layers; the black curve is the original saved MEAN5. No independent biological-sample interpretation or uncertainty interval is assigned. Full E/B/normalization and ALL3_EQUAL values remain in the companion table.","P_PATH; all native layers; original qualification context retained")
    path=FIG+"/S6_Paired_delta_skill_v0_1.pdf"
    with PdfPages(ROOT/path) as pdf:
        page=0
        for arm,endpoint,policy in itertools.product(["G","S"],ENDPOINTS,POLICIES):
            page+=1;fig=supp_base("S6.Paired | Saved h1-minus-comparator ΔSkill",arm,endpoint,policy,page)
            axes=fig.subplots(2,3,sharey="row")
            fig.subplots_adjust(left=.085,right=.97,bottom=.14,top=.83,hspace=.45,wspace=.25)
            for a,cmp in enumerate(["h0","h_aff"]):
                for b,op in enumerate(OPS):
                    ax=axes[a,b];field="h1_paired_differences."+cmp+".DeltaSkill"
                    for j,seed in enumerate(SEEDS):
                        yy=[value(b1(arm,seed,endpoint,"h1",op,policy,s),field,"S6.Paired",page,cmp+"."+op,seed,i+1) for i,s in enumerate(SPANS)]
                        ax.plot(SPANS,yy,color=COLORS[j],marker=MARKERS[j],lw=1.6 if seed=="MEAN5" else .65,
                            ms=3 if seed=="MEAN5" else 2.2,alpha=1 if seed=="MEAN5" else .75)
                    styled(ax,"ΔSkill: h1 − "+cmp if b==0 else None,True)
                    ax.set_title("h1 − "+cmp+" | "+op,fontsize=10)
            fig.supxlabel("Fraction of each operation's full representation displacement, λ (log2 scale)",y=.077,fontsize=10)
            finish(fig,"S6.Paired",page,pdf)
    add_artifact("S6.Paired",path,12,"Original paired h1-minus-h0 and h1-minus-h_aff ΔSkill at all six spans and all original parallel layers. These curves are distinct from function-self Skill. Companion tables retain every original paired ΔE/ΔB and the self Skill values without reconstructing any difference. Original FD unresolved groups remain in the qualification table; plotting saved fixed-AD values does not upgrade them.","P_PATH; relative pair differences")

STYLE="""body{font:14px/1.5 system-ui,sans-serif;color:#17212b;margin:28px;max-width:1600px}h1{font-size:24px}h2{font-size:18px}a{color:#00689d}code,td.num{font-family:ui-monospace,SFMono-Regular,monospace}header{max-width:1050px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:7px 9px;border-bottom:1px solid #dde3e7;text-align:left;vertical-align:top;white-space:nowrap}th{background:#eaf1f5;position:sticky;top:0}td.num{font-size:11px;cursor:pointer}td.num:hover{background:#e6f4fc}.scroll{max-height:70vh;overflow:auto;border:1px solid #cbd6df}select,button{padding:6px;margin:5px 10px 5px 0}button{cursor:pointer}pre{white-space:pre-wrap;word-break:break-word}.note{background:#f2f6f8;padding:12px 16px;border-left:3px solid #7395ab}.null{color:#6b4e00;background:#fff2cf}.missing{color:#666;background:#eee}dialog{max-width:85vw;max-height:85vh;border:1px solid #8295a3;border-radius:6px}img{max-width:95%;height:auto}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}.card{padding:14px;border:1px solid #cad6df;border-radius:6px}"""
def shell(title,body,js=""):
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>'+STYLE+'</style><body>'+body+('<script>'+js+'</script>' if js else '')+'</body></html>\n'
def esc(x):return html.escape(str(x),quote=True)

def full_tables(ledger):
    nav=[];tablesummary=[]
    labels={"B1_SPAN":"S6 / S6.Objects：完整六跨度与配对原值","B2_SUMMARY":"S4：完整操作原值与基线","B2_PAIRED":"S4：原保存配对比较","B2_REFERENCE":"S4：原保存参考敏感性","B2_BOUND":"S4：原保存下界与数值检查字段"}
    for sid,rows in ROWSETS.items():
        ordered=[rows[k] for k in sorted(rows)]
        cols=list(dict.fromkeys(c for r in ordered for c in r["values"]))
        axes=list(ordered[0]["native_axes"])
        data=[]
        for r in ordered:
            data.append({"line":r["original_line_1based"],"line_sha256":r["line_sha256"],"axes":r["native_axes"],
                "original_status_fields":r["original_status_fields"],"values":r["values"]})
        source=SOURCES[sid]
        path=TAB+"/"+sid+"_complete_v0_1.html"
        count=sum(len(r["values"]) for r in ordered)
        body='<header><h1>'+esc(labels[sid])+'</h1><p>原保存聚合值；点击任一数值查看原字段、原行、原 token 与 W02 引用。</p>'
        body+='<p class="note">全表保留 S/G、所有原生层级及 ALL3_EQUAL。ALL3_EQUAL 是原保存汇总，不是第四操作方向。null 显式保留；未保存资格字段保持 MISSING_FIELD。原 FD 不可判不因制图升级。</p>'
        body+='<details><summary>来源与范围</summary><pre>'+esc(json.dumps(source,ensure_ascii=False,indent=2))+'</pre></details>'
        body+='<p>记录数 '+str(len(ordered))+'；已核对结果数值槽 '+str(count)+'。显示原 token，不舍入。</p></header><div id="filters"></div><p id="summary"></p><button id="prev">上一页</button><button id="next">下一页</button><span id="page"></span><div class="scroll"><table><thead id="head"></thead><tbody id="rows"></tbody></table></div><dialog id="detail"><button onclick="this.parentElement.close()">关闭</button><pre id="detailtext"></pre></dialog>'
        payload=json.dumps({"source":source,"axes":axes,"columns":cols,"rows":data},ensure_ascii=False,separators=(",",":")).replace("</","<\\/")
        js="const D="+payload+";\n"+r"""
let page=0,chosen={},filtered=D.rows;const size=100;
const $=x=>document.getElementById(x),h=x=>String(x).replace(/[&<>"']/g,z=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[z]));
const filterAxes=D.axes.filter(k=>k!=='lambda');
for(const a of filterAxes){const s=document.createElement('select');s.title=a;s.innerHTML='<option value="">'+h(a)+': 全部</option>'+[...new Set(D.rows.map(r=>String(r.axes[a])))].map(v=>'<option>'+h(v)+'</option>').join('');s.onchange=()=>{chosen[a]=s.value;page=0;update()};$('filters').appendChild(s)}
$('head').innerHTML='<tr>'+['原行',...D.axes,...D.columns,'原状态 / null'].map(c=>'<th>'+h(c)+'</th>').join('')+'</tr>';
function inspect(r,c){$('detailtext').textContent=JSON.stringify({source:D.source,original_line_1based:r.line,original_line_sha256:r.line_sha256,native_axes:r.axes,cell:c,original_status_fields:r.original_status_fields},null,2);$('detail').showModal()}
function update(){filtered=D.rows.filter(r=>Object.entries(chosen).every(([k,v])=>!v||String(r.axes[k])===v));$('summary').textContent='当前记录 '+filtered.length+' / 原保存记录 '+D.rows.length+'；每页100行。数据完整嵌入，无外部请求。';const visible=filtered.slice(page*size,(page+1)*size);$('rows').textContent='';
for(const r of visible){const tr=document.createElement('tr');const add=(x,cls)=>{const td=document.createElement('td');td.textContent=x;if(cls)td.className=cls;tr.appendChild(td);return td};add(r.line);for(const a of D.axes)add(r.axes[a]);for(const col of D.columns){const c=r.values[col];if(!c){add('未选择：仅h1行保存配对叶','missing');continue}const td=add(c.raw_json===null?c.state:c.raw_json,'num'+(c.state==='SAVED_NULL'?' null':''));td.onclick=()=>inspect(r,c)}
const states=add('查看原状态 / null','num');states.onclick=()=>inspect(r,null);$('rows').appendChild(tr)}
$('page').textContent='第 '+(page+1)+' / '+Math.max(1,Math.ceil(filtered.length/size))+' 页';$('prev').disabled=page===0;$('next').disabled=(page+1)*size>=filtered.length}
$('prev').onclick=()=>{page--;update()};$('next').onclick=()=>{page++;update()};update();
"""
        textfile(path,shell(labels[sid],body,js))
        lines(TAB+"/"+sid+"_rows_v0_1.jsonl",({"source_id":sid,**r} for r in data))
        nav.append({"source_id":sid,"html":path,"row_data":TAB+"/"+sid+"_rows_v0_1.jsonl","original_rows":len(ordered),"audited_numeric_cells":count})
        tablesummary.append({"artifact_id":"S6.Table" if sid=="B1_SPAN" else "S4.Table."+sid,
            "path":path,"source_id":sid,"source_ledger":LEDGER,"row_count":len(ordered),"result_cell_count":count,
            "all_original_nulls_and_statuses_preserved":True,"ALL3_EQUAL":"Separate original saved aggregate, retained in table",
            "caption":"Complete original saved aggregate slots, native axes, row status and exact W02 citations. No value reconstruction.",
            "script":"scripts/render_w03_b1_b2_v0_1.py"})
    groups=ledger["qualification_registry"]["path_groups"]
    body='<h1>S6：原路径 FD／chain 资格记录</h1><p class="note">原40组背景资格；7组具资格经验匹配与33组不可判保留。不是每个聚合值、每条路径或每个节点的逐点资格。来源为原Q0记录，制图不产生新的科学PASS。</p><table><thead><tr>'
    keys=["group_index","case_id","native_head","endpoint","FD_status","chain_status","source_id","source_pointer"]
    body+="".join("<th>"+esc(k)+"</th>" for k in keys)+"</tr></thead><tbody>"
    for g in groups:body+="<tr>"+"".join("<td class='"+("missing" if k=="FD_status" and str(g[k]).startswith("UNRESOLVED") else "")+"'>"+esc(g.get(k,""))+"</td>" for k in keys)+"</tr>"
    body+='</tbody></table><p>完整来源：evidence/q0_b1_b2_v0_1.json；原组索引同时保留在 W02 各向量资格上下文。</p>'
    textfile(TAB+"/S6_qualification_v0_1.html",shell("S6 原资格",body))
    denom=json.loads((ROOT/ledger["supplemental_row_metadata_registry"]).read_text())
    for x in denom["records"]:
        assert x["original_field"]["raw_json"] is not None
    body='<h1>原路径分母元数据及 null</h1><p class="note">source_denominator 与 raw_link_count 各4032个原始 null 仍保留在 W02 向量／本次完整行表中。下面是单独保存的 denominator_map，不用它替换 null 或新求和。</p><pre>'+esc(json.dumps(denom,ensure_ascii=False,indent=2))+'</pre>'
    textfile(TAB+"/S6_denominator_metadata_v0_1.html",shell("原路径分母元数据",body))
    body='<h1>Table2.PATH：G/q 六跨度函数自身有符号 Skill</h1><p>P_PATH：32源／64链接；主受限行均匀参考；原保存 MEAN5、KD_KI_EQUAL。仅本表路径块；其余主表块未执行。下列值显示完整原 token，没有取绝对值、舍入或重算。</p><table><thead><tr><th>λ</th>'+''.join('<th>'+op+'</th>' for op in OPS)+'</tr></thead><tbody>'
    tablerefs=[]
    for s,label in zip(SPANS,SPAN_LABELS):
        body+="<tr><th>"+label+"</th>"
        for op in OPS:
            r=b1("G","MEAN5","KD_KI_EQUAL","q",op,POLICIES[0],s);c=r["values"]["Skill"]
            body+='<td class="num"><details><summary>'+esc(c["raw_json"])+'</summary><pre>'+esc(json.dumps({"source_id":"B1_SPAN","native_axes":r["native_axes"],"original_line_1based":r["original_line_1based"],"cell":c},ensure_ascii=False,indent=2))+'</pre></details></td>'
            tablerefs.append({"native_axes":r["native_axes"],"original_line_1based":r["original_line_1based"],"cell":c})
        body+="</tr>"
    body+='</tbody></table><p>Skill 为 q 自身的有符号近似收益；它与 h1 对参照的配对 ΔSkill 是不同对象。原组资格仍依 Q0，有不可判组，不宣称严格浮点或逐路径认证。</p>'
    textfile(TAB+"/Table2_PATH_v0_1.html",shell("Table2.PATH",body))
    save(REF+"/Table2_PATH_citations.json",{"artifact_id":"Table2.PATH","path":TAB+"/Table2_PATH_v0_1.html","entries":tablerefs,"scientific_arithmetic":False})
    save(AUD+"/complete_table_coverage.json",{"tables":nav,"total_original_rows":sum(x["original_rows"] for x in nav),
        "total_result_cells":sum(x["audited_numeric_cells"] for x in nav),"numeric_lexeme_display":"Original raw_json strings, no rounding",
        "qualification_table":TAB+"/S6_qualification_v0_1.html","denominator_metadata_table":TAB+"/S6_denominator_metadata_v0_1.html",
        "result_nulls_replaced":0,"all_original_status_fields_copied":True})
    tablesummary.append({"artifact_id":"Table2.PATH","path":TAB+"/Table2_PATH_v0_1.html",
        "source_id":"B1_SPAN","result_cell_count":18,"source_ledger":LEDGER,
        "citations_ref":REF+"/Table2_PATH_citations.json","script":"scripts/render_w03_b1_b2_v0_1.py",
        "caption":"Only the saved G/q/main-restricted-reference 18-slot signed Skill path block; other Table2 blocks are not rendered."})
    return nav,tablesummary

def verify_trace():
    for p in TRACE:
        r=ROWSETS[p["source_id"]][p["original_line_1based"]]
        matches=[c for c in r["values"].values() if c["json_pointer"]==p["json_pointer"]]
        assert len(matches)==1
        c=matches[0]
        assert c["raw_json"]==p["raw_json"] and c["citation_id"]==p["W02_citation_id"]
        assert r["native_axes"]==p["native_axes"]
        if p["original_cell_state"]=="NON_NULL_SAVED_NUMBER":
            assert math.isfinite(float(p["raw_json"]))
    return {"plotted_source_cell_occurrences":len(TRACE),"by_artifact":dict(Counter(x["artifact_id"] for x in TRACE)),
        "original_nulls_imputed":0,"metric_arithmetic":0,"coordinate_rule":"Direct original numeric-token float cast; saved spans/log2 axis and deterministic categorical offsets for display only",
        "text_outside_canvas_count":sum(len(x["text_outside_canvas"]) for x in LAYOUT),"layout_checks":LAYOUT,
        "old_control_files_unchanged":115,"scientific_PASS_created":False}

def old_checks():
    old=json.loads((ROOT/AUD/"old_control_manifest.json").read_text())
    for item in old["artifacts"]:
        b=(ROOT/item["path"]).read_bytes()
        assert len(b)==item["bytes"] and digest(b)==item["sha256"],"Old file modified: "+item["path"]

def bind_transformation(index):
    script="scripts/render_w03_b1_b2_v0_1.py"
    transform={"script":script,"sha256":digest((ROOT/script).read_bytes()),"kind":"DISPLAY_ONLY","scientific_arithmetic":False}
    index["transformation"]=transform
    index["figure_table_trace"]=[]
    for artifact in index["artifacts"]+index["tables"]:
        is_figure=artifact in index["artifacts"]
        index["figure_table_trace"].append({"artifact_id":artifact["artifact_id"],
            "source_data":{"dataset_id":"W02_B1_B2_SAVED_AGGREGATES","file":LEDGER,
                "point_or_table_registry":REF+"/plot_point_citations.jsonl" if is_figure else artifact.get("citations_ref",artifact["path"])},
            "transformation":transform,"caption_claim":artifact["caption"],"supported_manuscript_claims":[],
            "planned_claim_context":"C06 / v0.4 §3.4" if artifact["artifact_id"].startswith(("Fig.4","S6","Table2")) else "C04 / v0.4 §3.3",
            "limitations":["Existing finite exposed-development fixed-AD scope only","Original qualification unresolved/null/missing states retained; no new scientific PASS"]})
    return index

def main():
    if (ROOT/AUD/"receipt.json").exists():
        raise RuntimeError("W03 v0.1 is preserved by its delivery receipt; create a new version for changes")
    for path in [FIG,TAB,AUD,REF]:(ROOT/path).mkdir(parents=True,exist_ok=True)
    old_checks()
    ledger=load()
    main_figures();print("Rendered five main panels",flush=True)
    supplements();print("Rendered three complete-layer supplementary figure books",flush=True)
    nav,tablesummary=full_tables(ledger);print("Rendered complete source tables",flush=True)
    lines(REF+"/plot_point_citations.jsonl",TRACE)
    summary=verify_trace();old_checks()
    save(AUD+"/render_verification.json",summary)
    save(REF+"/figure_citation_index.json",bind_transformation({"schema":"manuscript-W03-figure-source-citations/0.1",
        "W02_ledger":LEDGER,"plot_point_citations":REF+"/plot_point_citations.jsonl",
        "artifacts":ARTIFACTS,"tables":tablesummary,
        "supported_manuscript_claims_status":"No manuscript drafted; locations and C04/C06 are planned only",
        "display_transformations":["numeric token to plotting float","axis bounds/ticks/log2 span coordinate","deterministic seed-layer category offsets"],
        "scientific_transformations":[],"qualification":"Existing fixed-AD finite exposed-development description only; original unresolved/null states retained"}))
    captions="# B1＋B2 W03 图注与表注 v0.1\n\n仅图表文字，不是论文正文。标签采用英文，说明采用中文；期刊格式待确认。\n\n"
    for a in ARTIFACTS:
        captions+="## "+a["artifact_id"]+"\n\n"+a["caption"]+"\n\n"
    captions+="## 完整数值表及资格\n\n五份完整来源表保留全部58,176个已核对结果数值槽、原行和18项引用解析入口；原null与缺失资格字段分别保留。S6资格表保留40组原FD/chain状态，包括33组不可判；它们不是逐点认证。分母元数据另列，不替代行内null。ALL3_EQUAL仅为原保存汇总，不作为第四方向。\n\n## Table2.PATH\n\nG/q、主受限参考、原保存MEAN5与KD_KI_EQUAL下，D/P/DP各六跨度的18个原Skill；完整token与原行引用可展开查看。仅制作此路径块，其他面板的Table2块不在本批范围。\n"
    textfile("docs/B1_B2_W03图注与表注_v0.1.md",captions)
    include="% W03 figures only; no manuscript prose. Paths relative to project root.\n\\usepackage{graphicx}\n"
    for n,label in enumerate(["Fig.3A","Fig.4A","Fig.4B","Fig.4C","Fig.4D"],1):
        include+="% "+label+"\n\\includegraphics[page="+str(n)+",width=\\linewidth]{"+FIG+"/B1_B2_main_figures_v0_1.pdf}\n"
    textfile(FIG+"/inclusion_snippets_v0_1.tex",include)
    body='<h1>B1＋B2 W03 图表包 v0.1</h1><p>v0.4：Fig.3A、Fig.4A—D及S4/S6。直接使用已核对W02槽；没有新科学计算或论文正文。图中零线为基线参考，原FD不可判不升级。</p><p><a href="B1_B2_main_figures_v0_1.pdf">主图 PDF（5页）</a> | <a href="S4_full_operation_v0_1.pdf">S4（12页）</a> | <a href="S6_Objects_span_skill_v0_1.pdf">S6.Objects（12页）</a> | <a href="S6_Paired_delta_skill_v0_1.pdf">S6.Paired（12页）</a></p><div class="cards">'
    for a,stem in zip(ARTIFACTS[:5],["Fig3A","Fig4A","Fig4B","Fig4C","Fig4D"]):
        body+='<div class="card"><h2>'+a["artifact_id"]+'</h2><a href="'+stem+'_v0_1.svg"><img src="'+stem+'_v0_1.png" alt="'+a["artifact_id"]+'"></a><p>'+esc(a["caption"])+'</p></div>'
    body+='</div><h2>完整表及可追溯记录</h2><ul>'
    for n in nav:body+='<li><a href="'+esc(os.path.relpath(ROOT/n["html"],ROOT/FIG))+'">'+esc(n["source_id"])+' 完整表</a>（'+str(n["original_rows"])+'原行；'+str(n["audited_numeric_cells"])+'结果槽）</li>'
    for name in ["Table2_PATH","S6_qualification","S6_denominator_metadata"]:
        body+='<li><a href="../../'+TAB+'/'+name+'_v0_1.html">'+name+'</a></li>'
    body+='</ul><p><a href="../../'+REF+'/figure_citation_index.json">图表引用索引</a> | <a href="../../'+REF+'/plot_point_citations.jsonl">逐绘制点来源</a></p>'
    textfile(FIG+"/index.html",shell("B1+B2 W03",body))
    print(json.dumps({"status":"RENDERED_AND_SOURCE_BOUND","main_panels":5,"supplement_books":3,"supplement_pages":36,
        "source_table_rows":9504,"complete_table_result_cells":58176,"plotted_cell_occurrences":len(TRACE),
        "outside_canvas":summary["text_outside_canvas_count"],"old_files_unchanged":115},ensure_ascii=False),flush=True)

if __name__=="__main__":main()

