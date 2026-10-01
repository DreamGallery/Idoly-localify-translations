#!/usr/bin/env python3
"""Synchronize trusted story sources, then reconcile GitHub task Issues.

Data and a durable pending-task ledger are pushed before any Issue API writes.
Retries reconcile existing Issues by script ID; concurrent branch edits fail closed.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import unicodedata

import story_migration as migration
import story_tasks as tasks

STATE = 'automation/story-sync-state.json'
SOURCE_REPOSITORY = 'DreamGallery/Hoshimi-Adv'
TARGET_REPOSITORY = 'DreamGallery/Idoly-localify-translations'


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), '-c', 'core.quotepath=false', '-c', 'credential.helper=',
                             '-c', 'credential.helper=!gh auth git-credential', *args],
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Git operation failed: ' + args[0])
    return result.stdout.strip()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def source_inventory(receipt):
    return {item['file_id']: {'source_sha256': item['source_sha256'], 'csv_path': item['csv_path']}
            for item in receipt['scripts'] if item['status'] != 'retired'}


def normalize_filename(value):
    # Browser-copied filenames may carry invisible direction/format markers.
    # Only trim boundaries; never silently change an identifier or path inside.
    def boundary(char):
        return char.isspace() or unicodedata.category(char) == 'Cf'
    start, end = 0, len(value)
    while start < end and boundary(value[start]):
        start += 1
    while end > start and boundary(value[end - 1]):
        end -= 1
    result = value[start:end]
    if not result:
        raise ValueError('Story filename is empty; enter an adv_...txt or adv_...csv filename')
    return result


def next_state(previous, receipt, source_commit, revision, filename=None, story_id=None):
    if filename is not None and story_id is not None:
        raise ValueError('Choose either filename or story_id, not both')
    if previous and previous.get('schema_version') != 1:
        raise ValueError('Unsupported synchronization ledger')
    if revision < previous.get('resource_revision', 0):
        raise ValueError('Source revision moved backwards; refusing downgrade')
    inventory = source_inventory(receipt)
    pending = copy.deepcopy(previous.get('pending_tasks', {}))
    candidates = {item['file_id'] for item in receipt['task_candidates']}
    changed = set(receipt['source_changed'])
    old_inventory = previous.get('scripts', {})
    # A release may have imported machine data before the NAS pushed its snapshot.
    # Compare the source ledger too, so such changes still produce collaboration tasks.
    if previous:
        for stem, entry in inventory.items():
            old = old_inventory.get(stem)
            if old != entry:
                candidates.add(stem)
                if old and old['source_sha256'] != entry['source_sha256']:
                    changed.add(stem)
    candidates.update(item['file_id'] for item in receipt['scripts'] if item['status'] == 'path_changed')
    if filename is not None:
        filename = normalize_filename(filename)
        matches = [stem for stem, item in inventory.items() if filename in
                   (stem, stem + '.txt', stem + '.csv', 'story/ai/' + item['csv_path'])]
        if len(matches) != 1:
            raise ValueError(f'Story filename {ascii(filename)} matched {len(matches)} current stories; '
                             'enter the exact adv_...txt or adv_...csv filename from Hoshimi-Adv')
        candidates.add(matches[0])
    if story_id is not None:
        story_id = normalize_filename(story_id)
        if not re.fullmatch(r'adv_[a-z]+_(?:[A-Za-z0-9]+_)*[0-9]+', story_id):
            raise ValueError('Use a story ID ending in a number, e.g. adv_card_ktn_15 or adv_event_2107')
        matches = {stem for stem in inventory
                   if stem.startswith(story_id + '_') and not stem.endswith('_short')}
        if not matches:
            raise ValueError(f'Story ID {ascii(story_id)} has no current non-short chapters')
        candidates.update(matches)
    for stem in sorted(candidates):
        item = inventory[stem]
        prior = pending.get(stem, {})
        pending[stem] = {**item, 'source_changed': stem in changed or prior.get('source_changed', False)}
    for stem in pending:
        if not tasks.STEM.fullmatch(stem):
            raise ValueError('Invalid pending task ID')
        if stem in inventory:
            if pending[stem]['source_sha256'] != inventory[stem]['source_sha256']:
                pending[stem]['source_changed'] = True
            pending[stem].update(inventory[stem])
    return {'schema_version': 1, 'source_repository': SOURCE_REPOSITORY,
            'source_commit': source_commit, 'resource_revision': revision,
            'scripts': inventory, 'pending_tasks': pending}


def publish_data(root, paths, expected_head, message):
    paths = sorted(set(paths))
    for path in paths:
        parts = PurePosixPath(path).parts
        if ('..' in parts or PurePosixPath(path).is_absolute() or
                not (path in (STATE, 'upstream.json') or
                     path.startswith(('story/', 'records/', 'archive/')))):
            raise ValueError('Unexpected synchronization output path')
    if git(root, 'rev-parse', 'HEAD') != expected_head:
        raise ValueError('Local branch changed during synchronization')
    git(root, 'fetch', 'origin', 'main')
    if git(root, 'rev-parse', 'FETCH_HEAD') != expected_head:
        raise ValueError('Translation branch advanced; retry without overwriting user edits')
    if not paths:
        return expected_head
    for offset in range(0, len(paths), 100):
        git(root, 'add', '--', *paths[offset:offset + 100])
    if set(git(root, 'diff', '--cached', '--no-renames', '--name-only').splitlines()) != set(paths):
        raise ValueError('Unexpected staged files')
    git(root, '-c', 'user.name=Idoly localization bot',
        '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
        'commit', '-m', message)
    git(root, 'push', 'origin', 'HEAD:refs/heads/main')
    return git(root, 'rev-parse', 'HEAD')


def run(source, root, repository=TARGET_REPOSITORY, filename=None, apply=False, story_id=None):
    source, root = Path(source).resolve(), Path(root).resolve()
    if repository != TARGET_REPOSITORY:
        raise ValueError('Unexpected task repository')
    if apply and git(root, 'status', '--porcelain'):
        raise ValueError('Translation checkout must be clean before synchronization')
    expected = git(root, 'rev-parse', 'HEAD')
    source_commit = git(source, 'rev-parse', 'HEAD')
    revision_text = (source / 'revision').read_text().strip()
    if not re.fullmatch(r'[0-9]+', revision_text):
        raise ValueError('Invalid source revision')
    revision = int(revision_text)
    upstream = json.loads((root / 'upstream.json').read_text())
    if revision < upstream.get('story', {}).get('resource_revision', 0):
        raise ValueError('Source repository is older than released data; wait for the source updater')
    receipt, writes, deletes = migration.plan(source / 'Resource', source / 'CSV', root)
    resources = sorted((source / 'Resource').rglob('adv_*.txt'), key=lambda p: p.name)
    snapshot = hashlib.sha256(''.join(path.name + '\0' + migration.digest(path.read_bytes()) + '\n'
                                      for path in resources).encode()).hexdigest()
    published = {p.relative_to(root).as_posix() for p in (root / 'story/ai').rglob('*.csv')}
    published.update(p for p in writes if p.startswith('story/ai/') and p.endswith('.csv'))
    published.difference_update(deletes - set(writes))
    fields = sum(bool(row['text']) for path in (source / 'CSV').rglob('*.csv')
                 for row in migration.read_csv(path)[:-2])
    upstream.setdefault('story', {}).update(resource_revision=revision, resource_files=len(resources),
                                            translated_csv_files=len(published), text_fields=fields,
                                            resource_snapshot_sha256=snapshot)
    upstream_data = (json.dumps(upstream, ensure_ascii=False, indent=2) + '\n').encode()
    if upstream_data != (root / 'upstream.json').read_bytes():
        writes['upstream.json'] = upstream_data
    ledger = migration.safe(root, root / STATE)
    previous = json.loads(ledger.read_text()) if ledger.exists() else {}
    state = next_state(previous, receipt, source_commit, revision, filename, story_id)
    ledger_data = encoded(state)
    if not ledger.exists() or ledger.read_bytes() != ledger_data:
        writes[STATE] = ledger_data
    selected = {stem: {'id': stem, 'path': 'story/ai/' + item['csv_path'],
                        'source_sha256': item['source_sha256']}
                for stem, item in state['pending_tasks'].items() if stem in state['scripts']}
    changed = {stem for stem, item in state['pending_tasks'].items() if item['source_changed']}
    paths = sorted(set(writes) | deletes)
    report = {'source_commit': source_commit, 'resource_revision': revision,
              'changed_files': len(paths), 'pending_tasks': len(selected),
              'retired_scripts': [s['file_id'] for s in receipt['scripts'] if s['status'] == 'retired'],
              'applied': apply}
    if not apply:
        report['task_files'] = sorted(selected)
        return report
    migration.apply(root, writes, deletes)
    expected = publish_data(root, paths, expected, 'Synchronize original stories and migrate translation drafts')
    # A failure here deliberately leaves the already-pushed ledger intact.
    results = tasks.synchronize(tasks.GitHub(repository), selected, True, changed) if selected else []
    if selected:
        for stem in selected:
            del state['pending_tasks'][stem]
        migration.atomic_write(ledger, encoded(state))
        expected = publish_data(root, [STATE], expected, 'Record synchronized story tasks')
    report.update(commit=expected, tasks=results)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-repo', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--filename')
    selection.add_argument('--story-id', help='Create tasks for all chapters under this ID, excluding _short')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.source_repo, args.data_dir, filename=args.filename, apply=args.apply,
                         story_id=args.story_id),
                     ensure_ascii=False, indent=2))
