#!/usr/bin/env python3
"""Plan Viewer tasks from trusted CSV data; --apply explicitly enables GitHub writes."""
import argparse
import csv
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

STEM = re.compile(r'adv_[A-Za-z0-9_-]+\Z')
REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')
COMMIT = re.compile(r'[a-f0-9]{40}\Z')
DATA_BRANCH = 'collaboration'
PATH = re.compile(r'[\w./-]+\Z')
FIELDS = ['id', 'name', 'text', 'trans']
STATES = ('待认领', '进行中', '完成')
PENDING_LABEL = '待翻译'


def is_short_story(stem):
    return stem.endswith('_short')


def read_story(path, root):
    if any(p.is_symlink() for p in (path, *path.parents) if p == root or root in p.parents):
        raise ValueError('Symlinked story data')
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Oversized story CSV')
    relative = path.relative_to(root).as_posix()
    if not PATH.fullmatch(relative) or any(p in ('', '.', '..') for p in relative.split('/')):
        raise ValueError('Unsafe story path')
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise ValueError(f'Invalid CSV columns: {relative}')
        rows = list(reader)
    if any(set(r) != set(FIELDS) or any(v is None for v in r.values()) for r in rows):
        raise ValueError(f'Malformed CSV: {relative}')
    info = [r for r in rows if r['id'] == 'info']
    if len(info) != 1 or not info[0]['name'].endswith('.txt'):
        raise ValueError(f'Missing source metadata: {relative}')
    stem = info[0]['name'][:-4]
    if not STEM.fullmatch(stem) or not HASH.fullmatch(info[0]['text']):
        raise ValueError(f'Invalid source metadata: {relative}')
    body = [r for r in rows if r['id'] not in ('info', '译者')]
    if not body:
        return None
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError(f'Duplicate CSV row: {relative}')
    return {'id': stem, 'path': relative, 'source_sha256': info[0]['text']}


def stories(root, filenames=None):
    root = Path(root)
    manifest_path = root / 'automation/story-sources.json'
    if manifest_path.is_symlink():
        raise ValueError('Symlinked source manifest')
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('schema_version') != 1 or
            manifest.get('source_repository') != 'DreamGallery/Hoshimi-Adv' or
            not COMMIT.fullmatch(manifest.get('source_commit', ''))):
        raise ValueError('Invalid pinned source manifest')
    inventory = {}
    for path in sorted((root / 'story/ai').rglob('*.csv')):
        story = read_story(path, root)
        if story is None or is_short_story(story['id']):
            continue
        if story['id'] in inventory:
            raise ValueError(f'Duplicate story ID: {story["id"]}')
        source = manifest.get('scripts', {}).get(story['id'])
        # Retired scripts remain archived in the data checkout, not in task selection.
        if source is None:
            continue
        if (source.get('source_sha256') != story['source_sha256'] or
                'story/ai/' + source.get('csv_path', '') != story['path']):
            raise ValueError('Pinned source manifest does not match story CSV')
        story.update(source_commit=manifest['source_commit'],
                     raw_path=source.get('raw_path', 'Resource/' + story['id'] + '.txt'),
                     data_branch=DATA_BRANCH)
        inventory[story['id']] = story
    if filenames is None:
        return inventory
    chosen = {}
    for filename in filenames:
        # Accept source script names, CSV filenames, or exact repository CSV paths.
        value = filename.strip()
        if is_short_story(Path(value).stem):
            raise ValueError('Short preview scripts do not need collaboration tasks: ' + value)
        matches = [s for s in inventory.values() if value in
                   (s['id'], s['id'] + '.txt', s['id'] + '.csv', s['path'])]
        if len(matches) != 1:
            raise ValueError(f'Unknown or ambiguous story filename: {value}')
        chosen[matches[0]['id']] = matches[0]
    return chosen


def marker(body, name):
    matches = re.findall(r'<!--\s*' + re.escape(name) + r':\s*(.*?)\s*-->', body)
    if len(matches) > 1:
        raise ValueError(f'Duplicate Issue marker: {name}')
    return matches[0] if matches else None


def set_marker(body, name, value):
    old = marker(body, name)
    line = f'<!-- {name}: {value} -->'
    if old is None:
        return body.rstrip() + ('\n' if body else '') + line
    return re.sub(r'<!--\s*' + re.escape(name) + r':\s*.*?\s*-->', lambda _: line, body)


def task_body(story, body=''):
    if is_short_story(story['id']):
        raise ValueError('Short preview scripts do not need collaboration tasks')
    path = story['path']
    source_commit = story.get('source_commit', '')
    raw_path = story.get('raw_path', 'Resource/' + story['id'] + '.txt')
    data_branch = story.get('data_branch', DATA_BRANCH)
    if (not STEM.fullmatch(story['id']) or not HASH.fullmatch(story['source_sha256']) or
            not path.startswith('story/ai/') or not PATH.fullmatch(path) or
            any(part in ('', '.', '..') for part in path.split('/'))):
        raise ValueError('Invalid task story metadata')
    if (not COMMIT.fullmatch(source_commit) or raw_path != 'Resource/' + story['id'] + '.txt'
            or data_branch != DATA_BRANCH):
        raise ValueError('Invalid pinned task source metadata')
    description = ('<!-- story-task-description:start -->\n'
                   '剧情翻译与校对任务。请在协作网站领取任务、保存草稿并提交完成结果。\n\n'
                   f'[查看原文](https://github.com/DreamGallery/Hoshimi-Adv/blob/{source_commit}/{raw_path})'
                   f' · [查看初译](https://github.com/DreamGallery/Idoly-localify-translations/blob/{data_branch}/{quote(path, safe="/")})\n'
                   '<!-- story-task-description:end -->')
    pattern = r'<!-- story-task-description:start -->.*?<!-- story-task-description:end -->'
    if '<!-- story-task-description:start -->' in body:
        body = re.sub(pattern, lambda _: description, body, flags=re.DOTALL)
    elif not re.sub(r'<!--.*?-->', '', body, flags=re.DOTALL).strip():
        body = description + ('\n\n' + body if body else '')
    values = {'raw_path': 'raw_txt/' + story['id'] + '.txt', 'ai_path': path,
              'translated_path': path.replace('story/ai/', 'story/human/', 1),
              'proofread_path': path.replace('story/ai/', 'story/reviewed/', 1),
              'source_sha256': story['source_sha256'], 'source_commit': source_commit,
              'source_raw_path': raw_path, 'data_branch': data_branch}
    for name, value in values.items():
        body = set_marker(body, name, value)
    for role in ('tr', 'pr'):
        current = marker(body, role)
        if current is None:
            body = body.rstrip() + f'\n<!-- {role}::待认领 -->'
        elif not re.fullmatch(r'[^\r\n:<>]*:(?:待认领|进行中|完成)', current):
            raise ValueError(f'Invalid task track: {role}')
    return body


def plan(story, issue=None, source_changed=False):
    if issue is None:
        return {'action': 'create', 'id': story['id'], 'payload':
                {'title': story['id'], 'body': task_body(story), 'labels': [PENDING_LABEL]}}
    old_body = issue.get('body') or ''
    previous = marker(old_body, 'source_sha256')
    changed = (previous is not None and previous != story['source_sha256']) or (source_changed and previous is None)
    body = task_body(story, old_body)
    payload = {}
    if changed:
        # Preserve ownership; migration has invalidated completion of the old source.
        history = []
        for role in ('tr', 'pr'):
            track = marker(body, role)
            user, state = track.rsplit(':', 1)
            history.append(f'{role}: {track}')
            if state == '完成':
                body = set_marker(body, role, user + (':进行中' if user else ':待认领'))
        body = set_marker(body, 'source_review_required', story['source_sha256'])
        notice = ('<!-- source-review-notice:start -->\n'
                  '原文已更新；请核对新原文及迁移后的草稿，重新完成翻译与校对。\n'
                  + '变更前任务状态：' + '；'.join(history) + '\n'
                  '<!-- source-review-notice:end -->')
        if '<!-- source-review-notice:start -->' in body:
            body = re.sub(r'<!-- source-review-notice:start -->.*?<!-- source-review-notice:end -->',
                          lambda _: notice, body, flags=re.DOTALL)
        else:
            body += '\n\n' + notice
        if issue.get('state') != 'open':
            payload['state'] = 'open'
    if body != old_body:
        payload['body'] = body
    return {'action': 'update' if payload else 'skip', 'id': story['id'],
            'number': issue['number'], 'payload': payload}


class GitHub:
    def __init__(self, repository):
        if not REPOSITORY.fullmatch(repository):
            raise ValueError('Invalid GitHub repository')
        self.base = 'repos/' + repository

    def request(self, method, endpoint, payload=None):
        command = ['gh', 'api', '--method', method, self.base + '/' + endpoint]
        if payload is not None:
            command += ['--input', '-']
        result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                                text=True, capture_output=True)
        if result.returncode:
            # Avoid echoing external response bodies or credential-bearing stderr.
            raise RuntimeError(f'GitHub {method} request failed (exit {result.returncode})')
        return json.loads(result.stdout)

    def issues(self):
        output = []
        for page in range(1, 1001):
            batch = self.request('GET', f'issues?state=all&per_page=100&page={page}')
            if not isinstance(batch, list):
                raise ValueError('Invalid GitHub Issue response')
            output.extend(i for i in batch if 'pull_request' not in i)
            if len(batch) < 100:
                return output
        raise ValueError('Too many Issue pages; refusing incomplete deduplication')

    def ensure_task_label(self):
        for page in range(1, 1001):
            labels = self.request('GET', f'labels?per_page=100&page={page}')
            if any(label['name'] == PENDING_LABEL for label in labels):
                return
            if len(labels) < 100:
                self.request('POST', 'labels', {'name': PENDING_LABEL, 'color': 'FBCA04',
                                               'description': '新发布的剧情翻译任务'})
                return
        raise ValueError('Too many label pages')


def synchronize(client, selected, apply=False, changed_ids=()):
    selected = {stem: story for stem, story in selected.items()
                if not is_short_story(stem) and not is_short_story(story['id'])}
    if not selected:
        return []
    by_title = {}
    for issue in client.issues():
        if issue.get('title') in selected:
            by_title.setdefault(issue['title'], []).append(issue)
    if any(len(matches) > 1 for matches in by_title.values()):
        raise ValueError('Duplicate task Issues; resolve duplicates before synchronization')
    # Validate the complete plan before any writes.
    plans = [plan(s, next(iter(by_title.get(s['id'], [])), None), s['id'] in changed_ids)
             for s in selected.values()]
    if apply:
        if any(item['action'] == 'create' for item in plans):
            client.ensure_task_label()
        for item in plans:
            if item['action'] == 'create':
                result = client.request('POST', 'issues', item['payload'])
                item['number'] = result['number']
            else:
                # Re-read before modifying to preserve newly claimed tracks, text,
                # and assignees. GitHub Issues do not offer atomic compare-and-swap.
                fresh = client.request('GET', f'issues/{item["number"]}')
                if fresh.get('title') != item['id']:
                    raise ValueError('Task title changed concurrently')
                update = plan(selected[item['id']], fresh, item['id'] in changed_ids)
                item.update(update)
                if item['action'] == 'update':
                    client.request('PATCH', f'issues/{item["number"]}', item['payload'])
    return [{k: v for k, v in p.items() if k != 'payload'} for p in plans]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--files', nargs='+')
    selection.add_argument('--all', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--source-change-receipt', type=Path)
    args = parser.parse_args()
    changed = set()
    receipt = None
    filenames = args.files
    if args.source_change_receipt:
        receipt = json.loads(args.source_change_receipt.read_text())
        if receipt.get('schema_version') != 1 or not isinstance(receipt.get('task_candidates'), list):
            raise ValueError('Invalid source change receipt')
        candidates = receipt['task_candidates']
        if any(not isinstance(s, dict) or not isinstance(s.get('file_id'), str) or
               not STEM.fullmatch(s['file_id']) or s.get('status') not in ('new', 'source_changed')
               for s in candidates):
            raise ValueError('Invalid changed source ID')
        changed = {s['file_id'] for s in candidates if s['status'] == 'source_changed'}
        if filenames is None and not args.all:
            filenames = [s['file_id'] for s in candidates if not is_short_story(s['file_id'])]
    elif filenames is None and not args.all:
        parser.error('Select --files, --all, or --source-change-receipt')
    selected = stories(args.data_dir, filenames)
    if receipt:
        for candidate in receipt['task_candidates']:
            story = selected.get(candidate['file_id'])
            if story and story['source_sha256'] != candidate.get('source_sha256'):
                raise ValueError('Receipt does not match current CSV source')
    result = synchronize(GitHub(args.repository), selected, args.apply, changed)
    print(json.dumps({'applied': args.apply, 'tasks': result}, ensure_ascii=False))


if __name__ == '__main__':
    main()
