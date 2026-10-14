import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sync_machine_translations as sync


STEM = 'adv_card_szk_54_01'
RELATIVE = 'story/ai/card/szk/54/' + STEM + '.csv'


def rows(translations=('', ''), sha='a' * 64):
    return [
        {'id': '3:title:1', 'name': '', 'text': 'タイトル', 'trans': translations[0]},
        {'id': '12:text:1', 'name': '雫', 'text': '「こんにちは」\n次の行', 'trans': translations[1]},
        {'id': 'info', 'name': STEM + '.txt', 'text': sha, 'trans': ''},
        {'id': '译者', 'name': '', 'text': '', 'trans': ''},
    ]


def story(translations=('', ''), *, path=RELATIVE, sha='a' * 64):
    values = rows(translations, sha)
    return sync.Story(path, '100644', sync.migration.encode_csv(values), values)


class PlanTests(unittest.TestCase):
    def test_fill_only_empty_fields_preserving_metadata_and_existing_text(self):
        source = story(('机器标题', '「你好」\n逗号,引号"'))
        target = story(('人工保留的标题', ' \t'))
        target.rows[-1]['trans'] = '协作者'
        original = copy.deepcopy(target.rows)
        changes, report = sync.plan({STEM: source}, {STEM: target})
        updated = sync.migration.parse_csv(changes[RELATIVE], STEM + '.csv')
        self.assertEqual(updated[0]['trans'], '人工保留的标题')
        self.assertEqual(updated[1]['trans'], source.rows[1]['trans'])
        self.assertEqual(updated[-2:], target.rows[-2:])
        self.assertEqual(report['filled_rows'], 1)
        self.assertEqual(target.rows, original)

    def test_empty_machine_fields_are_not_a_successful_fill(self):
        changes, report = sync.plan({STEM: story(('标题', ' \n'))}, {STEM: story()})
        self.assertEqual(report['filled_rows'], 1)
        self.assertEqual(sync.migration.parse_csv(changes[RELATIVE], STEM + '.csv')[1]['trans'], '')
        self.assertEqual(sync.plan({STEM: story()}, {STEM: story()})[0], {})

    def test_changed_original_hash_or_fields_are_skipped(self):
        source = story(('标题', '你好'))
        for change, reason in (
                (lambda v: v[-2].update(text='b' * 64), 'source_changed'),
                (lambda v: v[1].update(text='別の原文'), 'fields_changed'),
                (lambda v: v[1].update(name='別の人'), 'fields_changed'),
                (lambda v: v[1].update(id='13:text:1'), 'fields_changed')):
            with self.subTest(reason=reason):
                target = story()
                change(target.rows)
                changes, report = sync.plan({STEM: source}, {STEM: target})
                self.assertEqual(changes, {})
                self.assertEqual(report['skipped'][reason], [STEM])

    def test_reclassification_uses_target_path_and_does_not_recreate_missing_stories(self):
        new_path = 'story/ai/新分类/' + STEM + '.csv'
        source = {STEM: story(('标题', '你好'))}
        changes, _ = sync.plan(source, {STEM: story(path=new_path)})
        self.assertEqual(set(changes), {new_path})
        changes, report = sync.plan(source, {})
        self.assertEqual(changes, {})
        self.assertEqual(report['skipped']['missing_in_collaboration'], [STEM])


class GitTests(unittest.TestCase):
    def command(self, root, *args):
        return subprocess.check_output(['git', '-C', str(root), *map(str, args)],
                                       stderr=subprocess.PIPE).decode().strip()

    def write(self, root, relative, data):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit(self, root, message):
        self.command(root, 'add', '.')
        self.command(root, 'commit', '-m', message)
        return self.command(root, 'rev-parse', 'HEAD')

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root, self.remote, self.editor = [self.base / p for p in ('repo', 'remote.git', 'editor')]
        self.command(self.base, 'init', '--bare', self.remote)
        self.command(self.base, 'init', '-b', 'collaboration', self.root)
        self.command(self.root, 'config', 'user.name', 'Test')
        self.command(self.root, 'config', 'user.email', 'test@example.invalid')
        self.write(self.root, RELATIVE, story().data)
        self.protected = {
            'story/human/card/' + STEM + '.csv': b'human translation\n',
            'story/reviewed/card/' + STEM + '.csv': b'reviewed translation\n',
            'story/drafts/translation/' + STEM + '.csv': b'unpublished draft\n',
            'story/drafts/proofread/' + STEM + '.csv': b'unpublished review\n',
            'records/' + STEM + '.json': '{"translation":{"state":"翻译中"}}\n'.encode('utf-8'),
            'automation/story-sources.json': b'{"keep":"source ledger"}\n',
            'scripts/not-executed.py': b'raise SystemExit("must not execute")\n',
        }
        for path, data in self.protected.items():
            self.write(self.root, path, data)
        self.initial = self.commit(self.root, 'originals and work in progress')
        self.command(self.root, 'remote', 'add', 'origin', self.remote)
        self.command(self.root, 'push', 'origin', 'collaboration')
        self.command(self.root, 'switch', '-c', 'main')
        self.write(self.root, RELATIVE, story(('机器标题', '机器正文')).data)
        self.main = self.commit(self.root, 'published machine translations')
        self.command(self.root, 'push', 'origin', 'main')
        self.command(self.base, 'clone', '--branch', 'collaboration', self.remote, self.editor)
        self.command(self.editor, 'config', 'user.name', 'Human')
        self.command(self.editor, 'config', 'user.email', 'human@example.invalid')

    def remote_head(self, branch='collaboration'):
        return self.command(self.remote, 'rev-parse', branch)

    def remote_bytes(self, relative):
        return subprocess.check_output(['git', '-C', str(self.remote), 'show', 'collaboration:' + relative])

    def publish_edit(self, *, source_changed=False):
        self.write(self.editor, RELATIVE,
                   story(('并发编辑的标题', ''), sha='b' * 64 if source_changed else 'a' * 64).data)
        self.write(self.editor, 'story/drafts/translation/' + STEM + '.csv', b'new unpublished draft\n')
        commit = self.commit(self.editor, 'save human work')
        self.command(self.editor, 'push', 'origin', 'collaboration')
        return commit

    def test_dry_run_does_not_commit_or_modify_local_files(self):
        report = sync.run(self.root)
        self.assertEqual(report['changed_files'], 1)
        self.assertEqual(report['filled_rows'], 2)
        self.assertEqual(self.remote_head(), self.initial)
        self.assertEqual(self.command(self.root, 'status', '--porcelain'), '')

    def test_apply_and_retry_preserve_all_work_and_user_index(self):
        self.write(self.root, 'local-only.txt', b'local staged work')
        self.command(self.root, 'add', 'local-only.txt')
        self.write(self.root, 'unstaged.txt', b'local unstaged work')
        status = self.command(self.root, 'status', '--porcelain')
        report = sync.run(self.root, apply=True)
        tip = self.remote_head()
        self.assertEqual(report['target_commit'], tip)
        self.assertEqual(self.command(self.remote, 'rev-parse', tip + '^'), self.initial)
        self.assertEqual(self.command(self.remote, 'diff', '--name-only', self.initial, tip), RELATIVE)
        for path, value in self.protected.items():
            self.assertEqual(self.remote_bytes(path), value)
        self.assertEqual(self.remote_head('main'), self.main)
        self.assertEqual(self.command(self.root, 'status', '--porcelain'), status)
        self.assertEqual(self.command(self.root, 'rev-parse', 'HEAD'), self.main)
        self.assertEqual(sync.run(self.root, apply=True)['filled_rows'], 0)
        self.assertEqual(self.remote_head(), tip)

    def test_concurrent_edit_replans_without_overwriting_new_work(self):
        original_git = sync.git
        parents = []

        def racing_git(root, *args, **kwargs):
            if args[0] == 'push' and not parents:
                parents.append(self.publish_edit())
            return original_git(root, *args, **kwargs)

        with patch.object(sync, 'git', side_effect=racing_git):
            report = sync.run(self.root, apply=True)
        self.assertEqual(report['attempts'], 2)
        self.assertEqual(report['filled_rows'], 1)
        self.assertEqual(self.command(self.remote, 'rev-parse', 'collaboration^'), parents[0])
        actual = sync.migration.parse_csv(self.remote_bytes(RELATIVE), STEM + '.csv')
        self.assertEqual([r['trans'] for r in actual[:2]], ['并发编辑的标题', '机器正文'])
        self.assertEqual(self.remote_bytes('story/drafts/translation/' + STEM + '.csv'), b'new unpublished draft\n')

    def test_concurrent_source_update_is_not_filled_with_old_machine_text(self):
        original_git = sync.git
        parents = []

        def racing_git(root, *args, **kwargs):
            if args[0] == 'push' and not parents:
                parents.append(self.publish_edit(source_changed=True))
            return original_git(root, *args, **kwargs)

        with patch.object(sync, 'git', side_effect=racing_git):
            report = sync.run(self.root, apply=True)
        self.assertEqual(report['filled_rows'], 0)
        self.assertEqual(report['skipped']['source_changed'], [STEM])
        self.assertEqual(self.remote_head(), parents[0])

    def test_transport_error_is_not_retried(self):
        original_git = sync.git
        pushes = []

        def failing_git(root, *args, **kwargs):
            if args[0] == 'push':
                pushes.append(args)
                raise sync.GitFailure('push', 128)
            return original_git(root, *args, **kwargs)

        with patch.object(sync, 'git', side_effect=failing_git), self.assertRaises(sync.GitFailure):
            sync.run(self.root, apply=True)
        self.assertEqual(len(pushes), 1)
        self.assertEqual(self.remote_head(), self.initial)

    def test_lost_push_response_does_not_duplicate_commit(self):
        original_git = sync.git

        def lost_response(root, *args, **kwargs):
            result = original_git(root, *args, **kwargs)
            if args[0] == 'push':
                raise sync.GitFailure('push', 128)
            return result

        with patch.object(sync, 'git', side_effect=lost_response):
            report = sync.run(self.root, apply=True)
        self.assertEqual(report['attempts'], 1)
        self.assertEqual(report['target_commit'], self.remote_head())
        self.assertEqual(self.command(self.remote, 'rev-parse', 'collaboration^'), self.initial)

    def test_only_existing_ai_csv_paths_can_be_committed(self):
        current = sync.read_stories(self.root, self.main)
        for path in ('story/human/' + STEM + '.csv', 'story/ai/../private.csv',
                     'story/ai/missing/adv_new_01.csv', '.github/workflows/release.yml'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                sync.commit_changes(self.root, self.main, {path: b'invalid'}, current)

    def test_duplicate_story_and_symlink_are_rejected(self):
        self.write(self.root, 'story/ai/duplicate/' + STEM + '.csv', story().data)
        head = self.commit(self.root, 'duplicate')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            sync.read_stories(self.root, head)
        (self.root / ('story/ai/duplicate/' + STEM + '.csv')).unlink()
        (self.root / ('story/ai/duplicate/' + STEM + '.csv')).symlink_to('/tmp/never-read')
        head = self.commit(self.root, 'symlink')
        with self.assertRaisesRegex(ValueError, 'Non-regular'):
            sync.read_stories(self.root, head)

    def test_malformed_source_fails_before_any_push(self):
        self.write(self.root, RELATIVE, b'id,name,text,trans\ninvalid,,,\n')
        self.commit(self.root, 'bad machine data')
        self.command(self.root, 'push', 'origin', 'main')
        with self.assertRaises(ValueError):
            sync.run(self.root, apply=True)
        self.assertEqual(self.remote_head(), self.initial)


class WorkflowTests(unittest.TestCase):
    def test_all_entry_points_use_trusted_code_and_share_release_lock(self):
        root = Path(__file__).resolve().parents[1] / '.github/workflows'
        for name, checkout in (('release.yml', 'translations-main'),
                               ('story-task.yml', 'trusted'),
                               ('sync-story-sources.yml', 'trusted'),
                               ('sync-machine-translations.yml', 'trusted')):
            with self.subTest(name=name):
                source = (root / name).read_text()
                self.assertIn('group: idoly-localization-release', source)
                self.assertIn('python3 ' + checkout + '/scripts/sync_machine_translations.py', source)
                self.assertIn('--repository-dir ' + checkout, source)
                self.assertIn('contents: write', source)
        release = (root / 'release.yml').read_text()
        fill_step = release.split('- name: Fill published machine translations into collaboration')[1].split('- name:')[0]
        self.assertIn('if: ${{ !inputs.dry_run }}', fill_step)
        self.assertNotIn('always()', fill_step)


if __name__ == '__main__':
    unittest.main()
