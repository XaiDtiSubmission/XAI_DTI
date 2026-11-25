# Saved aggregate data dictionary

Data S1–S6 contain the 21 original saved aggregate files. Data S7 contains the follow-up path table. The ZIP files preserve complete-precision original JSON/JSONL bytes; gzip files restore the exact original bytes. No scientific quantity, row, null or saved status has been recomputed.

## Common axes and units

S and G identify the SMILES and GINE pipelines. Computational fits use seeds 17, 29, 43, 71 and 101; MEAN5 is the original five-fit summary, not a prediction ensemble or a sixth independent fit. Kd and Ki remain separate; KD_KI_EQUAL is the saved endpoint-balanced layer. D, P and DP denote drug, protein and joint displacement. ALL3_EQUAL is a saved summary across the three operations, not an additional operation. Full observed-source references and restricted path references have different donor supports and denominators.

Risk and response errors have pAffinity/function-output units. E is absolute approximation error, B is the error of zero response and Skill=B-E is signed. Each function has its own B. A difference between two functions' Skill is not Skill of their difference. R and R_perm are optional dimensionless normalized errors with their original undefined states. Amplitudes M_I/U_I are not predictive-risk gains.

## Confirmed array positions

In C05_DIAGNOSTIC, harm_proportions positions 0,1,2 are C3 direction-error harm, C7 harmful same-direction overshoot and C1 false-response harm. These three class-H contributions are divided by H_total of the selected summary; they are not averages of previously calculated ratios. H_total<=0 yields null with NO_OBSERVED_HARM. They are observed-harm shares, not causal shares.

In C05_GAIN_GROUP, class_MPH has shape [8,3]: rows C0–C7 and columns M,P,H. M is class mass, P helpful contribution and H harmful magnitude. Entries retain the original full-domain denominator restricted to the gain-sign group; they are not divided by group source_mass. Conditional group means are separate.

In C02_REFERENCE, transition_fractions and transition_counts flatten a 3-by-3 matrix in row-major order. Main FIT_ROW_UNIFORM signs are the outer axis and sensitivity FIT_PROTEIN_INPUT_UNIFORM signs the inner axis, each ordered -1,0,+1. Positions are (-,-),(-,0),(-,+),(0,-),(0,0),(0,+),(+,-),(+,0),(+,+). Counts exist only at native single-seed/single-endpoint layers; aggregate nulls retain NOT_APPLICABLE_TO_EQUAL_WEIGHT_AGGREGATE. Exact sign zero is not missing.

## Cancellation definitions

| Field | Definition | Units |
|---|---|---|
| C_increment | V_increment - abs(r_f(1)) | Function output |
| C_projected32 | Qplus32 + Qminus32 - abs(Q32) | Function output |
| C_readout_r | abs(r_h1(lambda)) + abs(r_aff(lambda)) - abs(r_q(lambda)) | Function output |
| C_readout_A | abs(A_h1(lambda)) + abs(A_aff(lambda)) - abs(A_q(lambda)) | Function output |
| C_readout_m | abs(m_h1(lambda)) + abs(m_aff(lambda)) - abs(m_q(lambda)) | Function output per displacement-fraction parameter |

These are unnormalized differences, not percentages or dimensionless ratios. They retain original finite-precision residuals without clipping. They use the original 32-source/64-link panel and its restricted references. A zero source projection does not itself make these quantities undefined.

Only C_increment and C_projected32 enter C07_SAME_PATH_JOINT. Full-denominator contributions aggregate a cell indicator times the named quantity; conditional means divide that contribution by cell mass. Empty cells retain zero unconditional mass/contribution and null/EMPTY_JOINT_CELL conditional means. Conditioning preserves the quantity's units. The three C_readout fields belong to the six measured spans in C07_READOUT and have no same-path cell axis.

## State and qualification handling

Explicit saved null, absent field, exact zero, empty conditional population and unresolved numerical qualification remain distinct. Integer counts need not have an integer interpretation at an endpoint/fit-balanced layer; original aggregate nulls stay null. Static producer-bound definitions do not constitute revalidation of original arrays or new scientific qualification. Refer to Supplementary Sections S7–S8 and the table-specific JSON dictionaries for their exact population and field scopes.
