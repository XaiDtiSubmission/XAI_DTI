"""Frozen text mapping and CPU collation; no chemistry, source files or data discovery."""
from dataclasses import dataclass
import re
import numpy as np
import torch
from .spec import SMILES_CAPS, PROTEIN_CAPS, NODE_CAPS, EDGE_CAPS, ENDPOINT, capacity

# Exact syntax inherited from the SHA-bound features.py, not a new tokenizer fit.
SMILES_TOKEN_PATTERN = re.compile(
    r"(\[[^\]]+\]|Br?|Cl?|N|O|S|P|F|I|b|c|n|o|s|p|"
    r"\(|\)|\.|=|#|-|\+|\\|/|:|~|@|\?|>>?|\*|\$|%\d{2}|[0-9])"
)
PROTEIN_ALPHABET = tuple("ABCDEFGHIKLMNPQRSTUVWXY")
PROTEIN_TO_INDEX = {"PAD": 0, "UNK": 1}
PROTEIN_TO_INDEX.update({v:i for i,v in enumerate(PROTEIN_ALPHABET, start=2)})


def smiles_ids(text, vocabulary):
    if (len(vocabulary) != 85 or vocabulary.get("PAD") != 0
            or vocabulary.get("UNK") != 1 or set(vocabulary.values()) != set(range(85))):
        raise ValueError("FROZEN_VOCABULARY_SCHEMA")
    tokens = SMILES_TOKEN_PATTERN.findall(text)
    if not tokens or "".join(tokens) != text or len(tokens) > 768:
        raise ValueError("SMILES_NOT_LOSSLESS_NONEMPTY_OR_WITHIN_CAP")
    return [vocabulary.get(token, vocabulary["UNK"]) for token in tokens]


def protein_ids(sequence):
    if not isinstance(sequence, str) or not 0 < len(sequence) <= 8192:
        raise ValueError("PROTEIN_EMPTY_OR_OVER_CAP")
    return [PROTEIN_TO_INDEX.get(residue, 1) for residue in sequence]


@dataclass(frozen=True)
class GraphArrays:
    x: object
    edge_index: object
    edge_attr: object


@dataclass(frozen=True)
class ModelBatch:
    # Identity, role, labels, weights and cluster keys deliberately excluded.
    protein_ids: torch.Tensor
    protein_mask: torch.Tensor
    endpoint_index: torch.Tensor
    smiles_ids: object = None
    smiles_mask: object = None
    x: object = None
    node_mask: object = None
    edge_attr: object = None
    edge_mask: object = None
    source_selector: object = None
    target_selector: object = None
    edge_index: object = None

    def to(self, device):
        return type(self)(**{k:(v.to(device) if torch.is_tensor(v) else v)
                             for k,v in vars(self).items()})


def pad_ids(values, capacities, vocab_size, override=None):
    if not values or any(not v for v in values):
        raise ValueError("EMPTY_TOKEN_INPUT")
    needed = max(map(len, values))
    cap = capacity(needed, capacities) if override is None else override
    if cap not in capacities or cap < needed:
        raise ValueError("UNREGISTERED_OR_SMALL_PADDING")
    ids = torch.zeros((len(values), cap), dtype=torch.int64)
    mask = torch.zeros((len(values), cap), dtype=torch.bool)
    for i, row in enumerate(values):
        if any(not isinstance(v, int) or not 1 <= v < vocab_size for v in row):
            raise ValueError("TOKEN_INDEX_OUT_OF_RANGE")
        ids[i,:len(row)] = torch.tensor(row, dtype=torch.int64)
        mask[i,:len(row)] = True
    return ids, mask


def validate_graph(graph):
    x, edges, attr = graph.x, graph.edge_index, graph.edge_attr
    if (x.ndim != 2 or x.shape[1] != 7 or len(x) == 0 or x.dtype != np.float32
            or edges.ndim != 2 or edges.shape[0] != 2 or edges.dtype != np.int64
            or attr.shape != (edges.shape[1], 4) or attr.dtype != np.float32):
        raise ValueError("FROZEN_GRAPH_SCHEMA")
    if not np.isfinite(x).all() or not np.isfinite(attr).all():
        raise ValueError("NONFINITE_GRAPH")
    if edges.size and (edges.min() < 0 or edges.max() >= len(x)):
        raise ValueError("EDGE_OUT_OF_RANGE")
    if (edges.shape[1] % 2 or not np.array_equal(edges[:,::2], edges[::-1,1::2])
            or not np.array_equal(attr[::2], attr[1::2])):
        raise ValueError("REVERSE_EDGE_MISMATCH")
    capacity(len(x), NODE_CAPS)
    capacity(edges.shape[1], EDGE_CAPS)


def collate(arm, proteins, endpoints, drugs, padding=None):
    if arm not in ("S", "G") or not 0 < len(proteins) == len(endpoints) == len(drugs):
        raise ValueError("BATCH_LENGTH_OR_ARM")
    if any(e not in ENDPOINT for e in endpoints):
        raise ValueError("ENDPOINT_NOT_KD_KI")
    padding = {} if padding is None else padding
    pids, pmask = pad_ids(proteins, PROTEIN_CAPS, 25, padding.get("protein"))
    common = dict(protein_ids=pids, protein_mask=pmask,
                  endpoint_index=torch.tensor([ENDPOINT[e] for e in endpoints], dtype=torch.int64))
    if arm == "S":
        ids, mask = pad_ids(drugs, SMILES_CAPS, 85, padding.get("smiles"))
        return ModelBatch(**common, smiles_ids=ids, smiles_mask=mask)
    for g in drugs:
        validate_graph(g)
    n = capacity(max(len(g.x) for g in drugs), NODE_CAPS)
    e = capacity(max(g.edge_index.shape[1] for g in drugs), EDGE_CAPS)
    nc, ec = padding.get("nodes", n), padding.get("edges", e)
    if nc not in NODE_CAPS or ec not in EDGE_CAPS or nc < n or ec < e:
        raise ValueError("GRAPH_PADDING_CAP")
    size = len(drugs)
    x = torch.zeros((size,nc,7), dtype=torch.float32)
    a = torch.zeros((size,ec,4), dtype=torch.float32)
    nm = torch.zeros((size,nc), dtype=torch.bool)
    em = torch.zeros((size,ec), dtype=torch.bool)
    src = torch.zeros((size,ec,nc), dtype=torch.float32)
    dst = torch.zeros((size,nc,ec), dtype=torch.float32)
    # Host indices retained for the CPU reference only; selectors are model input.
    host_edges = []
    for i,g in enumerate(drugs):
        ni, ei = len(g.x), g.edge_index.shape[1]
        x[i,:ni] = torch.from_numpy(g.x.copy())
        a[i,:ei] = torch.from_numpy(g.edge_attr.copy())
        nm[i,:ni] = True
        em[i,:ei] = True
        edges = tuple((int(s),int(t)) for s,t in g.edge_index.T)
        host_edges.append(edges)
        for j,(s,t) in enumerate(edges):
            src[i,j,s] = 1
            dst[i,t,j] = 1
    return ModelBatch(**common, x=x, node_mask=nm, edge_attr=a, edge_mask=em,
                      source_selector=src, target_selector=dst,
                      edge_index=tuple(host_edges))
