#!/usr/bin/env python3
"""Offline byte and saved-arithmetic checks. No model or array decoding."""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def need(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(relative):
    return json.loads((ROOT / relative).read_text(encoding='utf-8'))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 2**20), b''):
            h.update(block)
    return h.hexdigest()


def close(a, b):
    return math.isfinite(a) and math.isfinite(b) and abs(a-b) <= 1e-12 + 1e-12*(abs(a)+abs(b))


def check_manifest(relative):
    data = read_json(relative)
    count = 0
    for item in data['files']:
        path = ROOT / item['path']
        need(not Path(item['path']).is_absolute() and '..' not in Path(item['path']).parts, 'UNSAFE_MANIFEST_PATH')
        need(path.is_file() and not path.is_symlink(), 'MISSING_OR_SYMLINK:' + item['path'])
        need(path.stat().st_size == item['bytes'] and digest(path) == item['sha256'], 'BYTE_IDENTITY:' + item['path'])
        count += 1
    return count


def saved_mathematics():
    link = read_json('provenance/r01_original32/original_32_link_comparison.json')['comparisons']
    slots = read_json('provenance/r01_original32/original_32_slot_comparison.json')['slots']
    need(len(link) == 640 and len(slots) == 20, 'CORE_RECORD_COUNTS')
    for row in link:
        v = {k: x['actual'] for k, x in row['comparisons'].items()}
        need(close(v['epsilon'], v['r']-v['A']), 'LINK_RESIDUAL_IDENTITY')
        need(close(v['Skill'], abs(v['r'])-abs(v['epsilon'])), 'LINK_SKILL_IDENTITY')
        for field in row['comparisons'].values():
            need(close(field['actual'], field['saved']) and field['pass'], 'RECORDED_LINK_COMPARISON')
    for slot in slots:
        rows = [r for r in link if all(r[k] == slot[k] for k in ('fit', 'endpoint', 'function'))]
        need(len(rows) == 32 and len({r['selected_source_index0'] for r in rows}) == 16, 'ENDPOINT_SUPPORT')
        E = math.fsum(r['restricted_main_weight']/16*abs(r['comparisons']['epsilon']['actual']) for r in rows)
        B = math.fsum(r['restricted_main_weight']/16*abs(r['comparisons']['r']['actual']) for r in rows)
        for name, value in [('E', E), ('B', B), ('Skill', B-E)]:
            need(close(value, slot['comparisons'][name]['actual']), 'RECORDED_REDUCTION:' + name)
            need(close(value, slot['saved'][name]), 'SAVED_TARGET:' + name)
    # Check the saved target rows of both panels against their own published tables.
    historical = read_json('provenance/r01_original32/historical_128_coverage.json')['slots']
    for relative, targets in [('aggregates/AF06/B1_SPAN.jsonl', slots), ('aggregates/AF08/span_summary.jsonl', historical)]:
        lines = (ROOT / relative).read_bytes().splitlines(keepends=True)
        for target in targets:
            raw = lines[target['source_line_one_based']-1]
            need(hashlib.sha256(raw).hexdigest() == target['original_line_sha256'], 'SAVED_SUMMARY_LINE_HASH')
            row = json.loads(raw)
            for field in ('E', 'B', 'Skill'):
                need(row[field] == target['saved'][field], 'SAVED_TABLE_TARGET_VALUE')
    return {'new_model_evaluations': 0, 'saved_link_function_rows_checked': len(link),
            'saved_scalar_comparisons': 2560, 'original_summary_slots_checked': len(slots),
            'historical_followup_summary_slots_checked': len(historical),
            'meaning': 'Arithmetic closure and saved-table identities, not a new frozen-function execution.'}


def attachment_members():
    items = read_json('provenance/attachment_member_scan.json')['members']
    by_number = {}
    for item in items:
        by_number.setdefault(item['additional_file'], []).append(item)
    for number, members in by_number.items():
        candidates = list((ROOT / 'attachments').glob('Additional_file_%02d_*.zip' % number))
        need(len(candidates) == 1, 'ATTACHMENT_ARCHIVE')
        with zipfile.ZipFile(candidates[0]) as archive:
            need(set(archive.namelist()) == {x['member'] for x in members}, 'ATTACHMENT_MEMBER_SET')
            for item in members:
                raw = archive.read(item['member'])
                need(raw == (ROOT / ('aggregates/AF%02d/' % number) / item['member']).read_bytes(), 'ARCHIVE_BROWSEABLE_COPY')
                if item['member'].endswith('.gz'):
                    gzip.decompress(raw)
    return len(items)


def claim_navigation():
    data = read_json('claims/claim_asset_map.json')
    checked = 0
    for item in data['claims'] + data['revision_items']:
        for relative in item.get('public_artifacts', []):
            need(not Path(relative).is_absolute() and '..' not in Path(relative).parts, 'UNSAFE_CLAIM_PATH')
            need((ROOT / relative).exists(), 'MISSING_PUBLIC_CLAIM_ARTIFACT:' + relative)
            checked += 1
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-payload', action='store_true', help='Also hash extracted GitHub Release payload files; no decoding.')
    args = parser.parse_args()
    result = {'status': 'PASS', 'release': 'v0.23-r01.1',
              'scientific_material_files_checked': check_manifest('provenance/scientific_material_manifest.json'),
              'attachment_members_checked': attachment_members(), 'saved_mathematics': saved_mathematics(),
              'public_claim_artifact_paths_checked': claim_navigation(),
              'models_decoded': 0, 'research_scripts_executed': 0}
    if (ROOT / 'saved_rows/manifest.json').exists():
        result['saved_row_files_checked'] = check_manifest('saved_rows/manifest.json')
    if (ROOT / 'saved_rows/role_order_manifest.json').exists():
        result['role_order_files_checked'] = check_manifest('saved_rows/role_order_manifest.json')
    if (ROOT / 'saved_rows/retained_support/manifest.json').exists():
        result['retained_support_files_checked'] = check_manifest('saved_rows/retained_support/manifest.json')
    if args.with_payload:
        result['release_payload_files_checked'] = check_manifest('payload/manifest.json')
    else:
        result['release_payload_check'] = 'NOT_REQUESTED; use --with-payload after extracting release archives'
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'FAIL', 'reason': str(exc)}), file=sys.stderr)
        raise SystemExit(1)
