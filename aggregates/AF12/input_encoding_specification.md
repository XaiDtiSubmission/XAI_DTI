# Frozen model input specification

The rules below transcribe the frozen input producers used in this study. They specify encoding, padding and capacity handling; they do not revalidate the original input arrays or measure unknown-token, clipping or collision rates.

## SMILES tokens

The original regular expression was:

```python
SMILES_TOKEN_PATTERN = re.compile(
    r"(\[[^\]]+\]|Br?|Cl?|N|O|S|P|F|I|b|c|n|o|s|p|"
    r"\(|\)|\.|=|#|-|\+|\\|/|:|~|@|\?|>>?|\*|\$|%\d{2}|[0-9])"
)
```

The tokenizer uses findall and requires exact concatenation to the original input. The frozen model wrapper additionally rejects empty token sequences, checks the 85-entry continuous-ID schema and rejects more than 768 tokens. The original vocabulary has PAD=0, UNK=1 and IDs 0–84. Recognized tokens absent from that vocabulary map to UNK. No truncation is performed. The original JSON mapping is included without renumbering.

## Protein symbols

The literal alphabet is `ABCDEFGHIKLMNPQRSTUVWXY`. PAD=0, UNK=1 and the listed symbols receive IDs starting at 2 in that exact order. Characters outside the mapping use UNK=1. The wrapper requires a nonempty string with at most 8192 residues. Encoding does not itself clean or uppercase input; cleaned uppercase sequences belong to upstream data construction in Supplementary Section S1.1.

## Capacities and masks

| Input | Ordered capacity buckets |
|---|---|
| SMILES tokens | 32, 64, 128, 256, 512, 768 |
| Protein residues | 256, 512, 1024, 2048, 4096, 8192 |
| Graph nodes | 32, 64, 128, 256, 384 |
| Directed graph edges | 64, 128, 256, 512, 768 |

The capacity selector accepts a non-Boolean nonnegative integer and chooses the first sufficient registered bucket. Overrides must be registered and sufficient. Overflow stops processing without clipping. Token IDs are int64, initialized to PAD=0, with Boolean masks initially false; actual prefix positions are marked true. Actual IDs satisfy 1<=ID<vocab_size. Vocabulary sizes are 85 for SMILES and 25 for protein.

## Graph encoding and batching

Supplementary Table S2a gives all seven node and four edge features. For one graph, x is N by 7, edge_index is 2 by E_dir and edge_attr is E_dir by 4. Every RDKit bond appends begin-to-end and end-to-begin edges with identical attributes. x and edge_attr are float32, and graph indices are int64. Empty bond sets retain edge_index shape [2,0] and edge_attr shape [0,4]; no-atom graphs are invalid.

The wrapper checks finite attributes, index ranges, paired reverse edges, feature schema and registered capacities. Batched x has shape [B,Ncap,7] and edge_attr [B,Ecap,4], both zero-padded with Boolean node/edge masks. source_selector[B,Ecap,Ncap] and target_selector[B,Ncap,Ecap] are float32 incidence selectors; host edge tuples are retained for the CPU reference. An edge-free graph may occupy the first edge bucket with all edge-mask positions false; padding does not create bonds.

Kd=0 and Ki=1. Identity/group keys, data roles, labels and sample weights are not model features. No pretrained protein language model or GNN weights were used. Encoding rules do not establish that vocabulary construction, weight construction or all historical preprocessing used only the later MODEL_FIT role.
