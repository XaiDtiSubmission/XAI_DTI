# Source adjudication and retained support

This companion gives the record decisions, denominators and frozen model comparisons used for the source-adjudication analysis. The audit covered 728 records from two source queues. The retained-support analysis excludes 726 records with conflicting target annotations. The two records whose annotations agree with the human FAAH measurements retain their existing eligibility. Unresolved measurement fields remain identified in the record ledger.

| File | Contents |
| --- | --- |
| record_disposition.csv | Source record, label and pair identifiers, model role, endpoint, decision, evidence and unresolved fields |
| support_counts.csv | Original and retained evaluation support by endpoint; source, link, candidate, and label-group counts keep their respective units |
| gate_by_fit_endpoint.csv and gate_summary.csv | Original and retained complete GATE comparisons, using the same stored scores, labels and FIT-only calibration maps |
| path_by_fit_endpoint.csv and path_mean5_by_endpoint.csv | GINE full drug displacement for h1 and q. Original full support, original reference on common nonempty sources, and retained links with within-source normalization are separate rows |
| complete_operation_GINE_q.csv | GINE q on D, P and DP, by endpoint, fit and complete reference. The protein-input-uniform reference assigns equal mass to retained donor protein inputs and then equal mass to rows within each input |
| GINE_equal_endpoint_display_means.csv | Five-fit means with equal Kd/Ki weights for the retained path and complete-operation comparisons |
| original_ranking_retained_sources.csv | Original saved actions and losses for four or eight queries, restricted to sources whose 32 candidates all remain eligible |
| anchored_summary.csv | For both model arms and complete references: component magnitude, source-reference variation, retained-support risk and deletion contrasts by fit and endpoint |
| diagnostic_by_fit_endpoint.csv | Helpful and harmful approximation contributions for GINE q across D, P and DP, by fit, endpoint and reference |
| retained_gain_skill_joint.csv | Source masses and unconditional contributions for each combination of anchored-deletion gain sign and GINE q Skill sign, on their paired retained support |
| frozen_lineage_scope.json | Verified scope of the deterministic reconstruction of frozen labels and inputs in the registered provenance audit |
| provenance.json | Relative source locators and SHA-256 fingerprints |

The three model roles in the ledger are historical. The eligibility decision applies to this manuscript's evaluation and four-corner support. The q01 exclusions have no membership effect on the original or follow-up path or ranking panels. The saved q06 path and ranking exclusions therefore yield the same support as the combined adjudication for those panels. These comparisons retain the original trained heads, encoder coordinates, model selection and calibration. The original full-support data remain available as the reported historical comparison.
