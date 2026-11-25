#!/usr/bin/env python3
"""Portable, bounded original32 frozen-head replay. Default: preflight only.

Uses project-owned historical loader/head definitions through a four-name AST
projection. It never dispatches a historical worker or builds an encoder.
"""
import argparse
import ast
import collections
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EPS = ('Kd', 'Ki')


class Stop(Exception):
    pass


def need(value, reason):
    if not value:
        raise Stop(reason)


def finite(np, values, reason):
    need(bool(np.isfinite(values).all()), reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(relative):
    return json.loads((ROOT / relative).read_text())


def relative_file(relative):
    need(not Path(relative).is_absolute() and '..' not in Path(relative).parts, 'PUBLIC_RELATIVE_PATH_REQUIRED')
    p = ROOT / relative
    need(p.is_file() and not p.is_symlink(), 'MISSING_RELEASE_ASSET:' + relative)
    return p


def close(actual, saved):
    return math.isfinite(actual) and abs(actual-saved) <= 1e-12 + 1e-12*(abs(actual)+abs(saved))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Explicitly decode five GINE model/cache bindings and evaluate the bounded target.')
    parser.add_argument('--output', type=Path, help='New JSON receipt path; never overwrites an existing path.')
    args = parser.parse_args()
    binding = load('payload/selectors/model_case_bindings.json')
    contract = load('methods/core_contract.json')
    cases = contract['cases']
    public = {c['case_id']: c for c in binding['cases'] if c['arm'] == 'G'}
    need(set(public) == {c['case_id'] for c in cases}, 'FIVE_GINE_CASES')
    manifest = {f['path']: f for f in load('payload/manifest.json')['files']}
    paths = [binding['calibration']['path'], 'payload/selectors/original32_link_panel.jsonl',
             'payload/references/original32_span_values.npy']
    sources = {}
    for case in cases:
        item = public[case['case_id']]
        for field, public_field in [('h0_checkpoint', 'h0_checkpoint'), ('h1_head', 'h1_head'), ('cache', 'own_original_cache_reference')]:
            rec = item[public_field]
            need(rec['path'] in manifest and rec['sha256'] == manifest[rec['path']]['sha256'], 'PUBLIC_BINDING_MANIFEST_JOIN')
            paths.append(rec['path'])
            sources[case[field]] = rec
    for path in paths:
        need(path in manifest, 'RELEASE_FILE_NOT_MANIFESTED:' + path)
        relative_file(path)
    result = {'status': 'PREFLIGHT_READY', 'target': {'arm': 'G', 'operation': 'D', 'lambda': 1,
              'reference': 'FIT_ROW_UNIFORM', 'functions': ['h1', 'q'], 'fits': [17,29,43,71,101], 'endpoints': list(EPS)},
              'required_release_files': paths, 'historical_versions': contract['historical_versions'],
              'current_execution': 'NOT_REQUESTED', 'model_evaluations': 0}
    if not args.execute:
        print(json.dumps(result, indent=2))
        return
    need(args.output is not None and not args.output.exists(), 'EXECUTION_REQUIRES_NEW_OUTPUT_PATH')
    # Imports and binary decoding occur only after the explicit --execute flag.
    import numpy as np
    import torch
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    counters = collections.Counter()

    def verified(relative):
        raw = relative_file(relative).read_bytes()
        rec = manifest[relative]
        need(len(raw) == rec['bytes'] and sha(raw) == rec['sha256'], 'RELEASE_BYTE_IDENTITY:' + relative)
        return raw

    class Context:
        m = contract

        def __init__(self):
            self.sources = sources

        def read(self, rid):
            return verified(sources[rid]['path'])

        def reserve(self, name, counts, **metadata):
            counters.update(counts)

        def done(self):
            pass

    context = Context()
    code = (ROOT / 'src/historical/bottleneck/scripts/sgc_fr40_path_span_v0_2.py').read_text()
    names = {'Lex', 'checkpoint', 'make_head', 'cache'}
    definitions = [n for n in ast.parse(code).body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    need({n.name for n in definitions} == names, 'FOUR_HELPER_DEFINITIONS')
    ns = {'json': json, 're': re, 'math': math, 'io': io, 'zipfile': zipfile, 'need': need, 'Stop': Stop, 'finite': finite}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), '<historical-four-helper-projection>', 'exec'), ns)
    lex = ns['Lex'](verified(binding['calibration']['path']))
    affine = {}
    for item in lex.items(lex.fields()['slots'][0]):
        fields = lex.fields(item[0])
        if lex.val(fields['family']) != 'affine':
            continue
        cid, ep = lex.val(fields['case_id']), lex.val(fields['endpoint'])
        if cid in public and ep in EPS:
            a, b = map(float, lex.val(fields['coefficients']))
            need(a > 0 and math.isfinite(a) and math.isfinite(b), 'AFFINE_COEFFICIENT')
            need((cid, ep) not in affine, 'DUPLICATE_AFFINE_SLOT')
            affine[cid, ep] = (a, b)
    need(len(affine) == 10, 'TEN_GINE_AFFINE_SLOTS')
    links = [json.loads(line) for line in verified('payload/selectors/original32_link_panel.jsonl').splitlines()]
    need(len(links) == 64, 'ORIGINAL_64_LINKS')
    for i, link in enumerate(links):
        need(link['link_index0'] == i and link['selected_source_index0'] == i//2 and
             link['endpoint'] == EPS[int(i >= 32)] and link['restricted_main_weight'] == .5, 'FROZEN_LINK_ORDER_WEIGHT')
    old = np.load(io.BytesIO(verified('payload/references/original32_span_values.npy')), allow_pickle=False)
    need(old.shape == (10,64,3,6,4,4) and old.dtype == np.dtype('<f8'), 'ORIGINAL_SAVED_ARRAY_SCHEMA')
    expected = load('provenance/r01_original32/original_32_slot_comparison.json')['slots']
    output, link_checks = [], 0
    for case in cases:
        cid = case['case_id']
        V = ns['cache'](context, np, case['cache'], links)
        models = [ns['make_head'](torch, ns['checkpoint'](context, torch, np, case[field], case, higher))
                  for field, higher in [('h0_checkpoint', False), ('h1_head', True)]]
        values, gradients = [{}, {}], [{}, {}]
        results = {}
        for li, link in enumerate(links):
            ep = link['endpoint']
            index = link['operation_index_columns']
            z0, z1 = V[index[7]], V[index[9]]
            delta, native = z1-z0, []
            for hid, model in enumerate(models):
                vals = []
                for vector, gradient_needed in [(z0, True), (z1, False)]:
                    canonical = vector.copy(); canonical[canonical == 0] = 0
                    key = (ep, canonical.tobytes())
                    if key not in values[hid] or (gradient_needed and key not in gradients[hid]):
                        x = torch.tensor(vector.reshape(1,1024), dtype=torch.float64, requires_grad=gradient_needed)
                        if gradient_needed:
                            y, _, _ = model(x, EPS.index(ep))
                            g = torch.autograd.grad(y, x)[0].detach().numpy().ravel()
                            finite(np, g, 'NONFINITE_SOURCE_AD'); gradients[hid][key] = g
                            counters['source_input_AD'] += 1
                        else:
                            with torch.no_grad():
                                y, _, _ = model(x, EPS.index(ep))
                        values[hid][key] = float(y.detach().item()); counters['native_head_forward'] += 1
                    vals.append(values[hid][key])
                canonical = z0.copy(); canonical[canonical == 0] = 0
                projection = float(np.dot(gradients[hid][ep, canonical.tobytes()], delta))
                native.append((vals[0], vals[1], projection)); counters['direction_dot'] += 1
            h0, h1 = native; a, b = affine[cid, ep]
            response = {'h1': (h1[1]-h1[0], h1[2]),
                        'q': ((h1[1]-(a*h0[1]+b))-(h1[0]-(a*h0[0]+b)), h1[2]-a*h0[2])}
            for fun, (r, A) in response.items():
                e = r-A; vals = [A,r,e,abs(r)-abs(e)]
                saved = old[case['case_code'],li,0,5,{'q':0,'h1':3}[fun],:]
                need(all(close(x,float(y)) for x,y in zip(vals,saved)), 'ORIGINAL_LINK_TARGET_MISMATCH')
                results[li,fun] = vals; link_checks += 1
        for ep in EPS:
            for fun in ['h1','q']:
                selected = [i for i,l in enumerate(links) if l['endpoint'] == ep]
                weights = np.array([links[i]['restricted_main_weight']/16 for i in selected], dtype=np.float64)
                values_for_slot = np.array([results[i,fun] for i in selected], dtype=np.float64)
                E = float(np.tensordot(weights,np.abs(values_for_slot[:,2]),axes=(0,0)))
                B = float(np.tensordot(weights,np.abs(values_for_slot[:,1]),axes=(0,0)))
                target = next(x for x in expected if (x['fit'],x['endpoint'],x['function']) == (case['seed'],ep,fun))
                need(all(close(x,target['saved'][k]) for k,x in [('E',E),('B',B),('Skill',B-E)]), 'SUMMARY_TARGET_MISMATCH')
                output.append({'fit':case['seed'],'endpoint':ep,'function':fun,'E':E,'B':B,'Skill':B-E})
        del V, models
    need(len(output) == 20 and link_checks == 640 and counters['checkpoint_decode'] == 10 and
         counters['source_input_AD'] <= 320 and counters['native_head_forward'] <= 960 and counters['direction_dot'] == 640, 'BOUNDED_COVERAGE')
    result.update({'status':'PASS_EXPLICIT_ORIGINAL32_FROZEN_CORE','current_execution':'EXPLICIT_EXECUTE',
        'versions':{'python':sys.version.split()[0],'numpy':np.__version__,'torch':torch.__version__},
        'runtime_is_original_binary':'NOT_ATTESTED; compare recorded historical fingerprints separately',
        'counter_totals':dict(counters),'slots':output,'link_function_rows_checked':link_checks,
        'model_evaluations':counters['native_head_forward'],
        'limitations':['This wrapper does not repeat historical FD/chain qualifications.',
        'No followup128 function calls, encoder forward, label scoring, training, calibration refit or new inferential statistics.',
        'Frozen original support; retained-support analyses are separate AF18 objects.',
        'This public adapter was not model-executed during release preparation.']})
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False); stream.write('\n')
    print(json.dumps({'status':result['status'],'slots':20,'link_function_rows':link_checks}))


if __name__ == '__main__':
    main()
