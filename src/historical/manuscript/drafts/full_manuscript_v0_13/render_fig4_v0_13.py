"""Render Figure 4 and record displayed values from the sealed aggregates."""

import csv
import hashlib
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, StrMethodFormatter


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ORIGINAL = ROOT / (
    "inherited/manuscript_handoff_v0_1/snapshots/bottleneck/results/"
    "smiles_gine_bottleneck_fr40_path_span_v0_2/run_001"
)
FOLLOWUP = ROOT / "output/manuscript_path_recheck_v0_1/run_001"
CAPTION_COUNTS = {"Original": (32, 64), "Follow-up": (128, 256)}
SPANS = (1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2, 1)
SPAN_LABELS = ("1/32", "1/16", "1/8", "1/4", "1/2", "1")
EQUAL = "KD_KI_EQUAL"
MAIN = "FIT_ROW_UNIFORM"
SENSITIVITY = "FIT_PROTEIN_INPUT_UNIFORM"
OPERATIONS = {
    "D": ("Drug (D)", "#0F4D92", "o"),
    "P": ("Protein (P)", "#167565", "s"),
    "DP": ("Joint (DP)", "#BD591F", "^"),
}
ORIGINAL_HASHES = {
    "span_summary.jsonl": "9f350c2c635e8ce6d41dd40e580322ddfa1b6e99bc9a69347ce57363b42d09aa",
    "panel_composition.json": "cbd979cb87054195a5acf9d41855da2899f8902dc53b58da9fbc3bc279650f85",
}
INK, GRID, ZERO = "#20262D", "#E0E5EA", "#65717C"


def verified(path, digest):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"Saved evidence does not match its registered hash: {path}")
    return raw


class Sources:
    def __init__(self):
        seal = json.loads((FOLLOWUP / "run_seal.json").read_bytes())
        if seal["status"] != "PRIVATE_FINITE_DEVELOPMENT_ACCEPTED":
            raise ValueError("Follow-up evidence lacks private acceptance")
        self.hashes = {
            "Original": ORIGINAL_HASHES,
            "Follow-up": {name: entry["sha256"] for name, entry in seal["artifacts"].items()},
        }
        self.roots = {"Original": ORIGINAL, "Follow-up": FOLLOWUP}
        self.rows = {}
        self.ledger = []
        for panel, root in self.roots.items():
            expected = 6912 if panel == "Original" else 5184
            rows = verified(root / "span_summary.jsonl", self.hashes[panel]["span_summary.jsonl"]).splitlines()
            if len(rows) != expected:
                raise ValueError(f"Unexpected saved row count for {panel}")
            operation_key = "operation_layer" if panel == "Original" else "operation"
            for line_number, raw in enumerate(rows, start=1):
                row = json.loads(raw)
                if row["seed_layer"] != "MEAN5" or row[operation_key] not in OPERATIONS:
                    continue
                key = (panel, row["arm"], row["reference_scheme"], row["function_id"],
                       row[operation_key], row["lambda"], row["endpoint_layer"])
                if key in self.rows:
                    raise ValueError(f"Duplicate saved row key: {key}")
                self.rows[key] = (line_number, row)

    def record(self, use, panel, filename, field, value, *,
               line="", arm="", reference="", function="", operation="", span="", endpoint=""):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Nonfinite value for {filename}:{line}:{field}")
        sources, links = CAPTION_COUNTS[panel]
        self.ledger.append({
            "use": use, "panel": panel, "sources": sources, "links": links, "arm": arm,
            "seed_layer": "MEAN5" if arm else "", "reference_scheme": reference,
            "function_id": function, "operation": operation, "lambda": span,
            "endpoint_layer": endpoint, "source": str((self.roots[panel] / filename).relative_to(ROOT)),
            "sha256": self.hashes[panel][filename], "line": line, "field": ".".join(field), "full_precision": value,
        })
        return value

    def value(self, panel, operation, span, endpoint, field, use, *,
              arm="G", function="q", reference=MAIN):
        line, data = self.rows[panel, arm, reference, function, operation, span, endpoint]
        for key in field:
            data = data[key]
        return self.record(use, panel, "span_summary.jsonl", field, data, line=line,
                           arm=arm, reference=reference, function=function,
                           operation=operation, span=span, endpoint=endpoint)

    def control(self, panel, filename, field, use, endpoint=""):
        data = json.loads(verified(self.roots[panel] / filename, self.hashes[panel][filename]))
        for key in field:
            data = data[key]
        return self.record(use, panel, filename, field, data, endpoint=endpoint)

    def gather(self):
        curves, full_drug, half_drug = {}, {}, {}
        for panel in CAPTION_COUNTS:
            curves[panel] = {}
            for op in OPERATIONS:
                curves[panel][op] = []
                for span in SPANS:
                    value = self.value(panel, op, span, EQUAL, ("Skill",), "Fig. 4A–B / Table 2")
                    curves[panel][op].append(value)
                    if panel == "Follow-up":
                        for field in ("E", "B"):
                            self.value(panel, op, span, EQUAL, (field,), "Results 3.4 / Table S11")
                        self.value(panel, op, span, EQUAL, ("Skill",), "Results 3.4 / S10.3 (sensitivity)",
                                   reference=SENSITIVITY)
            full_drug[panel] = [curves[panel]["D"][-1],
                                self.value(panel, "D", 1, EQUAL, ("Skill",),
                                           "Results 3.4 / Fig. 4C", function="h1")]
            for comparator in ("h0", "h_aff"):
                full_drug[panel].append(self.value(
                    panel, "D", 1, EQUAL, ("h1_paired_differences", comparator, "DeltaSkill"),
                    "Results 3.4 / Fig. 4C", function="h1",
                ))
                for field in ("DeltaE", "DeltaB", "DeltaSkill"):
                    self.value(panel, "D", 1 / 4, EQUAL, ("h1_paired_differences", comparator, field),
                               "Results 3.4 / Table S12", function="h1")
            half_drug[panel] = []
            for endpoint in ("Kd", "Ki", EQUAL):
                for field in ("E", "B"):
                    self.value(panel, "D", 1 / 2, endpoint, (field,), "Results 3.4 / Table 2")
                half_drug[panel].append(self.value(
                    panel, "D", 1 / 2, endpoint, ("Skill",), "Results 3.4 / Fig. 4D / Table 2",
                ))
                if panel == "Follow-up":
                    self.value(panel, "D", 1 / 2, endpoint, ("Skill",), "Results 3.4 / S10.3 (sensitivity)",
                               reference=SENSITIVITY)
                if endpoint != EQUAL:
                    fields = ("N_selected_actual", "link_denominator") if panel == "Original" else (
                        "O_SRC_sources", "eligible_before", "eligible_after", "selected_sources",
                        "selected_links", "unique_FIT_donor_rows", "reference_design_different",
                    )
                    for field in fields:
                        self.control(panel, "panel_composition.json", ("endpoint", endpoint, field),
                                     "Methods / Table 1 / Tables S1 and S10", endpoint)
            for op in OPERATIONS:
                self.value(panel, op, 1 / 4, EQUAL, ("Skill",), "Results 3.4 / S10.3 (SMILES)", arm="S")
            self.value(panel, "D", 1 / 2, EQUAL, ("Skill",), "Results 3.4 (SMILES)", arm="S")
        for span in (1 / 4, 1):
            for field in ("E", "B"):
                self.value("Original", "D", span, EQUAL, (field,), "S10.3 (original error examples)")
        for op in ("P", "DP"):
            self.value("Original", op, 1, EQUAL, ("Skill",), "Results 3.4 (original full displacement)", function="h1")
        for filename, fields, use in (
            ("panel_composition.json", (("old_pair_group_count",), ("old_source_pair_overlap",),
                                        ("selected_sources",), ("selected_links",)), "Abstract / Methods / Table 1 / S10.1"),
            ("pilot_receipt.json", (("completed_calls", "chain_groups"),), "Methods 2.6 / S10.2"),
            ("dedup_and_carry_receipt.json", (("carry_scalars",), ("full_span_max_abs", "A"),
                                             ("full_span_max_abs", "r")), "S10.2"),
            ("closing_receipt.json", (("completed_ledger_totals", "summary_rows"),
                                      ("completed_ledger_totals", "paired_rows")), "Data S7"),
        ):
            for field in fields:
                self.control("Follow-up", filename, field, use)
        return curves, full_drug, half_drug

    def write_ledger(self):
        with (HERE / "path_numeric_sources_v0_13.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=tuple(self.ledger[0]))
            writer.writeheader()
            writer.writerows(self.ledger)


def draw_spans(axis, label, title, curves):
    axis.set_title(label + "   " + title, loc="left", fontweight="bold", pad=10)
    axis.grid(axis="y", color=GRID, linewidth=0.55)
    axis.axhline(0, color=ZERO, linewidth=0.95)
    for op, (_, color, marker) in OPERATIONS.items():
        axis.plot(SPANS, curves[op], color=color, marker=marker, markersize=4.7, linewidth=1.45)
    axis.set_xscale("log", base=2)
    axis.set_xticks(SPANS, SPAN_LABELS)
    axis.set_xlim(0.027, 1.16)
    axis.set_ylim(-0.65, 0.105)
    axis.yaxis.set_major_locator(FixedLocator((-0.6, -0.4, -0.2, 0)))
    axis.set_xlabel(r"Fraction of full displacement, $\lambda$ (log$_2$)")


def draw_paired(axis, title, labels, markers, panel_values, limits, ticks, xlabel):
    axis.set_title(title, loc="left", fontweight="bold", pad=10)
    positions = list(reversed(range(len(labels))))
    axis.set_yticks(positions, labels)
    axis.set_ylim(-0.5, len(labels) - 0.48)
    axis.set_xlim(*limits)
    axis.tick_params(axis="y", length=0, pad=6)
    axis.spines["left"].set_visible(False)
    axis.grid(axis="x", color=GRID, linewidth=0.55)
    axis.axvline(0, color=ZERO, linewidth=0.95)
    axis.xaxis.set_major_locator(FixedLocator(ticks))
    axis.xaxis.set_major_formatter(StrMethodFormatter("{x:.2g}"))
    axis.set_xlabel(xlabel)
    for panel, offset in (("Original", +0.12), ("Follow-up", -0.12)):
        for y, value, marker in zip(positions, panel_values[panel], markers, strict=True):
            color = "#0F4D92" if marker == "o" else "#8D475A"
            axis.plot(value, y + offset, linestyle="none", marker=marker, markersize=5.8,
                      markeredgewidth=1.2, markeredgecolor=color,
                      markerfacecolor=color if panel == "Original" else "white")


def render(curves, full_drug, half_drug):
    settings = {
        "font.family": ["DejaVu Sans", "sans-serif"],
        "font.size": 8.3,
        "text.color": INK,
        "axes.labelcolor": INK,
        "axes.edgecolor": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.spines.right": False,
        "axes.spines.top": False,
        "axes.linewidth": 0.7,
        "axes.axisbelow": True,
        "legend.frameon": False,
        "pdf.fonttype": 42,
        "savefig.facecolor": "white",
    }
    with plt.rc_context(settings):
        fig = plt.figure(figsize=(7.25, 5.95), facecolor="white")
        grid = fig.add_gridspec(2, 2, height_ratios=(1.05, 0.9), left=0.165, right=0.97,
                               top=0.87, bottom=0.165, wspace=0.40, hspace=0.59)
        original = fig.add_subplot(grid[0, 0])
        followup = fig.add_subplot(grid[0, 1], sharey=original)
        draw_spans(original, "A", "Original path panel", curves["Original"])
        draw_spans(followup, "B", "Follow-up path panel", curves["Follow-up"])
        original.set_ylabel(r"$\mathrm{Skill}(q)$ (pAffinity)")
        followup.tick_params(axis="y", labelleft=False)
        handles = [Line2D([0], [0], label=label, color=color, marker=marker, markersize=4.5, linewidth=1.45)
                   for label, color, marker in OPERATIONS.values()]
        fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.56, 0.975), ncol=3)

        full = fig.add_subplot(grid[1, 0])
        half = fig.add_subplot(grid[1, 1])
        draw_paired(
            full, "C   Full drug displacement",
            (r"$\mathrm{Skill}(q)$", r"$\mathrm{Skill}(h_1)$",
             r"$\Delta\mathrm{Skill}_{1,0}$", r"$\Delta\mathrm{Skill}_{1,\mathrm{aff}}$"),
            ("o", "o", "D", "D"), full_drug, (-0.27, 0.34), (-0.2, -0.1, 0, 0.1, 0.2, 0.3),
            r"Skill or paired $\Delta$Skill (pAffinity)",
        )
        full.axhline(1.5, color=GRID, linewidth=0.75)
        draw_paired(
            half, "D   Half drug displacement",
            ("Kd", "Ki", "Kd/Ki equal"), ("o", "o", "o"), half_drug,
            (-0.045, 0.045), (-0.04, -0.02, 0, 0.02, 0.04), r"$\mathrm{Skill}(q)$ (pAffinity)",
        )
        handles = [
            Line2D([0], [0], label=f"{panel} ({counts[0]} sources, {counts[1]} links)",
                   color=INK, marker="o", markersize=5.8, markeredgewidth=1.2, linestyle="none",
                   markerfacecolor=INK if panel == "Original" else "white")
            for panel, counts in CAPTION_COUNTS.items()
        ]
        fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.55, 0.015), ncol=2)
        fig.savefig(HERE / "Fig4_v0_13.pdf")
        fig.savefig(HERE / "Fig4_v0_13.png", dpi=300)
        plt.close(fig)


if __name__ == "__main__":
    sources = Sources()
    curves, full_drug, half_drug = sources.gather()
    render(curves, full_drug, half_drug)
    sources.write_ledger()
