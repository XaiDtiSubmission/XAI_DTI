"""Approved optimizer/loss/order primitives; no run loop or training launch."""
import math
import torch
from torch.nn import functional as F
from .spec import LRS, EPOCHS, BATCH_SIZE, PILOT_SEED, FORMAL_SEEDS, derive_seed


def make_optimizer(model, lr):
    if lr not in LRS:
        raise ValueError("LR_OUTSIDE_APPROVED_GRID")
    model.permit.consume("optimizer_construction",model.arm)
    return torch.optim.AdamW(model.parameters(),lr=lr,betas=(0.9,0.999),
                            eps=1e-8,weight_decay=3e-5,foreach=False,
                            amsgrad=False,maximize=False,capturable=False,
                            differentiable=False,fused=False)


def weighted_huber(prediction, truth, weights):
    if prediction.shape != truth.shape or truth.shape != weights.shape or prediction.ndim != 1:
        raise ValueError("LOSS_VECTOR_SHAPES")
    if (not torch.isfinite(prediction).all() or not torch.isfinite(truth).all()
            or not torch.isfinite(weights).all() or bool((weights < 0).any())):
        raise ValueError("LOSS_NONFINITE_OR_INVALID_WEIGHT")
    denominator = weights.sum().clamp_min(torch.finfo(torch.float32).eps)
    return (weights*F.huber_loss(prediction,truth,reduction="none",delta=1.0)).sum()/denominator


def qualify_optimizer_step(model, optimizer, batch, truth, weights):
    # Synthetic qualification only; this is not a formal fit/epoch.
    model.permit.require_synthetic("synthetic")
    optimizer.zero_grad(set_to_none=True)
    prediction = model(batch,data_kind="synthetic")
    loss = weighted_huber(prediction,truth,weights)
    model.permit.consume("backward",model.arm)
    loss.backward()
    gradients = {name:p.grad.detach().cpu().clone() if p.grad is not None else None
                 for name,p in model.named_parameters()}
    if any(g is None or not bool(torch.isfinite(g).all()) for g in gradients.values()):
        raise ValueError("NONFINITE_OR_MISSING_PARAMETER_GRADIENT")
    norm = torch.nn.utils.clip_grad_norm_(model.parameters(),5.0,error_if_nonfinite=True,
                                         foreach=False)
    model.permit.consume("optimizer_step",model.arm)
    optimizer.step()
    if any(not bool(torch.isfinite(p).all()) for p in model.parameters()):
        raise ValueError("NONFINITE_UPDATED_PARAMETER")
    return {"prediction":prediction.detach().cpu(),"loss":float(loss.detach().cpu()),
            "unclipped_gradients":gradients,"gradient_norm":float(norm.detach().cpu())}


def epoch_batches(row_count, generator):
    if row_count != 46140:
        raise ValueError("UNEXPECTED_FIT_SIZE")
    indices = torch.randperm(row_count,generator=generator,device="cpu").tolist()
    return [indices[i:i+BATCH_SIZE] for i in range(0,row_count,BATCH_SIZE)]


def loader_generator(seed):
    return torch.Generator(device="cpu").manual_seed(derive_seed(seed,"loader"))


def chosen_checkpoint(epoch_scores):
    if len(epoch_scores) != EPOCHS or any(not math.isfinite(x) for x in epoch_scores):
        raise ValueError("INCOMPLETE_FIXED_EPOCH_SELECTION")
    return min(range(EPOCHS),key=lambda i:(epoch_scores[i],i))+1


def chosen_lr(candidate_scores):
    if set(candidate_scores) != set(LRS) or any(not math.isfinite(v) for v in candidate_scores.values()):
        raise ValueError("INCOMPLETE_LR_SELECTION")
    return min(LRS,key=lambda lr:(candidate_scores[lr],lr))


def fixed_case_plan(selected_lrs=None):
    cases = []
    for i,lr in enumerate(LRS):
        for arm in (("S","G") if i%2 == 0 else ("G","S")):
            cases.append({"phase":"pilot","arm":arm,"seed":PILOT_SEED,"lr":lr,"epochs":EPOCHS})
    if selected_lrs is None:
        return cases
    if set(selected_lrs) != {"S","G"} or any(v not in LRS for v in selected_lrs.values()):
        raise ValueError("FORMAL_LR_NOT_FROZEN")
    for i,seed in enumerate(FORMAL_SEEDS):
        for arm in (("S","G") if i%2 == 0 else ("G","S")):
            cases.append({"phase":"formal","arm":arm,"seed":seed,
                          "lr":selected_lrs[arm],"epochs":EPOCHS})
    return cases
