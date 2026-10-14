#!/usr/bin/env python3
"""Fill matching empty AI fields from published main without changing human work."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile

import story_migration as migration

SOURCE_BRANCH = 'main'
TARGET_BRANCH = 'collaboration'
BOT_EMAIL = '41898282+github-actions[bot]@users.noreply.github.com'


class GitFailure(RuntimeError):
    def __init__(self, operation, code):
        # Credential helpers and remote errors may include secrets.
        super().__init__(f'Git {operation} failed (exit {code})')
        self.code = code


def git(root, *args, data=None, env=None):
    result = subprocess.run(
        ['git', '-C', str(root), '-c', 'core.quotepath=false',
         '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential', *args],
        input=data, capture_output=True, env=env)
    if result.returncode:
        raise GitFailure(args[0], result.returncode)
    return result.stdout


def text_git(root, *args, **kwargs):
    return git(root, *args, **kwargs).decode('utf-8').strip()


def fetch(root, *branches):
    git(root, 'fetch', '--no-tags', 'origin', *[
        f'+refs/heads/{branch}:refs/remotes/origin/{branch}' for branch in branches])
    return [text_git(root, 'rev-parse', f'refs/remotes/origin/{branch}') for branch in branches]


def ancestor(root, older, newer):
    try:
        git(root, 'merge-base', '--is-ancestor', older, newer)
        return True
    except GitFailure as error:
        if error.code == 1:
            return False
        raise


@dataclass
class Story:
    path: str
    mode: str
    data: bytes
    rows: list[dict[str, str]]


def valid_path(path):
    parts = PurePosixPath(path).parts
    return (path.startswith('story/ai/') and not any(c in path for c in '\\\n\r\t\0')
            and all(part not in ('', '.', '..') for part in path.split('/'))
            and len(parts) >= 3 and path.endswith('.csv')
            and migration.STEM.fullmatch(PurePosixPath(path).stem))


def read_stories(root, commit):
    """Read Git blobs in bulk; never check out or execute collaboration files."""
    entries = []
    for record in git(root, 'ls-tree', '-rz', '--full-tree', commit, '--', 'story/ai').split(b'\0'):
        if not record:
            continue
        header, raw_path = record.split(b'\t', 1)
        mode, kind, oid = header.decode('ascii').split()
        path = raw_path.decode('utf-8')
        if mode not in ('100644', '100755') or kind != 'blob':
            raise ValueError('Non-regular AI data file: ' + path)
        if not path.endswith('.csv'):
            continue
        if not valid_path(path):
            raise ValueError('Invalid AI data path: ' + path)
        entries.append((path, mode, oid))
    if not entries:
        return {}
    output = io.BytesIO(git(root, 'cat-file', '--batch',
                           data=('\n'.join(oid for _, _, oid in entries) + '\n').encode('ascii')))
    result = {}
    for path, mode, oid in entries:
        actual_oid, kind, size = output.readline().decode('ascii').split()
        if actual_oid != oid or kind != 'blob':
            raise ValueError('Unexpected Git blob response')
        data = output.read(int(size))
        if len(data) != int(size) or output.read(1) != b'\n':
            raise ValueError('Truncated Git blob response')
        stem = PurePosixPath(path).stem
        if stem in result:
            raise ValueError('Duplicate AI story: ' + stem)
        result[stem] = Story(path, mode, data, migration.parse_csv(data, stem + '.csv'))
    if output.read():
        raise ValueError('Unexpected trailing Git blob data')
    return result


def plan(published, collaboration):
    changes, files = {}, []
    skipped = {key: [] for key in ('missing_in_collaboration', 'source_changed', 'fields_changed')}
    for stem, source in sorted(published.items()):
        target = collaboration.get(stem)
        if target is None:
            skipped['missing_in_collaboration'].append(stem)
            continue
        if source.rows[-2]['text'] != target.rows[-2]['text']:
            skipped['source_changed'].append(stem)
            continue
        identity = lambda rows: {r['id']: (r['name'], r['text']) for r in rows[:-2]}
        if identity(source.rows) != identity(target.rows):
            skipped['fields_changed'].append(stem)
            continue
        incoming = {r['id']: r['trans'] for r in source.rows[:-2]}
        rows = [dict(row) for row in target.rows]
        filled = 0
        for row in rows[:-2]:
            value = incoming[row['id']]
            if row['text'] and not row['trans'].strip() and value.strip():
                row['trans'] = value
                filled += 1
        if filled:
            changes[target.path] = migration.encode_csv(rows)
            files.append({'file_id': stem, 'path': target.path, 'filled_rows': filled})
    return changes, {'changed_files': len(files), 'filled_rows': sum(f['filled_rows'] for f in files),
                     'files': files, 'skipped': skipped}


def commit_changes(root, parent, changes, stories):
    """Use a separate index to preserve all other tree entries and local edits."""
    modes = {story.path: story.mode for story in stories.values()}
    if not changes or any(not valid_path(path) or path not in modes for path in changes):
        raise ValueError('Only existing AI CSV files may be updated')
    with tempfile.TemporaryDirectory(prefix='idoly-machine-sync-') as directory:
        env = {**os.environ, 'GIT_INDEX_FILE': str(Path(directory) / 'index')}
        git(root, 'read-tree', parent, env=env)
        entries = []
        for path, data in sorted(changes.items()):
            oid = text_git(root, 'hash-object', '-w', '--stdin', data=data)
            entries.append(f'{modes[path]} {oid}\t{path}\0')
        git(root, 'update-index', '-z', '--index-info', data=''.join(entries).encode('utf-8'), env=env)
        staged = set(git(root, 'diff', '--cached', '--no-renames', '--name-only', '-z', parent,
                         env=env).decode('utf-8').rstrip('\0').split('\0'))
        if staged != set(changes):
            raise ValueError('Unexpected staged machine translation files')
        tree = text_git(root, 'write-tree', env=env)
        return text_git(root, '-c', 'user.name=Idoly localization bot', '-c', 'user.email=' + BOT_EMAIL,
                        'commit-tree', tree, '-p', parent, '-m', 'Fill missing story machine translations')


def run(repository, *, apply=False, max_attempts=3):
    root = Path(repository).resolve()
    source_commit, target_commit = fetch(root, SOURCE_BRANCH, TARGET_BRANCH)
    published = read_stories(root, source_commit)
    for attempt in range(1, max_attempts + 1):
        current = read_stories(root, target_commit)
        changes, report = plan(published, current)
        report.update(schema_version=1, source_branch=SOURCE_BRANCH, target_branch=TARGET_BRANCH,
                      source_commit=source_commit, target_before=target_commit,
                      target_commit=target_commit, applied=apply, attempts=attempt)
        if not apply or not changes:
            return report
        commit = commit_changes(root, target_commit, changes, current)
        try:
            git(root, 'push', 'origin', commit + ':refs/heads/' + TARGET_BRANCH)
        except GitFailure:
            latest, = fetch(root, TARGET_BRANCH)
            if ancestor(root, commit, latest):
                report['target_commit'] = commit  # Push succeeded but its response was lost.
                return report
            if latest == target_commit:
                raise  # Authentication/network failures are not branch races.
            if not ancestor(root, target_commit, latest):
                raise ValueError('Collaboration history was rewritten; refusing synchronization') from None
            target_commit = latest  # Re-plan from the new tree, preserving concurrent work.
        else:
            report['target_commit'] = commit
            return report
    raise ValueError('Collaboration kept advancing; retry machine synchronization')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Push filled AI fields; otherwise report only')
    args = parser.parse_args()
    print(json.dumps(run(args.repository_dir, apply=args.apply), ensure_ascii=False, indent=2))
