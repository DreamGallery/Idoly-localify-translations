#!/usr/bin/env python3
"""Validate a source snapshot and plan conservative story migrations (stdlib only)."""
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import os
import tempfile

COLUMNS = ['id', 'name', 'text', 'trans']
STEM = re.compile(r'adv_[A-Za-z0-9_-]+\Z')
FIELD = re.compile(r'[1-9][0-9]*:(text|title|choice|narration):[1-9][0-9]*\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')
LAYERS = ('ai', 'human', 'reviewed', 'drafts/translation', 'drafts/proofread')
ASSIGN = re.compile(r'(?:(?<=\[)|(?<= ))([A-Za-z_][A-Za-z_0-9]*)=')
TAG = re.compile(r'^\[([A-Za-z_][A-Za-z_0-9]*)\b')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe(root, path):
    path.relative_to(root)
    for p in (path, *path.parents):
        if p.is_symlink():
            raise ValueError('Symlinked input/output is not supported')
        if p == root:
            break
    return path


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f'Invalid CSV columns: {path.name}')
        rows = list(reader)
    if any(set(r) != set(COLUMNS) or any(not isinstance(v, str) for v in r.values()) for r in rows):
        raise ValueError(f'Malformed CSV: {path.name}')
    if len(rows) < 2 or rows[-2]['id'] != 'info' or rows[-1]['id'] != '译者':
        raise ValueError(f'Missing metadata: {path.name}')
    if len({r['id'] for r in rows}) != len(rows) or any(not FIELD.fullmatch(r['id']) for r in rows[:-2]):
        raise ValueError(f'Invalid/duplicate field IDs: {path.name}')
    if rows[-2]['name'] != path.stem + '.txt' or not STEM.fullmatch(path.stem) or not HASH.fullmatch(rows[-2]['text']):
        raise ValueError(f'Invalid source metadata: {path.name}')
    return rows


def encode_csv(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def source_fields(script):
    # Same command grammar as public HoshimiToolkit adv_csv; no project import.
    def end(line, start):
        depth, i = 0, start
        while i < len(line):
            char = line[i]
            if char == '\\' and i + 1 < len(line):
                i += 2
                continue
            if char == '[':
                depth += 1
            elif char == ']':
                if depth == 0:
                    return i
                depth -= 1
            elif char == ' ' and depth == 0 and ASSIGN.match(line, i + 1):
                return i
            i += 1
        raise ValueError('Unclosed source command')
    result = []
    for number, line in enumerate(script.splitlines(), 1):
        tag = TAG.match(line)
        if not tag or tag[1] not in ('message', 'narration', 'title', 'choice', 'choicegroup'):
            continue
        tag = tag[1]
        assignments = list(ASSIGN.finditer(line))
        name = next((line[a.end():end(line, a.end())] for a in assignments if a[1] == 'name'), '')
        key = 'title' if tag == 'title' else 'text'
        category = 'choice' if tag in ('choice', 'choicegroup') else 'narration' if tag == 'narration' else key
        ordinal = 0
        for a in assignments:
            if a[1] != key:
                continue
            value = line[a.end():end(line, a.end())]
            if value:
                ordinal += 1
                result.append({'id': f'{number}:{category}:{ordinal}', 'name': name, 'text': value, 'trans': ''})
    return result


def identity(row):
    return row['id'].split(':')[1], row['name'], row['text']


def rebase(old, current):
    result = [{**r, 'trans': ''} for r in current]
    same = old[-2]['text'] == current[-2]['text']
    old_counts = Counter(identity(r) for r in old[:-2])
    new_counts = Counter(identity(r) for r in current[:-2])
    by_key = {identity(r): r for r in old[:-2]}
    by_id = {r['id']: r for r in old[:-2]}
    retained = set()
    for row in result[:-2]:
        prior = None
        if same:
            # Safe legacy narration/choice alias only with verified same source.
            alias = re.sub(r':(?:choice|narration):', ':text:', row['id'])
            candidate = by_id.get(row['id'], by_id.get(alias))
            if candidate and (candidate['name'], candidate['text']) == (row['name'], row['text']):
                prior = candidate
        elif old_counts[identity(row)] == new_counts[identity(row)] == 1:
            prior = by_key[identity(row)]
        if prior:
            row['trans'] = prior['trans']
            retained.add(prior['id'])
    if same and len(retained) != len(old[:-2]):
        raise ValueError('CSV fields disagree with their declared source hash')
    if same and len(old[:-2]) != len(current[:-2]):
        raise ValueError('CSV field count disagrees with its declared source hash')
    result[-1] = dict(old[-1])
    lost = [r['id'] for r in old[:-2] if r['trans'] and r['id'] not in retained]
    return result, lost


def inventory(root, folder):
    result = {}
    for path in sorted((root / folder).rglob('*.csv')):
        safe(root, path)
        rows = read_csv(path)
        if path.stem in result:
            raise ValueError(f'Duplicate script in {folder}: {path.stem}')
        result[path.stem] = (path, rows)
    return result


def plan(source_dir, csv_dir, translations_dir):
    source_dir, csv_dir, root = map(lambda p: Path(p).absolute(), (source_dir, csv_dir, translations_dir))
    incoming = inventory(csv_dir, '')
    if not incoming:
        raise ValueError('Empty source CSV snapshot')
    originals = {}
    for path in sorted(source_dir.rglob('adv_*.txt')):
        safe(source_dir, path)
        if not STEM.fullmatch(path.stem) or path.stem in originals:
            raise ValueError('Invalid or duplicate source script identity')
        raw = path.read_bytes()
        body = source_fields(raw.decode('utf-8'))
        originals[path.stem] = (raw, body)
        if body and path.stem not in incoming:
            raise ValueError(f'Incomplete CSV snapshot: {path.stem}')
    layers = {layer: inventory(root, 'story/' + layer) for layer in LAYERS}
    manifest_path = safe(root, root / 'automation/story-sources.json')
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    writes, deletes, reports = {}, set(), []
    def write(path, data):
        safe(root, root / path)
        if not (root / path).exists() or (root / path).read_bytes() != data:
            writes[path] = data
    def archive(path, sha):
        raw = path.read_bytes()
        dest = 'archive/backups/' + sha + '/' + digest(raw) + '/' + path.relative_to(root).as_posix()
        write(dest, raw)
        return dest
    for stem, (source_csv, current) in incoming.items():
        rel = source_csv.relative_to(csv_dir).as_posix()
        if stem not in originals:
            raise ValueError(f'Missing source script: {stem}')
        raw, body = originals[stem]
        sha = digest(raw)
        if any(r['trans'] for r in current):
            raise ValueError(f'Source CSV must contain no translations: {stem}')
        if sha != current[-2]['text'] or body != [{**r, 'trans': ''} for r in current[:-2]]:
            raise ValueError(f'Source snapshot mismatch: {stem}')
        before = set(writes) | deletes
        existing = {k: v[stem] for k, v in layers.items() if stem in v}
        old_hashes = {rows[-2]['text'] for _, rows in existing.values()}
        changed = bool(old_hashes - {sha})
        previous = layers['ai'].get(stem)
        status = 'source_changed' if changed else 'new' if not existing else 'path_changed' if any(p.relative_to(root).as_posix() != 'story/' + k + '/' + rel for k, (p, _) in existing.items()) else 'unchanged'
        losses, rebased, archived, conflicts = {}, {}, {}, []
        for layer, (path, rows) in existing.items():
            rebased[layer], losses[layer] = rebase(rows, current)
            if changed or status == 'path_changed':
                archived[layer] = archive(path, rows[-2]['text'])
        if 'ai' not in rebased:
            rebased['ai'] = [{**r, 'trans': ''} for r in current]
        if changed:
            for formal, draft in [('human', 'drafts/translation'), ('reviewed', 'drafts/proofread')]:
                if formal in rebased:
                    # Existing working drafts include intentional blank edits.
                    if draft not in rebased:
                        rebased[draft] = rebased[formal]
                    else:
                        for a, b in zip(rebased[draft][:-2], rebased[formal][:-2]):
                            if b['trans'] and a['trans'] != b['trans']:
                                # Unpublished work is authoritative within its draft.
                                # Keep both alternatives in the confirmation record.
                                conflicts.append({'row_id': a['id'], 'draft_layer': draft,
                                                  'draft_translation': a['trans'],
                                                  'formal_layer': formal,
                                                  'formal_translation': b['trans']})
                    del rebased[formal]
        for layer, rows in rebased.items():
            write('story/' + layer + '/' + rel, encode_csv(rows))
        for layer, (path, _) in existing.items():
            relative = path.relative_to(root).as_posix()
            if layer not in rebased or relative != 'story/' + layer + '/' + rel:
                deletes.add(relative)
        record_path = safe(root, root / 'records' / (stem + '.json'))
        if changed or (record_path.exists() and status == 'path_changed'):
            record = (json.loads(record_path.read_text(encoding='utf-8')) if record_path.exists()
                      else {'schema_version': 1, 'file_id': stem})
            if record.get('file_id') != stem:
                raise ValueError(f'Record identity mismatch: {stem}')
            if record_path.exists():
                archive(record_path, previous[1][-2]['text'] if previous else sorted(old_hashes)[0])
            artifacts = record.setdefault('artifacts', {})
            for track, formal in [('translation', 'human'), ('proofread', 'reviewed')]:
                draft = 'drafts/' + track
                if changed:
                    record.setdefault(track, {})['state'] = '进行中'
                    record[track]['revision'] = record[track].get('revision', 0) + 1
                    record[track]['draft_revision'] = record[track].get('draft_revision', 0) + 1
                    record.setdefault('force_complete', {})[track] = False
                    prior = artifacts.pop(track + '_csv', {})
                    if draft in rebased:
                        artifact = artifacts.setdefault(track + '_draft', prior)
                        artifact['based_on_revision'] = record[track].get('revision', 0)
                for suffix, layer in [('_csv', formal), ('_draft', draft)]:
                    if track + suffix in artifacts:
                        artifacts[track + suffix]['path'] = 'story/' + layer + '/' + rel
            if changed:
                artifacts.pop('proofread_txt', None)
                record.pop('direct_machine_proofread', None)
                record.pop('source_confirmation', None)
                previous_sources = []
                old_source = manifest.get('scripts', {}).get(stem, {})
                if old_source.get('source_sha256') in old_hashes and manifest.get('source_commit'):
                    previous_sources.append({'source_repository': manifest['source_repository'],
                                             'source_commit': manifest['source_commit'],
                                             'raw_path': old_source.get('raw_path', 'Resource/' + stem + '.txt'),
                                             'source_sha256': old_source['source_sha256']})
                # Keep prior unresolved recovery information across repeated upstream
                # changes; each archived record also retains its complete history.
                prior_change = record.get('source_change')
                if prior_change:
                    record.setdefault('source_change_history', []).append(prior_change)
                record['source_change'] = {
                    'status': 'needs-confirmation', 'previous_source_sha256': sorted(old_hashes),
                    'source_sha256': sha, 'archived_artifacts': archived,
                    'conflicts': conflicts, 'previous_sources': previous_sources,
                    'lost_translations': {
                        layer: [dict(row) for row in existing[layer][1][:-2] if row['id'] in lost]
                        for layer, lost in losses.items() if lost}}
            write(record_path.relative_to(root).as_posix(), (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode())
        reports.append({'file_id': stem, 'csv_path': rel, 'status': status, 'source_sha256': sha,
                        'previous_source_sha256': previous[1][-2]['text'] if previous else None,
                        'needs_confirmation': changed, 'cleared_translations': losses,
                        'changed_paths': sorted((set(writes) | deletes) - before)})
    for stem in sorted(set().union(*(set(v) for v in layers.values())) - set(incoming)):
        reports.append({'file_id': stem, 'status': 'retired', 'needs_confirmation': False, 'changed_paths': []})
    receipt = {'schema_version': 1, 'scripts': reports, 'task_candidates': [r for r in reports if r['status'] in ('new', 'source_changed')],
               'source_changed': [r['file_id'] for r in reports if r['status'] == 'source_changed'], 'changed_paths': sorted(set(writes) | deletes)}
    return receipt, writes, deletes


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.migration-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def apply(root, writes, deletes):
    root = Path(root).absolute()
    originals = {p: (root / p).read_bytes() if (root / p).exists() else None for p in set(writes) | deletes}
    try:
        # Archives are durable before any formal artifact is removed.
        for path in sorted(writes, key=lambda p: (not p.startswith('archive/'), p)):
            atomic_write(safe(root, root / path), writes[path])
        for path in deletes - set(writes):
            safe(root, root / path).unlink()
    except Exception:
        for path, data in originals.items():
            if data is not None:
                atomic_write(root / path, data)
            elif (root / path).exists():
                (root / path).unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', required=True)
    parser.add_argument('--csv-dir', required=True)
    parser.add_argument('--translations-dir', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    receipt, writes, deletes = plan(args.source_dir, args.csv_dir, args.translations_dir)
    if args.apply:
        apply(args.translations_dir, writes, deletes)
    receipt['applied'] = args.apply
    atomic_write(Path(args.report), (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode())
    print(json.dumps({'applied': args.apply, 'changed_files': len(receipt['changed_paths']), 'task_candidates': len(receipt['task_candidates'])}))


if __name__ == '__main__':
    main()
