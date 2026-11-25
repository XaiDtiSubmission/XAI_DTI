"""Data-free constants implementing the approved v0.2 definition."""
import hashlib

CONTRACT_ID = "SGC-v0.2-BUDGET-MATCHED-DRUG-PIPELINES-STATS"
SEED_NAMESPACE = "SGC-v0.1"
FORMAL_SEEDS = (17, 29, 43, 71, 101)
PILOT_SEED = 20261022
LRS = (0.00006, 0.0002, 0.0006)
KERNELS = (4, 6, 8)
SMILES_CAPS = (32, 64, 128, 256, 512, 768)
PROTEIN_CAPS = (256, 512, 1024, 2048, 4096, 8192)
NODE_CAPS = (32, 64, 128, 256, 384)
EDGE_CAPS = (64, 128, 256, 512, 768)
ENDPOINT = {"Kd": 0, "Ki": 1}
EXPECTED_COUNTS = {"S_drug": 185795, "G_drug": 185861,
                   "protein": 149376, "fusion": 295554,
                   "S": 630725, "G": 630791}
EPOCHS = 150
BATCH_SIZE = 32


def derive_seed(seed, name):
    payload = f"{SEED_NAMESPACE}|{seed}|{name}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % (2**63 - 1)


def capacity(required, choices):
    if not isinstance(required, int) or isinstance(required, bool) or required < 0:
        raise ValueError("INVALID_CAPACITY_REQUEST")
    for value in choices:
        if required <= value:
            return value
    raise ValueError("CAPACITY_OVERFLOW_NO_TRUNCATION")


def analytic_counts():
    s = 84*128 + 128*65*sum(KERNELS) + 3*65 + 195*128 + 128
    g = 7*128+128 + 5*(2*(128*128+128)+(4*128+128)+1) + 128*128+128
    p = 24*64 + 64*128*sum(KERNELS)+3*128
    h = 1024*256+256 + 256*128+128 + 2*(128+1)
    return {"S_drug": s, "G_drug": g, "protein": p, "fusion": h,
            "S": s+p+h, "G": g+p+h}
