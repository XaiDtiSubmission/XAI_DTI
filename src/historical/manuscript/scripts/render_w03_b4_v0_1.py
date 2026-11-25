#!/usr/bin/env python3
"""B4 W03 scientific graphics from sealed W02 aggregate ledgers only.

No inherited aggregate, code, sample, asset, parameter or scientific runtime is
opened. Float casts, limits, colors, categorical offsets and pagination are
display operations only. Scientific values and nulls remain original tokens.
"""
import hashlib
import html
import importlib.util
import itertools
import json
import math
import shutil
import subprocess
from collections import Counter
from io import StringIO
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parents[1]
W02 = "references/numeric_citations_b4_v0_1"
FIG = "figures/w03_b4_v0_1"
TAB = "tables/w03_b4_v0_1"
REF = "references/numeric_citations_b4_w03_v0_1"
AUD = "audit/w03_b4_v0_1"
ARMS = ["G", "S"]
EPS = ["Kd", "Ki", "KD_KI_EQUAL"]
POL = ["FIT_ROW_UNIFORM", "FIT_PROTEIN_INPUT_UNIFORM"]
SEEDS = ["17", "29", "43", "71", "101", "MEAN5"]
FUN = ["q", "h0", "h_aff", "h1"]
OPS = ["D", "P", "DP", "ALL3_EQUAL"]
SIGNS = [-1, 0, 1]
CLASSES = ["C" + str(i) for i in range(8)]
CG = ["C3_DIRECTION", "C7_SAME_SIGN_HARM", "OTHER_C0_C1_C2_C4_C5_C6"]
RG = ["SOURCE_ZERO_PROJECTION", "REVERSAL_OBSERVED", "NOT_OBSERVED_ON_GRID"]
LAMBDAS = ["0.03125", "0.0625", "0.125", "0.25", "0.5", "1.0"]
COLORS = ["#0077BB", "#EE7733", "#009988", "#CC3311", "#AA3377", "#111111"]
MARKERS = ["o", "s", "^", "v", "P", "D"]
ROWS, INDEX, SOURCES = {}, {}, {}
TRACE, LAYOUT, ARTIFACTS, PAGES = [], [], [], []
LEDGER = {}
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.titlesize": 9,
    "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
    "axes.spines.top": False, "axes.spines.right": False, "axes.linewidth": .65,
    "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none", "savefig.dpi": 240,
    "figure.facecolor": "white", "axes.unicode_minus": True})

class Token(str):
    """A JSON number's original lexeme, never a re-encoded scientific float."""

def encode(x):
    if isinstance(x, Token):
        return str(x)
    if isinstance(x, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + encode(v) for k, v in x.items()) + "}"
    if isinstance(x, (list, tuple)):
        return "[" + ",".join(encode(v) for v in x) + "]"
    return json.dumps(x, ensure_ascii=False, allow_nan=False)

def sha(b):
    return hashlib.sha256(b).hexdigest()

def load(p, tokens=False):
    return json.loads((ROOT / p).read_text(), **({"parse_float": Token, "parse_int": Token} if tokens else {}))

def textfile(p, text):
    assert p.startswith((FIG + "/", TAB + "/", REF + "/", AUD + "/", "docs/B4_W03", "planning/w03_b4", "evidence/w03_b4", "README_W03_B4"))
    assert not (ROOT / AUD / "receipt.json").exists(), "B4 W03 v0.1 sealed"
    target = ROOT / p
    if target.exists() and target.read_text() == text:
        return
    if target.exists():
        patch = "*** Begin Patch\n*** Update File: " + p + "\n@@\n"
        patch += "".join("-" + x + "\n" for x in target.read_text().splitlines())
        patch += "".join("+" + x + "\n" for x in text.splitlines()) + "*** End Patch\n"
    else:
        patch = "*** Begin Patch\n*** Add File: " + p + "\n" + "".join("+" + x + "\n" for x in text.splitlines()) + "*** End Patch\n"
    r = subprocess.run([shutil.which("apply_patch")], input=patch, text=True, cwd=ROOT, capture_output=True)
    assert r.returncode == 0, r.stdout + r.stderr

def save(p, x):
    textfile(p, encode(x) + "\n")

def esc(x):
    return html.escape(str(x), quote=True)

def read_ledger():
    global LEDGER
    receipt = load("audit/w02_b4_v0_1/receipt.json")
    for item in receipt["artifacts"]:
        b = (ROOT / item["path"]).read_bytes()
        assert sha(b) == item["sha256"] and len(b) == item["bytes"], item["path"]
    LEDGER = load(W02 + "/ledger.json")
    for source in LEDGER["sources"]:
        sid = source["source_id"]
        SOURCES[sid] = source
        rr = []
        with (ROOT / source["row_registry"]).open() as f:
            for line in f:
                row = json.loads(line, parse_float=Token, parse_int=Token)
                key = (sid, tuple(sorted(row["native_axis_raw_json"].items())))
                assert key not in INDEX
                INDEX[key] = row
                rr.append(row)
        assert len(rr) == source["row_count"]
        ROWS[sid] = rr
    assert sum(len(rr) for rr in ROWS.values()) == 65664
    for d in [FIG, TAB, REF, AUD]:
        (ROOT / d).mkdir(parents=True, exist_ok=True)

def row(sid, arm="G", ep="KD_KI_EQUAL", policy=POL[0], seed="MEAN5", op="D", **extra):
    axes = dict(arm=arm, endpoint_layer=ep, reference_scheme=policy, seed_layer=seed, operation_layer=op, **extra)
    tokens = {k: (str(v) if k == "lambda" else encode(v)) for k, v in axes.items()}
    return INDEX[(sid, tuple(sorted(tokens.items())))]

def get(row, pointer):
    key = pointer.strip("/").split("/")
    node = row["metrics"] if key[0] in row["metrics"] else row["original_metadata"]
    for part in key:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node

def values(rows, pointer, artifact, page, subplot, x_pointer=None):
    """One trace per displayed series, with exact W02 mark references."""
    marks, vals = [], []
    for r in rows:
        original = get(r, pointer)
        state = "SAVED_NULL" if original is None else "NON_NULL_SAVED_NUMBER" if isinstance(original, Token) else "SAVED_NONNUMERIC"
        assert state != "SAVED_NONNUMERIC", (artifact, pointer)
        mark = {"vector_id": r["vector_id"], "registry_line_1based": int(r["registry_line_1based"]),
            "original_line_1based": int(r["original_line_1based"]), "original_raw_json": encode(original), "original_state": state}
        if x_pointer is not None:
            xv = r["native_axes"]["lambda"] if x_pointer == "/lambda" else get(r, x_pointer)
            mark["x_raw_json"] = encode(xv)
        marks.append(mark)
        vals.append(math.nan if original is None else float(str(original)))
    TRACE.append({"binding_id": "B4W03." + str(len(TRACE)+1).zfill(6), "artifact_id": artifact, "page_1based": page,
        "subplot_id": subplot, "source_id": rows[0]["source_id"], "field_pointer": pointer,
        "x_pointer": x_pointer, "marks": marks,
        "transformation": "DIRECT_SAVED_TOKEN_FLOAT_CAST_FOR_DISPLAY; NULL_MASKED_WITH_EXPLICIT_LABEL_NO_IMPUTATION"})
    return vals

def style(ax, ylabel, zero=True):
    if zero:
        ax.axhline(0, color="#737d85", lw=.65, zorder=0)
    ax.grid(axis="y", color="#dfe4e8", lw=.5)
    ax.set_axisbelow(True)
    ax.set_ylabel(ylabel)

def scatter_seeds(ax, rr_by_seed, pointer, artifact, page, sub, labels=OPS):
    for i, rr in enumerate(rr_by_seed):
        yy = values(rr, pointer, artifact, page, sub)
        xx = [j + (i-2.5)*.07 for j in range(len(rr))]
        ax.scatter(xx, yy, color=COLORS[i], marker=MARKERS[i], s=15 if i==5 else 10,
                   linewidth=.35, edgecolors="white", zorder=6 if i==5 else 3)
        for x, y in zip(xx, yy):
            if math.isnan(y):
                ax.plot([x], [.035], "x", transform=ax.get_xaxis_transform(), color=COLORS[i], ms=3, clip_on=False)
    ax.set_xticks(range(len(labels)), labels, fontsize=6.4)
    style(ax, pointer.strip("/"))

def seed_legend(fig):
    h = [Line2D([], [], color=COLORS[i], marker=MARKERS[i], ls="none", ms=4,
                label=seed + (" (saved)" if seed=="MEAN5" else "")) for i, seed in enumerate(SEEDS)]
    fig.legend(handles=h, ncol=6, frameon=False, loc="upper left", bbox_to_anchor=(.064,.907), fontsize=8)

def finish(fig, art, page, pdf, stem=None, family=None):
    # Layout checks are display-only; remove ticks outside visible data axes.
    for ax in fig.axes:
        if not ax.axison:
            continue
        if ax.get_xscale() == "linear":
            lo, hi = sorted(ax.get_xlim())
            ax.set_xticks([v for v in ax.get_xticks() if lo <= v <= hi])
        lo, hi = sorted(ax.get_ylim())
        ax.set_yticks([v for v in ax.get_yticks() if lo <= v <= hi])
    fig.canvas.draw()
    width, height = fig.canvas.get_width_height()
    outside = []
    for t in fig.findobj(matplotlib.text.Text):
        if t.get_visible() and t.get_text():
            b = t.get_window_extent(fig.canvas.get_renderer())
            if b.x0 < -1 or b.y0 < -1 or b.x1 > width+1 or b.y1 > height+1:
                outside.append(t.get_text())
    LAYOUT.append({"artifact_id": art, "page_1based": page, "outside_canvas": outside})
    assert not outside, (art, page, outside)
    pdf.savefig(fig)
    if stem:
        stream = StringIO()
        fig.savefig(stream, format="svg")
        textfile(FIG + "/" + stem + ".svg", stream.getvalue())
        fig.savefig(ROOT / FIG / (stem + ".png"), dpi=240)
    PAGES.append({"artifact_id": art, "page_1based": page, "family": family or art})
    plt.close(fig)

def heatmaps(fig, axes, matrices, title, rowlabels, collabels, signed=False, mass=False, fontsize=7):
    finite = [float(v) for a in matrices for v in a.ravel() if math.isfinite(v)]
    limit = max([abs(v) for v in finite] + [1e-12])
    cm = plt.get_cmap("RdBu_r" if signed else "YlGnBu").copy()
    cm.set_bad("#ebedef")
    im = None
    for ax, matrix in zip(axes, matrices):
        masked = np.ma.masked_invalid(matrix)
        im = ax.imshow(masked, interpolation="nearest", cmap=cm, vmin=-limit if signed else 0,
                       vmax=1 if mass else limit, aspect="auto")
        for i in range(3):
            for j in range(3):
                v = matrix[i,j]
                if math.isnan(v):
                    ax.add_patch(Rectangle((j-.5,i-.5),1,1,fill=False,hatch="///",ec="#a8adb3",lw=.3))
                    label, color = "null", "#626971"
                else:
                    label = "0" if v == 0 else format(v,".3g")
                    ratio = abs(v)/limit if signed else v/(1 if mass else limit)
                    color = "white" if ratio > .65 else "#16232c"
                ax.text(j,i,label,ha="center",va="center",fontsize=fontsize,color=color)
        ax.set_xticks(range(3),collabels,rotation=22,ha="right",fontsize=fontsize)
        ax.set_yticks(range(3),rowlabels,fontsize=fontsize)
        ax.set_xticks([-.5,.5,1.5,2.5],minor=True)
        ax.set_yticks([-.5,.5,1.5,2.5],minor=True)
        ax.grid(which="minor",color="white",lw=1)
        ax.tick_params(which="both",length=0)
    bar = fig.colorbar(im, ax=list(axes), fraction=.018, pad=.025)
    bar.ax.tick_params(labelsize=fontsize)
    bar.set_label(title,fontsize=fontsize)

def main_3b(fig, art, page):
    axes = fig.subplots(2,2)
    fig.subplots_adjust(left=.095,right=.96,bottom=.16,top=.84,wspace=.36,hspace=.62)
    rr = [row("C05_DIAGNOSTIC", op=op, function_id="q") for op in OPS[:3]]
    for ax, fields, labels, title in [
        (axes[0,0],["P_total","H_total"],["P_total","H_total"],"B1  Positive and harmful contributions"),
        (axes[0,1],["H_direction","H_overshoot","H_false_response"],["C3: direction","C7: same-sign harm","C1: false response"],"B2  Named harm amplitudes")]:
        for i,(field,label) in enumerate(zip(fields,labels)):
            yy = values(rr,"/"+field,art,page,title)
            xx = [j+(i-(len(fields)-1)/2)*.20 for j in range(3)]
            ax.bar(xx,yy,width=.18,color=COLORS[i],label=label,edgecolor="white",hatch=["","//",".."][i])
            ax.scatter(xx,yy,s=9,color=COLORS[i],zorder=3)
        ax.set_xticks(range(3),OPS[:3]);style(ax,"Saved contribution magnitude")
        ax.set_title(title,loc="left",fontweight="bold");ax.legend(frameon=False,fontsize=7,loc="upper left")
        ax.margins(y=.22)
    ax = axes[1,0]
    for i,field in enumerate(["Skill_diag","Skill_saved"]):
        yy = values(rr,"/"+field,art,page,"self_signed_Skill")
        ax.scatter([x+(i-.5)*.08 for x in range(3)], yy, s=37, marker=["o","x"][i],
                   facecolors="none" if i==0 else COLORS[1],edgecolors=COLORS[i],color=COLORS[i],label=field)
    ax.set_xticks(range(3),OPS[:3]);style(ax,"Self signed Skill (not DeltaSkill)")
    ax.set_title("B3  Diagnostic and original saved Skill",loc="left",fontweight="bold");ax.legend(frameon=False)
    ax = axes[1,1]
    for i,op in enumerate(OPS[:3]):
        r = [row("C05_CLASSES", op=op, function_id="q",class_id=cl) for cl in CLASSES]
        yy = values(r,"/M",art,page,"class_weight_mass")
        ax.scatter([x+(i-1)*.18 for x in range(8)],yy,marker=MARKERS[i],color=COLORS[i],s=22,label=op)
    ax.set_xticks(range(8),CLASSES);style(ax,"Class weight mass M (not M_I)")
    ax.set_title("B4  All eight classes, including zeros",loc="left",fontweight="bold");ax.legend(frameon=False,ncol=3)

def source_matrix(op, field, art, page, arm="G", ep="KD_KI_EQUAL", policy=POL[0], seed="MEAN5"):
    rr = [row("C05_SOURCE_JOINT",arm,ep,policy,seed,op,gain_sign=g,Skill_diag_sign=s) for g,s in itertools.product(SIGNS,SIGNS)]
    return np.array(values(rr,"/"+field,art,page,op+"."+field)).reshape(3,3)

def main_3c(fig, art, page):
    gs = fig.add_gridspec(3,3,left=.095,right=.91,bottom=.15,top=.84,hspace=.78,wspace=.48,height_ratios=[1,1,.78])
    for i,field in enumerate(["source_mass","Skill_contribution"]):
        aa = [fig.add_subplot(gs[i,j]) for j in range(3)]
        matrices = [source_matrix(op,field,art,page) for op in OPS[:3]]
        heatmaps(fig,aa,matrices,"Source mass" if i==0 else "q Skill contribution",
                 ["g < 0","g = 0","g > 0"],["Skill < 0","Skill = 0","Skill > 0"],signed=i==1,mass=i==0)
        for ax,op in zip(aa,OPS[:3]):ax.set_title(op+" | "+("full-N_e mass" if i==0 else "whole-q contribution"),fontweight="bold")
    for j,op in enumerate(OPS[:3]):
        ax = fig.add_subplot(gs[2,j]);ax.axis("off")
        rr = [row("C05_GAIN_GROUP",op=op,gain_sign=g) for g in SIGNS]
        fields = ["source_mass","Skill_contribution","gain_contribution"]
        data = []
        for field in fields:
            yy = values(rr,"/"+field,art,page,op+".saved_gain_marginal")
            data.append(["0" if v==0 else format(v,".3g") for v in yy])
        t = ax.table(cellText=data,rowLabels=["mass","q Skill contrib.","g contrib."],colLabels=["g < 0","g = 0","g > 0"],
                     cellLoc="center",bbox=[.10,.12,.9,.72])
        t.auto_set_font_size(False);t.set_fontsize(6.8)
        for cell in t.get_celld().values():cell.set_edgecolor("#d4dde3");cell.set_linewidth(.5)
        ax.set_title(op+" | saved g-sign marginals",fontsize=8.2,pad=5)

def curve_rows(op, function="q", arm="G",ep="KD_KI_EQUAL",policy=POL[0],seed="MEAN5"):
    return [row("C07_CURVE",arm,ep,policy,seed,op,function_id=function,k=k) for k in range(33)]

def curve(ax, rr, field, art, page, sub, color, marker, label, lw=1):
    yy = values(rr,"/"+field,art,page,sub,x_pointer="/t")
    xx = [float(str(r["metrics"]["t"])) for r in rr]
    ax.plot(xx,yy,color=color,marker=marker,ms=2.7,lw=lw,label=label,markevery=1)
    for x,y in zip(xx,yy):
        if math.isnan(y):ax.plot([x],[.025],"x",transform=ax.get_xaxis_transform(),color=color,ms=3)
    ax.set_xticks([0,.25,.5,.75,1]);ax.set_xlim(-.015,1.015)
    style(ax,field)

def main_5a(fig, art, page):
    axes = fig.subplots(4,3)
    fig.subplots_adjust(left=.11,right=.96,bottom=.14,top=.84,hspace=.55,wspace=.38)
    for j,op in enumerate(OPS[:3]):
        rr = curve_rows(op)
        for i,field in enumerate(["m","abs_m","u_conditional"]):
            curve(axes[i,j],rr,field,art,page,op+"."+field,COLORS[j],MARKERS[j],op)
        ax = axes[3,j]
        for i,field in enumerate(["qualification_mass","source_zero_mass"]):
            curve(ax,rr,field,art,page,op+".qualification",COLORS[i],MARKERS[i],field)
        ax.set_ylabel("Qualification / zero mass")
        ax.legend(frameon=False,fontsize=6.1,loc="center left")
        for i in range(4):
            if i==0:axes[i,j].set_title(op,fontweight="bold")
            if i==3:axes[i,j].set_xlabel("Saved grid t = k/32")
            else:axes[i,j].set_xticklabels([])

def path_matrix(op,field,art,page,function="q",arm="G",ep="KD_KI_EQUAL",policy=POL[0],seed="MEAN5"):
    rr = [row("C07_SAME_PATH_JOINT",arm,ep,policy,seed,op,function_id=function,class_group_at_lambda1=c,sampled_reversal_group=r)
          for c,r in itertools.product(CG,RG)]
    return np.array(values(rr,"/"+field,art,page,op+"."+field)).reshape(3,3)

def main_5b(fig,art,page):
    gs = fig.add_gridspec(3,3,left=.105,right=.90,bottom=.16,top=.84,hspace=.78,wspace=.48)
    for i,field in enumerate(["M","Skill","conditional/Skill"]):
        aa = [fig.add_subplot(gs[i,j]) for j in range(3)]
        matrices = [path_matrix(op,field,art,page) for op in OPS[:3]]
        heatmaps(fig,aa,matrices,["Path mass M","Unconditional Skill contribution","Within-cell conditional Skill"][i],
                 ["C3","C7","OTHER"],["source-zero","observed","not seen"],signed=i>0,mass=i==0)
        for ax,op in zip(aa,OPS[:3]):ax.set_title(op+" | "+["all path cells","full denominator","conditional; null kept"][i],fontweight="bold",fontsize=8)
    fig.text(.075,.093,"C3: direction error | C7: same-sign harmful overshoot | OTHER: C0/C1/C2/C4/C5/C6",fontsize=7.2)

def main_figures():
    path = FIG + "/B4_main_figures_v0_1.pdf"
    drawers = [("Fig.3B", main_3b, (8.6,7.0), "Full O_SRC | diagnostic quantities and saved baselines", "C05"),
               ("Fig.3C", main_3c, (8.7,8.4), "Full O_SRC | g belongs bar_qI; Skill belongs whole q", "C05"),
               ("Fig.5A", main_5a, (8.5,9.0), "Restricted path panel | signed projection, amplitude and alignment", "C07"),
               ("Fig.5B", main_5b, (8.6,9.0), "Restricted path panel | own lambda=1 class x sampled reversal", "C07")]
    with PdfPages(ROOT/path,metadata={"Title":"B4: saved diagnostics and path behavior","Author":""}) as pdf:
        for page,(art,draw,size,title,claim) in enumerate(drawers,1):
            fig = plt.figure(figsize=size)
            fig.suptitle(art+" | "+title,x=.075,y=.965,ha="left",fontsize=12,fontweight="bold")
            fig.text(.075,.915,"G | q | saved MEAN5 | Kd/Ki equal | "+("full" if claim=="C05" else "restricted")+" row-uniform reference",fontsize=8)
            draw(fig,art,page)
            fig.text(.075,.065,"O_SRC: 2,242 sources / 322,659 links" if claim=="C05" else "Path panel: 32 sources / 64 links | fixed 33 nodes; no continuous-boundary claim",fontsize=7.5)
            fig.text(.075,.043,"Original signed values and nulls retained. Color/position scaling is display only; exact tokens and statuses: companion tables.",fontsize=7)
            fig.text(.075,.023,"Finite exposed-development evidence; no new statistics, scientific PASS, causal attribution or cross-panel gain ratio.",fontsize=7,color="#465561")
            finish(fig,art,page,pdf,art.replace(".","")+"_v0_1")
            ARTIFACTS.append({"artifact_id":art,"claim_id":claim,"pdf":path,"page_1based":page,
                "svg":FIG+"/"+art.replace(".","")+"_v0_1.svg","png":FIG+"/"+art.replace(".","")+"_v0_1.png"})

def supp_base(art,arm,ep,policy,page,function=None,seed_legend_on=True):
    fig = plt.figure(figsize=(11.7,8.3))
    fig.suptitle(art,x=.065,y=.969,ha="left",fontsize=13,fontweight="bold")
    scope = "full O_SRC 2242 / 322659" if art.startswith("S5") else "restricted path panel 32 / 64"
    policy_label = "row-uniform" if policy==POL[0] else "protein-input-uniform"
    fig.text(.07,.93,f"{scope} | {arm} | {ep} | {policy_label}"+(" | "+function if function else "")+f" | page {page}",fontsize=8.5)
    if seed_legend_on:seed_legend(fig)
    fig.text(.07,.038,"Original saved layers only. x near floor = original null (never zero). Complete exact tokens, original flags and counts: companion tables.",fontsize=7.3)
    fig.text(.07,.02,"No independent biological-seed interpretation, interpolation certification, new normalization, uncertainty estimate or scientific PASS.",fontsize=7.3,color="#4b5863")
    return fig

def generic_grid(fig,fields,functions=FUN,rows=None):
    axes = fig.subplots(len(fields),len(functions),squeeze=False)
    fig.subplots_adjust(left=.075,right=.98,bottom=.115,top=.82,hspace=.68,wspace=.37)
    for i,field in enumerate(fields):
        for j,function in enumerate(functions):
            axes[i,j].set_title(function+" | "+field,fontsize=8)
    return axes

def s5_book():
    path=FIG+"/S5_figures_v0_1.pdf";page=0;sections=[]
    with PdfPages(ROOT/path,metadata={"Title":"S5 B4: diagnostics, classes, source joint and paired accounts","Author":""}) as pdf:
        for family,fields in [("S5.Diagnostic",["P_total","H_total","Skill_diag"]),
                              ("S5.Carry",["E_diag_minus_saved","Skill_diag_minus_saved","max_abs_eps_replay_difference"])]:
            sections.append({"artifact_id":family,"start_page":page+1})
            for arm,ep,pol in itertools.product(ARMS,EPS,POL):
                page+=1;fig=supp_base(family,arm,ep,pol,page);aa=generic_grid(fig,fields)
                for j,function in enumerate(FUN):
                    by_seed=[[row("C05_DIAGNOSTIC",arm,ep,pol,seed,op,function_id=function) for op in OPS] for seed in SEEDS]
                    for i,field in enumerate(fields):scatter_seeds(aa[i,j],by_seed,"/"+field,family,page,function+"."+field)
                finish(fig,family,page,pdf,family=family)
        for family,field in [("S5.ClassMass","M"),("S5.ClassHarm","H")]:
            sections.append({"artifact_id":family,"start_page":page+1})
            for arm,ep,pol in itertools.product(ARMS,EPS,POL):
                page+=1;fig=supp_base(family,arm,ep,pol,page);aa=fig.subplots(4,4)
                fig.subplots_adjust(left=.075,right=.98,bottom=.115,top=.82,hspace=.8,wspace=.38)
                for i,function in enumerate(FUN):
                    for j,op in enumerate(OPS):
                        by_seed=[[row("C05_CLASSES",arm,ep,pol,seed,op,function_id=function,class_id=cl) for cl in CLASSES] for seed in SEEDS]
                        scatter_seeds(aa[i,j],by_seed,"/"+field,family,page,function+"."+op,CLASSES)
                        aa[i,j].set_title(function+" / "+op,fontsize=8)
                finish(fig,family,page,pdf,family=family)
        family="S5.SourceJoint";sections.append({"artifact_id":family,"start_page":page+1})
        for arm,ep,pol in itertools.product(ARMS,EPS,POL):
            page+=1;fig=supp_base(family+" | q | saved MEAN5",arm,ep,pol,page,seed_legend_on=False)
            gs=fig.add_gridspec(3,4,left=.08,right=.91,bottom=.14,top=.84,hspace=.78,wspace=.48)
            for i,field in enumerate(["source_mass","Skill_contribution","gain_contribution"]):
                aa=[fig.add_subplot(gs[i,j]) for j in range(4)]
                mm=[source_matrix(op,field,family,page,arm,ep,pol) for op in OPS]
                heatmaps(fig,aa,mm,field,["g < 0","g = 0","g > 0"],["Skill < 0","Skill = 0","Skill > 0"],signed=i>0,mass=i==0,fontsize=6)
                for ax,op in zip(aa,OPS):ax.set_title(op,fontsize=8)
            fig.text(.08,.083,"g: original bar_qI deletion; Skill: whole q. All raw seed layers, saved marginals and nested classes retained in complete tables.",fontsize=7.6)
            finish(fig,family,page,pdf,family=family)
        family="S5.Paired";sections.append({"artifact_id":family,"start_page":page+1})
        fields=["delta_E_saved","delta_E_diag","delta_B","delta_P_total","delta_H_total","delta_Skill_diag","residual_error","residual_skill"]
        for arm,ep,pol,comparator in itertools.product(ARMS,EPS,POL,["h0","h_aff"]):
            page+=1;fig=supp_base(family+" | h1 minus "+comparator,arm,ep,pol,page);aa=fig.subplots(2,4)
            fig.subplots_adjust(left=.075,right=.98,bottom=.14,top=.80,hspace=.75,wspace=.38)
            by_seed=[[row("C05_PAIRED",arm,ep,pol,seed,op,comparator_id=comparator) for op in OPS] for seed in SEEDS]
            for ax,field in zip(aa.ravel(),fields):
                scatter_seeds(ax,by_seed,"/"+field,family,page,comparator+"."+field);ax.set_title(field,fontsize=8)
            fig.text(.075,.08,"Saved h1-minus-comparator accounts. Signed residuals retained; no R, class migration or causal decomposition computed.",fontsize=8)
            finish(fig,family,page,pdf,family=family)
    assert page==84
    ARTIFACTS.append({"artifact_id":"S5.B4","pdf":path,"page_count":page,"sections":sections,"claim_id":"C05"})
    print("S5 PDF complete: 84 pages",flush=True)

def s7_book():
    path=FIG+"/S7_figures_v0_1.pdf";page=0;sections=[]
    with PdfPages(ROOT/path,metadata={"Title":"S7 B4: path projections, own path joint cells and cancellations","Author":""}) as pdf:
        family="S7.Curves";sections.append({"artifact_id":family,"start_page":page+1})
        for arm,ep,pol,function in itertools.product(ARMS,EPS,POL,FUN):
            page+=1;fig=supp_base(family,arm,ep,pol,page,function);aa=fig.subplots(4,4)
            fig.subplots_adjust(left=.075,right=.98,bottom=.12,top=.81,hspace=.6,wspace=.36)
            for j,op in enumerate(OPS):
                for s,seed in enumerate(SEEDS):
                    rr=curve_rows(op,function,arm,ep,pol,seed)
                    for i,field in enumerate(["m","abs_m","u_conditional","qualification_mass"]):
                        curve(aa[i,j],rr,field,family,page,function+"."+op+"."+field,COLORS[s],MARKERS[s],seed,lw=1.3 if s==5 else .65)
                aa[0,j].set_title(op,fontsize=8)
                for i in range(3):aa[i,j].set_xticklabels([])
                aa[3,j].set_xlabel("Saved t = k/32",fontsize=7)
            finish(fig,family,page,pdf,family=family)
            if page%12==0:print("S7 curves: page "+str(page),flush=True)
        family="S7.SamePathJoint";sections.append({"artifact_id":family,"start_page":page+1})
        for arm,ep,pol,function in itertools.product(ARMS,EPS,POL,FUN):
            page+=1;fig=supp_base(family+" | saved MEAN5",arm,ep,pol,page,function,False)
            gs=fig.add_gridspec(3,4,left=.095,right=.91,bottom=.15,top=.84,hspace=.78,wspace=.47)
            for i,field in enumerate(["M","Skill","conditional/Skill"]):
                aa=[fig.add_subplot(gs[i,j]) for j in range(4)]
                mm=[path_matrix(op,field,family,page,function,arm,ep,pol) for op in OPS]
                heatmaps(fig,aa,mm,field,["C3","C7","OTHER"],["source-zero","observed","not seen"],signed=i>0,mass=i==0,fontsize=6)
                for ax,op in zip(aa,OPS):ax.set_title(op,fontsize=8)
            fig.text(.08,.085,"Own path lambda=1 class; OTHER retains C0/C1/C2/C4/C5/C6. Raw seed cells and all other saved fields remain in complete tables.",fontsize=7.6)
            finish(fig,family,page,pdf,family=family)
        family="S7.Cancellation";sections.append({"artifact_id":family,"start_page":page+1})
        fields=["C_increment","C_projected32","resolution_difference","endpoint_integral_residual"]
        for arm,ep,pol in itertools.product(ARMS,EPS,POL):
            page+=1;fig=supp_base(family,arm,ep,pol,page);aa=generic_grid(fig,fields)
            for j,function in enumerate(FUN):
                by_seed=[[row("C07_PATH",arm,ep,pol,seed,op,function_id=function) for op in OPS] for seed in SEEDS]
                for i,field in enumerate(fields):scatter_seeds(aa[i,j],by_seed,"/"+field,family,page,function+"."+field)
            finish(fig,family,page,pdf,family=family)
        family="S7.FirstObservedHistogram";sections.append({"artifact_id":family,"start_page":page+1})
        for arm,ep,pol in itertools.product(ARMS,EPS,POL):
            page+=1;fig=supp_base(family+" | saved MEAN5",arm,ep,pol,page,seed_legend_on=False);aa=fig.subplots(4,4)
            fig.subplots_adjust(left=.075,right=.98,bottom=.14,top=.84,hspace=.75,wspace=.37)
            for i,function in enumerate(FUN):
                for j,op in enumerate(OPS):
                    r=row("C07_PATH",arm,ep,pol,"MEAN5",op,function_id=function)
                    yy=[]
                    for pos in range(32):yy.extend(values([r],"/first_observed_reversal_mass_histogram/"+str(pos),family,page,function+"."+op))
                    aa[i,j].bar(range(1,33),yy,width=.8,color=COLORS[j],edgecolor="none")
                    aa[i,j].set_xticks([1,8,16,24,32]);style(aa[i,j],"Saved mass");aa[i,j].set_title(function+" / "+op,fontsize=8)
            fig.text(.075,.085,"Native bin j maps to first observed node k=j+1, t=k/32: sealed static source binding only. Not-observed/source-zero masses remain separate.",fontsize=7.5)
            finish(fig,family,page,pdf,family=family)
        family="S7.ReadoutCancellation";sections.append({"artifact_id":family,"start_page":page+1})
        for arm,ep,pol in itertools.product(ARMS,EPS,POL):
            page+=1;fig=supp_base(family+" | q",arm,ep,pol,page);aa=fig.subplots(3,4)
            fig.subplots_adjust(left=.075,right=.98,bottom=.13,top=.81,hspace=.75,wspace=.37)
            for j,op in enumerate(OPS):
                for i,field in enumerate(["C_readout_r","C_readout_A","C_readout_m"]):
                    ax=aa[i,j]
                    for s,seed in enumerate(SEEDS):
                        rr=[row("C07_READOUT",arm,ep,pol,seed,op,function_id="q",**{"lambda":la}) for la in LAMBDAS]
                        yy=values(rr,"/"+field,family,page,op+"."+field,x_pointer="/lambda")
                        ax.plot([float(la) for la in LAMBDAS],yy,color=COLORS[s],marker=MARKERS[s],ms=3,lw=1.3 if s==5 else .7)
                    ax.set_xscale("log",base=2);ax.set_xticks([float(la) for la in LAMBDAS],["1/32","1/16","1/8","1/4","1/2","1"],fontsize=6)
                    style(ax,field);ax.set_title(op+" | "+field,fontsize=8)
            fig.text(.075,.08,"Saved q two-head difference cancellation; separate from increment and projection-area cancellation. Six saved spans; no new normalized ratio.",fontsize=7.7)
            finish(fig,family,page,pdf,family=family)
    assert page==132
    ARTIFACTS.append({"artifact_id":"S7.B4","pdf":path,"page_count":page,"sections":sections,"claim_id":"C07"})
    print("S7 PDF complete: 132 pages",flush=True)

CSS="""body{font:14px/1.55 system-ui,sans-serif;color:#1c303e;margin:28px;max-width:1600px}a{color:#006b99}h1{font-size:26px}h2{font-size:19px}table{border-collapse:collapse;font-size:11px;width:100%}td,th{border-bottom:1px solid #d6dfe5;padding:7px;text-align:left;vertical-align:top;white-space:nowrap}th{position:sticky;top:0;background:#edf4f7}.scroll{overflow:auto;max-height:70vh}.null{background:#fff2cf;color:#775718}.token{font-family:ui-monospace,monospace;max-width:290px;overflow:hidden;text-overflow:ellipsis;cursor:pointer}.token:hover{background:#e6f6ff}select,button{padding:6px;margin:4px}pre{white-space:pre-wrap;word-break:break-word}dialog{max-width:88vw;max-height:85vh;border:1px solid #829aa8;border-radius:8px}.note{padding:12px 16px;background:#eef4f7;border-left:3px solid #738e9e}.grid{display:grid;grid-template-columns:repeat(2,minmax(300px,1fr));gap:22px}.card{border:1px solid #d4dfe5;padding:14px}img{width:100%;height:auto}.sub{color:#546977}"""

def shell(title,body,js=""):
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+CSS+'</style><body>'+body+('<script>'+js+'</script>' if js else '')+'</body></html>\n'

def complete_tables():
    tables=[]
    for sid,rr in ROWS.items():
        source=SOURCES[sid];cols=source["metric_fields"];axes=list(source["axis_enums"])
        records=[]
        for r in rr:
            records.append({"id":r["vector_id"],"axes":r["native_axes"],"axis_raw":r["native_axis_raw_json"],
                "line":int(r["original_line_1based"]),"hash":r["original_line_sha256"],
                "values":{f:encode(r["metrics"][f]) for f in cols},
                "metadata":{f:encode(v) for f,v in r["original_metadata"].items()},
                "field_checks":r["field_checks"],"exceptions":r["metric_leaf_exceptions"],"qualification":r["qualification"]})
        payload=encode({"source":source,"axes":axes,"columns":cols,"common":LEDGER["common_citation_context"],"rows":records}).replace('</','<\\/')
        body='<h1>'+esc(("S5" if sid.startswith("C05") else "S7")+" | "+sid+" 完整已核对聚合表")+'</h1>'
        body+='<p>全部 '+str(len(rr))+' 条原行；原数值token、null、原状态及原生轴完整保留。点击单元格查看原文件/SHA、原字段与行号、对象和引用。</p>'
        body+='<p class="note">g属于原bar_qI锚定删减；Skill属于整个q。诊断量与原保存量、完整O_SRC与受限路径分开。harm_proportions和class_MPH只按原位置显示，未确认标签不猜测；路径直方图bin已有静态绑定。null不补0，行缺省PASS不补造。页面完全本地，无外部请求。</p>'
        body+='<div id="filters"></div><p id="summary"></p><button id="prev">上一页</button><button id="next">下一页</button><span id="page"></span><div class="scroll"><table><thead id="head"></thead><tbody id="rows"></tbody></table></div><dialog id="detail"><button onclick="this.parentElement.close()">关闭</button><pre id="detailtext"></pre></dialog>'
        js='const D='+payload+';\n'+r'''
const $=x=>document.getElementById(x),esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let chosen={},page=0,filtered=D.rows;const size=40;
for(const axis of D.axes){const s=document.createElement('select');s.title=axis;s.innerHTML='<option value="">'+axis+': 全部</option>'+[...new Set(D.rows.map(r=>r.axis_raw[axis]))].map(v=>'<option value="'+esc(v)+'">'+esc(v)+'</option>').join('');s.onchange=()=>{chosen[axis]=s.value;page=0;update()};$('filters').appendChild(s)}
$('head').innerHTML='<tr>'+['原行',...D.axes,...D.columns,'原状态/分母/资格'].map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr>';
function inspect(r,f){let fn=r.axes.function_id||'q';if(D.source.source_id==='C05_PAIRED')fn='h1 minus '+r.axes.comparator_id;if(['C05_SOURCE_JOINT','C05_GAIN_GROUP'].includes(D.source.source_id))fn=f&&f.startsWith('gain_')?'bar_qI anchored deletion g':f&&f.startsWith('Skill')?'whole q=h1-h_aff':'g_bar_qI / Skill_q joint grouping; exact field role preserved';const citation={...D.common,manuscript_version:'B4_W03_FIGURES_TABLES_v0.1_NO_BODY',claim_id:D.source.claim_id,inherited_source_path:D.source.inherited_source_path,source_sha256:D.source.source_sha256,json_pointer_or_line_1based:{line_1based:r.line,json_pointer_in_original_line:f?'/'+f:'original metadata',original_line_sha256:r.hash},panel:D.source.panel,function_or_contrast:fn,operation:r.axes.operation_layer,reference_policy:r.axes.reference_scheme,seed_layer:r.axes.seed_layer,endpoint_layer:r.axes.endpoint_layer,saved_value_locator:{W02_registry:D.source.row_registry,vector_id:r.id,field:f},display_precision:'ORIGINAL_JSON_TOKEN_UNROUNDED',transcription_check_status:'EXACT_W02_TOKEN_DIRECT_DISPLAY',figure_or_supplement_id:(D.source.panel==='P_OSRC'?'S5':'S7')+'.Table.'+D.source.source_id};$('detailtext').textContent=JSON.stringify({citation,original_token:f?r.values[f]:null,native_axes:r.axes,original_metadata_tokens:r.metadata,field_checks:r.field_checks,nested_null_or_tag_states:r.exceptions,original_qualification:r.qualification,source:D.source},null,2);$('detail').showModal()}
function update(){filtered=D.rows.filter(r=>Object.entries(chosen).every(([k,v])=>!v||r.axis_raw[k]===v));$('summary').textContent='当前 '+filtered.length+' / '+D.rows.length+' 原行；每页40行。';$('rows').textContent='';for(const r of filtered.slice(page*size,(page+1)*size)){const tr=document.createElement('tr'),cell=(s,c='')=>{const td=document.createElement('td');td.textContent=s;td.className=c;tr.appendChild(td);return td};cell(r.line);for(const a of D.axes)cell(r.axis_raw[a]);for(const f of D.columns){const t=r.values[f];const td=cell(t,'token'+(t==='null'?' null':''));td.title=t;td.onclick=()=>inspect(r,f)}const td=cell('查看原状态/分母/引用','token');td.onclick=()=>inspect(r,null);$('rows').appendChild(tr)}$('page').textContent='第 '+(page+1)+' / '+Math.max(1,Math.ceil(filtered.length/size))+' 页';$('prev').disabled=page===0;$('next').disabled=(page+1)*size>=filtered.length}
$('prev').onclick=()=>{page--;update()};$('next').onclick=()=>{page++;update()};update();
'''
        path=TAB+"/"+sid+"_complete_v0_1.html"
        textfile(path,shell(sid+" 完整表",body,js))
        tables.append({"artifact_id":("S5.Table." if sid.startswith("C05") else "S7.Table.")+sid,"source_id":sid,
            "html":path,"row_count":len(rr),"W02_registry":source["row_registry"],"original_scalar_tokens_and_nulls_preserved":True})
        print("Complete table "+sid+": "+str(len(rr))+" rows",flush=True)
    save(AUD+"/complete_table_coverage.json",{"schema":"manuscript-B4-W03-complete-table-coverage/0.1","tables":tables,
        "row_count":65664,"result_non_null_numbers":913570,"result_nulls":80894,"all_original_metadata_and_qualifications_retained":True,
        "unconfirmed_array_labels":"harm_proportions and class_MPH native positions only","new_or_imputed_values":0})
    return tables

def final_metadata(tables):
    textfile(REF+"/plot_bindings.jsonl","".join(encode(t)+"\n" for t in TRACE))
    save(REF+"/figure_citation_index.json",{"schema":"manuscript-B4-W03-series-citation-index/0.1","W02_ledger":W02+"/ledger.json",
        "sources":SOURCES,"required_fields":LEDGER["required_fields"],"plot_bindings":REF+"/plot_bindings.jsonl",
        "series_bindings":len(TRACE),"table_bindings":tables,"artifacts":ARTIFACTS,
        "resolution":"For each mark join source_id + W02 registry line/vector_id, exact field_pointer and original row line/SHA; resolve W02 all18 contexts, override manuscript_version and figure_or_supplement_id by this binding. g/Skill object roles come from exact W02 field role.",
        "display_precision":"Direct original token float cast for coordinates; .3g annotations and axis/color/categorical layout only. Full exact tokens in companion tables.",
        "position_binding_addendum":"audit/w02_b4_v0_1/static_position_binding_addendum.json",
        "no_new_scientific_values_or_arithmetic":True,"old_W02_ledger_changed":False})
    save(AUD+"/render_verification.json",{"schema":"manuscript-B4-W03-render-layout-check/0.1","page_count":len(PAGES),"pages":PAGES,
        "text_canvas_checks":LAYOUT,"out_of_canvas_count":sum(len(x["outside_canvas"]) for x in LAYOUT),
        "matplotlib":matplotlib.__version__,"scientific_requalification":False})
    save(AUD+"/runtime_versions.json",{"python":str(Path(__import__('sys').executable)),"matplotlib":matplotlib.__version__,
        "purpose":"Existing own writing-only plotting runtime; no inherited scientific environment or research script"})
    cards=[]
    for a in ARTIFACTS[:4]:
        cards.append('<div class="card"><h2>'+a['artifact_id']+'</h2><a href="'+Path(a['svg']).name+'"><img src="'+Path(a['png']).name+'" alt="'+a['artifact_id']+'"></a><p><a href="'+Path(a['svg']).name+'">SVG</a> · <a href="'+Path(a['png']).name+'">PNG</a> · <a href="B4_main_figures_v0_1.pdf#page='+str(a['page_1based'])+'">PDF对应页</a></p></div>')
    body='<h1>B4 W03 · 失效诊断与路径行为</h1><p class="sub">v0.4 / C05、C07 · 仅已核对聚合值的显示；无补算、样本载荷或论文正文。</p>'
    body+='<p class="note"><b>g属于bar_qI锚定删减，Skill属于整个q。</b> 完整O_SRC与受限路径面板分开；诊断量与旧保存量分开。null和原不可判资格保留。harm_proportions与class_MPH未确认标签只保留原生位置；直方图bin沿已封存静态绑定。</p>'
    body+='<p><a href="B4_main_figures_v0_1.pdf">四个主图面板 PDF（4页）</a> · <a href="S5_figures_v0_1.pdf">S5 PDF（84页）</a> · <a href="S7_figures_v0_1.pdf">S7 PDF（132页）</a> · <a href="../../docs/B4_W03图注与表注_v0.1.md">中文图注／表注</a> · <a href="../../docs/B4_W03图表交付_v0.1.md">交付与核验</a></p><div class="grid">'+''.join(cards)+'</div>'
    body+='<h2>补充图索引</h2><table><tr><th>图组</th><th>PDF起始页</th></tr>'
    for a in ARTIFACTS[4:]:
        for s in a['sections']:body+='<tr><td>'+s['artifact_id']+'</td><td><a href="'+Path(a['pdf']).name+'#page='+str(s['start_page'])+'">第 '+str(s['start_page'])+' 页</a></td></tr>'
    body+='</table><h2>九份完整补充表（可按原轴筛选、分页、展开引用）</h2><ul>'
    for t in tables:body+='<li><a href="../../'+t['html']+'">'+t['source_id']+'</a> · '+str(t['row_count'])+' 原行</li>'
    body+='</ul><p><a href="../../references/numeric_citations_b4_w03_v0_1/ledger.json">图表数值引用覆盖账本</a> · <a href="../../evidence/w03_b4_issues_v0_1.json">未决及可用范围</a> · <a href="../../planning/w03_b4_mapping_v0_1.json">映射增量</a></p>'
    textfile(FIG+"/index_v0_1.html",shell("B4 W03 图表总览",body))

def main():
    assert not (ROOT/AUD/"receipt.json").exists()
    read_ledger()
    tables=(load(AUD+"/complete_table_coverage.json")["tables"] if "--reuse-tables" in __import__('sys').argv else complete_tables())
    main_figures();print("Four main panels complete",flush=True)
    s5_book();s7_book();final_metadata(tables)
    print(encode({"status":"B4_W03_RENDERED_PENDING_FIDELITY_AND_VISUAL_REVIEW","pages":len(PAGES),"series_bindings":len(TRACE)}),flush=True)

if __name__=="__main__":
    main()
