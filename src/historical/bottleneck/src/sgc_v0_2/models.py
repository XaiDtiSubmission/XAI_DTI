"""Approved S/G mathematics. No models constructed at module import."""
import math
import os
import torch
from torch import nn
from torch.nn import functional as F
from .spec import KERNELS, EXPECTED_COUNTS, derive_seed


def mean_rms(h, mask):
    m = mask.to(dtype=h.dtype).unsqueeze(-1)
    count = m.sum(dim=1)
    if bool((count <= 0).any()):
        raise ValueError("EMPTY_READOUT")
    h = h * m
    mean = h.sum(dim=1) / count
    rms = torch.sqrt((h*h).sum(dim=1) / count + 1e-12)
    return torch.cat((mean, rms), dim=-1)


class ZeroPadLookup(nn.Module):
    def __init__(self, vocab, width):
        super().__init__()
        self.nonpad = nn.Parameter(torch.empty(vocab-1, width, dtype=torch.float32))
        self.register_buffer("pad", torch.zeros(1,width, dtype=torch.float32))
        self.vocab = vocab

    def forward(self, ids):
        table = torch.cat((self.pad, self.nonpad), dim=0)
        return F.one_hot(ids, num_classes=self.vocab).to(table.dtype) @ table


class CPUPrivateDropout(nn.Module):
    def __init__(self, seed):
        super().__init__()
        self.p = 0.1
        self.generator = torch.Generator(device="cpu")
        self.generator.manual_seed(seed)

    def forward(self, value):
        if not self.training:
            return value
        mask = (torch.rand(value.shape, generator=self.generator, device="cpu",
                           dtype=torch.float32) >= self.p).to(value.device, value.dtype)
        return value * mask / (1-self.p)


class ProteinCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.lookup = ZeroPadLookup(25,64)
        self.convs = nn.ModuleList([nn.Conv1d(64,128,k,padding="same",bias=True)
                                   for k in KERNELS])

    def forward(self, ids, mask):
        z = self.lookup(ids).transpose(1,2)
        return torch.cat([mean_rms(F.relu(conv(z)).transpose(1,2),mask)
                          for conv in self.convs], dim=-1)


class SmilesCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.lookup = ZeroPadLookup(85,128)
        self.convs = nn.ModuleList([nn.Conv1d(128,65,k,padding="same",bias=True)
                                   for k in KERNELS])
        self.projection = nn.Linear(195,128,bias=True)

    def forward(self, ids, mask):
        z = self.lookup(ids).transpose(1,2)
        positions = torch.cat([F.relu(conv(z)).transpose(1,2)
                               for conv in self.convs], dim=-1)
        return mean_rms(F.relu(self.projection(positions)),mask)


class GINEBlock(nn.Module):
    def __init__(self):
        super().__init__()
        self.edge = nn.Linear(4,128,bias=True)
        self.first = nn.Linear(128,128,bias=True)
        self.second = nn.Linear(128,128,bias=True)
        self.eps = nn.Parameter(torch.zeros((), dtype=torch.float32))

    def update(self, h, aggregate, node_mask):
        u = self.second(F.relu(self.first((1+self.eps)*h + aggregate)))
        return (h + F.relu(u)) * node_mask.unsqueeze(-1).to(h.dtype)

    def forward(self, h, graph):
        source = torch.bmm(graph.source_selector, h)
        message = F.relu(source + self.edge(graph.edge_attr))
        message = message * graph.edge_mask.unsqueeze(-1).to(message.dtype)
        aggregate = torch.bmm(graph.target_selector, message)
        return self.update(h, aggregate, graph.node_mask)

    def reference(self, h, graph):
        if h.device.type != "cpu":
            raise ValueError("EDGE_LOOP_REFERENCE_CPU_ONLY")
        per_graph = []
        for b,edges in enumerate(graph.edge_index):
            messages = [F.relu(h[b,s] + self.edge(graph.edge_attr[b,j]))
                        for j,(s,t) in enumerate(edges)]
            nodes = []
            for n in range(h.shape[1]):
                incoming = [message for message,(s,t) in zip(messages,edges) if t == n]
                aggregate = torch.stack(incoming).sum(dim=0) if incoming else h[b,n]*0
                nodes.append(aggregate)
            per_graph.append(torch.stack(nodes))
        return self.update(h, torch.stack(per_graph), graph.node_mask)


class GraphGINE(nn.Module):
    def __init__(self):
        super().__init__()
        self.node_projection = nn.Linear(7,128,bias=True)
        self.blocks = nn.ModuleList([GINEBlock() for _ in range(5)])
        self.projection = nn.Linear(128,128,bias=True)

    def forward(self, graph, reference=False):
        h = F.relu(self.node_projection(graph.x))
        h = h * graph.node_mask.unsqueeze(-1).to(h.dtype)
        for block in self.blocks:
            h = block.reference(h,graph) if reference else block(h,graph)
        return mean_rms(F.relu(self.projection(h)),graph.node_mask)


class Fusion(nn.Module):
    def __init__(self, seed):
        super().__init__()
        self.first = nn.Linear(1024,256,bias=True)
        self.second = nn.Linear(256,128,bias=True)
        self.Kd = nn.Linear(128,1,bias=True)
        self.Ki = nn.Linear(128,1,bias=True)
        self.drop0 = CPUPrivateDropout(derive_seed(seed,"head_dropout_0"))
        self.drop1 = CPUPrivateDropout(derive_seed(seed,"head_dropout_1"))

    def forward(self, drug, protein, endpoint):
        z = self.drop0(F.relu(self.first(torch.cat((drug,protein),dim=-1))))
        z = self.drop1(F.relu(self.second(z)))
        both = torch.cat((self.Kd(z),self.Ki(z)),dim=-1)
        # Algebraically route Kd/Ki, avoiding indexed-gradient accumulation.
        route = F.one_hot(endpoint,num_classes=2).to(both.dtype)
        return (both * route).sum(dim=-1)


def initialize(module, seed, name):
    generator = torch.Generator(device="cpu")
    generator.manual_seed(derive_seed(seed,name))
    with torch.no_grad():
        for item in module.modules():
            if isinstance(item, ZeroPadLookup):
                nn.init.normal_(item.nonpad,mean=0,std=1,generator=generator)
                item.pad.zero_()
            elif isinstance(item, (nn.Linear,nn.Conv1d)):
                nn.init.kaiming_uniform_(item.weight,a=math.sqrt(5),generator=generator)
                fan_in = item.weight.shape[1] * (item.weight.shape[2] if item.weight.ndim == 3 else 1)
                nn.init.uniform_(item.bias,-1/math.sqrt(fan_in),1/math.sqrt(fan_in),
                                 generator=generator)
            elif isinstance(item, GINEBlock):
                item.eps.zero_()


class ControlledDTI(nn.Module):
    def __init__(self, arm, seed, permit):
        permit.consume("model_construction",f"{arm}:{seed}")
        if arm not in ("S","G"):
            raise ValueError("ARM")
        super().__init__()
        self.arm, self.permit = arm, permit
        self.protein = ProteinCNN()
        self.drug = SmilesCNN() if arm == "S" else GraphGINE()
        self.fusion = Fusion(seed)
        initialize(self.protein,seed,"protein_init")
        initialize(self.fusion,seed,"fusion_init")
        initialize(self.drug,seed,f"drug_{arm}_init")
        counts = {"protein":sum(p.numel() for p in self.protein.parameters()),
                  "fusion":sum(p.numel() for p in self.fusion.parameters()),
                  arm+"_drug":sum(p.numel() for p in self.drug.parameters()),
                  arm:sum(p.numel() for p in self.parameters())}
        if any(value != EXPECTED_COUNTS[key] for key,value in counts.items()):
            raise ValueError("INSTANTIATED_PARAMETER_COUNT_MISMATCH")
        self.parameter_counts = counts

    def forward(self, batch, *, data_kind, graph_reference=False):
        self.permit.require_synthetic(data_kind)
        self.permit.consume("forward",self.arm + (":reference" if graph_reference else ""))
        protein = self.protein(batch.protein_ids,batch.protein_mask)
        if self.arm == "S":
            if graph_reference:
                raise ValueError("REFERENCE_ONLY_G")
            drug = self.drug(batch.smiles_ids,batch.smiles_mask)
        else:
            drug = self.drug(batch,reference=graph_reference)
        return self.fusion(drug,protein,batch.endpoint_index)

    def dropout_state(self):
        return {name:getattr(self.fusion,name).generator.get_state().clone()
                for name in ("drop0","drop1")}

    def restore_dropout_state(self, states):
        for name in ("drop0","drop1"):
            getattr(self.fusion,name).generator.set_state(states[name].cpu())


def build_pair(seed, permit, device="cpu"):
    if device not in ("cpu","mps"):
        raise ValueError("UNAPPROVED_DEVICE")
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise RuntimeError("MPS_FALLBACK_MUST_BE_DISABLED")
    torch.use_deterministic_algorithms(True,warn_only=False)
    left = ControlledDTI("S",seed,permit)
    right = ControlledDTI("G",seed,permit)
    # Common initial values cloned, never tied; assertion precedes device transfer.
    for module_name in ("protein","fusion"):
        target = getattr(right,module_name)
        target.load_state_dict(getattr(left,module_name).state_dict(),strict=True)
        lp = dict(getattr(left,module_name).named_parameters())
        rp = dict(target.named_parameters())
        if lp.keys() != rp.keys():
            raise ValueError("COMMON_PARAMETER_KEYS")
        for name in lp:
            if not torch.equal(lp[name],rp[name]) or lp[name].data_ptr() == rp[name].data_ptr():
                raise ValueError("COMMON_INITIAL_VALUE_OR_STORAGE")
    return left.to(device=device,dtype=torch.float32), right.to(device=device,dtype=torch.float32)
