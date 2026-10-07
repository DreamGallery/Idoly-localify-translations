import copy
import json
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import sync_story_sources as sync


def receipt(sha='a' * 64, status='unchanged', path='group/月のテンペスト/01/adv_group_01.csv'):
    row = {'file_id': 'adv_group_01', 'source_sha256': sha, 'csv_path': path, 'status': status}
    return {'scripts': [row], 'task_candidates': [row] if status in ('new', 'source_changed') else [],
            'source_changed': ['adv_group_01'] if status == 'source_changed' else []}


class StateTests(unittest.TestCase):
    def baseline(self):
        return sync.next_state({}, receipt(), '1' * 40, 1066)

    def test_initial_baseline_does_not_open_all_existing_tasks(self):
        self.assertEqual(self.baseline()['pending_tasks'], {})

    def test_manual_exact_filename_and_unknown_rejection(self):
        state = sync.next_state({}, receipt(), '1' * 40, 1066, 'adv_group_01.txt')
        self.assertEqual(list(state['pending_tasks']), ['adv_group_01'])
        for bad in ('../adv_group_01.txt', 'adv_missing', '$(echo secret)'):
            with self.assertRaises(ValueError):
                sync.next_state({}, receipt(), '1' * 40, 1066, bad)

    def test_ledger_detects_change_already_imported_by_release(self):
        state = sync.next_state(self.baseline(), receipt('b' * 64), '2' * 40, 1067)
        self.assertTrue(state['pending_tasks']['adv_group_01']['source_changed'])

    def test_browser_copied_filename_trims_invisible_boundary_markers(self):
        for name in ('adv_group_01.csv\u200e', '\u200f adv_group_01.txt \u200e',
                     '\ufeff\u200badv_group_01\u200b',
                     'story/ai/group/月のテンペスト/01/adv_group_01.csv\u200e'):
            with self.subTest(name=ascii(name)):
                state = sync.next_state({}, receipt(), '1' * 40, 1066, name)
                self.assertEqual(list(state['pending_tasks']), ['adv_group_01'])

    def test_blank_or_interior_markers_do_not_select_a_story(self):
        for name in ('', ' \u200e\u200b ', 'adv_group_\u200e01.csv', '../adv_group_01.csv\u200e'):
            with self.subTest(name=ascii(name)), self.assertRaises(ValueError):
                sync.next_state({}, receipt(), '1' * 40, 1066, name)

    def test_unknown_filename_error_identifies_input(self):
        with self.assertRaisesRegex(ValueError, 'adv_missing.csv.*matched 0'):
            sync.next_state({}, receipt(), '1' * 40, 1066, 'adv_missing.csv')

    def test_batch_id_matches_all_chapters_but_not_short_or_neighbor_id(self):
        data = receipt()
        names = ['adv_card_ktn_15_01', 'adv_card_ktn_15_02', 'adv_card_ktn_15_03',
                 'adv_card_ktn_15_01_short', 'adv_card_ktn_150_01', 'adv_card_ktn_16_01']
        data['scripts'] = [{**data['scripts'][0], 'file_id': name,
                            'csv_path': 'card/' + name + '.csv'} for name in names]
        data['scripts'].append({**data['scripts'][0], 'file_id': 'adv_card_ktn_15_04',
                                'status': 'retired'})
        state = sync.next_state({}, data, '1' * 40, 1066, story_id='adv_card_ktn_15\u200e')
        self.assertEqual(sorted(state['pending_tasks']), names[:3])
        retry = sync.next_state(state, data, '1' * 40, 1066, story_id='adv_card_ktn_15')
        self.assertEqual(retry, state)

    def test_batch_rejects_empty_broad_unsafe_or_missing_id(self):
        for value in ('', 'adv', 'adv_card', '../adv_group_01', 'adv_group_01*',
                      'adv_group_01.csv', 'adv_group_99'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sync.next_state({}, receipt(), '1' * 40, 1066, story_id=value)
        with self.assertRaisesRegex(ValueError, 'not both'):
            sync.next_state({}, receipt(), '1' * 40, 1066,
                            filename='adv_group_01.csv', story_id='adv_group_01')

    def test_pending_survives_retry_and_does_not_mutate_input(self):
        state = sync.next_state(self.baseline(), receipt('b' * 64), '2' * 40, 1067)
        before = copy.deepcopy(state)
        retry = sync.next_state(state, receipt('b' * 64), '2' * 40, 1067)
        self.assertEqual(retry, before)
        self.assertEqual(state, before)
        sync.next_state(state, receipt('c' * 64), '3' * 40, 1068)
        self.assertEqual(state, before)

    def test_path_only_change_preserves_completion(self):
        state = sync.next_state(self.baseline(), receipt(path='group/new/adv_group_01.csv'), '2' * 40, 1067)
        self.assertFalse(state['pending_tasks']['adv_group_01']['source_changed'])

    def test_revision_downgrade_rejected(self):
        with self.assertRaises(ValueError):
            sync.next_state(self.baseline(), receipt(), '2' * 40, 1065)

    def test_new_story_is_queued(self):
        state = sync.next_state({}, receipt(status='new'), '1' * 40, 1066)
        self.assertEqual(len(state['pending_tasks']), 1)

    def test_push_requires_unchanged_remote_even_without_data_changes(self):
        with patch.object(sync, 'git', side_effect=['local', '', 'newer']):
            with self.assertRaisesRegex(ValueError, 'advanced'):
                sync.publish_data(Path('.'), [], 'local', 'message')

    def test_main_write_is_never_allowed(self):
        with patch.object(sync, 'git') as git:
            with self.assertRaisesRegex(ValueError, 'collaboration'):
                sync.publish_data(Path('.'), [], 'local', 'message', branch='main')
            git.assert_not_called()

    def test_unexpected_output_is_not_staged(self):
        for path in ('scripts/evil.py', '../leak', 'story/../secret'):
            with self.assertRaises(ValueError):
                sync.publish_data(Path('.'), [path], 'x', 'message')

    def test_real_git_archive_rename_commits_both_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            remote, checkout = base / 'remote.git', base / 'checkout'
            def command(*args):
                return subprocess.run(['git', *map(str, args)], check=True,
                                      capture_output=True, text=True).stdout.strip()
            command('init', '--bare', remote)
            command('init', '-b', 'collaboration', checkout)
            command('-C', checkout, 'config', 'user.name', 'Test')
            command('-C', checkout, 'config', 'user.email', 'test@example.invalid')
            old = 'story/human/group/月のテンペスト/adv_group_01.csv'
            new = 'archive/backups/source/group/月のテンペスト/adv_group_01.csv'
            (checkout / old).parent.mkdir(parents=True)
            (checkout / old).write_text('historical translation\n')
            command('-C', checkout, 'add', '.')
            command('-C', checkout, 'commit', '-m', 'initial')
            command('-C', checkout, 'remote', 'add', 'origin', remote)
            command('-C', checkout, 'push', 'origin', 'collaboration')
            head = command('-C', checkout, 'rev-parse', 'HEAD')
            command('-C', checkout, 'push', 'origin', 'HEAD:refs/heads/main')
            (checkout / new).parent.mkdir(parents=True)
            (checkout / old).rename(checkout / new)
            result = sync.publish_data(checkout, [old, new], head, 'archive translation')
            self.assertNotEqual(result, head)
            self.assertEqual(command('--git-dir', remote, 'rev-parse', 'collaboration'), result)
            self.assertEqual(command('--git-dir', remote, 'rev-parse', 'main'), head)
            self.assertEqual(command('-C', checkout, 'status', '--porcelain'), '')


class SyncIntegrationTests(unittest.TestCase):
    def command(self, *args):
        return subprocess.run(['git', *map(str, args)], check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, root, message):
        self.command('-C', root, 'add', '.')
        self.command('-C', root, 'commit', '-m', message)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.source, self.root, self.remote = [base / p for p in ('source', 'data', 'remote.git')]
        self.command('init', '--bare', self.remote)
        for root, branch in ((self.source, 'main'), (self.root, 'collaboration')):
            self.command('init', '-b', branch, root)
            self.command('-C', root, 'config', 'user.name', 'Test')
            self.command('-C', root, 'config', 'user.email', 'test@example.invalid')
        self.stem = 'adv_test_01'
        self.relative = 'test/' + self.stem + '.csv'
        raw = b'[title title=test]\n'
        source = self.source / ('Resource/' + self.stem + '.txt')
        source.parent.mkdir()
        source.write_bytes(raw)
        rows = sync.migration.source_fields(raw.decode()) + [
            {'id': 'info', 'name': self.stem+'.txt', 'text': sync.migration.digest(raw), 'trans': ''},
            {'id': '译者', 'name': '', 'text': '', 'trans': ''}]
        csv = self.source / 'CSV' / self.relative
        csv.parent.mkdir(parents=True)
        csv.write_bytes(sync.migration.encode_csv(rows))
        (self.source / 'revision').write_text('1')
        self.commit(self.source, 'source snapshot')
        (self.root / 'upstream.json').write_text('{}\n')
        self.commit(self.root, 'data baseline')
        self.initial = self.command('-C', self.root, 'rev-parse', 'HEAD')
        self.command('-C', self.root, 'remote', 'add', 'origin', self.remote)
        self.command('-C', self.root, 'push', 'origin', 'collaboration', 'HEAD:refs/heads/main')

    def test_skip_tasks_publishes_pinned_manifest_and_keeps_pending_ledger(self):
        with patch.object(sync.tasks, 'GitHub') as github, patch.object(sync.tasks, 'synchronize') as tasks:
            result = sync.run(self.source, self.root, apply=True, skip_tasks=True)
            github.assert_not_called()
            tasks.assert_not_called()
        manifest = json.loads((self.root / sync.MANIFEST).read_text())
        self.assertEqual(manifest['source_commit'], self.command('-C', self.source, 'rev-parse', 'HEAD'))
        self.assertEqual(manifest['source_repository'], sync.SOURCE_REPOSITORY)
        self.assertEqual(manifest['scripts'][self.stem]['raw_path'], 'Resource/'+self.stem+'.txt')
        self.assertEqual(manifest['scripts'][self.stem]['csv_path'], self.relative)
        ledger = json.loads((self.root / sync.STATE).read_text())
        self.assertIn(self.stem, ledger['pending_tasks'])
        self.assertEqual(result['data_branch'], 'collaboration')
        self.assertTrue(result['tasks_skipped'])
        self.assertEqual(self.command('--git-dir', self.remote, 'rev-parse', 'main'), self.initial)
        with patch.object(sync.tasks, 'GitHub'), patch.object(sync.tasks, 'synchronize', return_value=[]) as tasks:
            sync.run(self.source, self.root, apply=True)
            selected = tasks.call_args.args[1][self.stem]
            self.assertEqual(selected['source_commit'], manifest['source_commit'])
            self.assertEqual(selected['data_branch'], 'collaboration')
        self.assertEqual(json.loads((self.root / sync.STATE).read_text())['pending_tasks'], {})

    def test_failed_issue_reconcile_keeps_durable_pending_ledger(self):
        with patch.object(sync.tasks, 'GitHub'), patch.object(sync.tasks, 'synchronize', side_effect=RuntimeError('API failed')):
            with self.assertRaisesRegex(RuntimeError, 'API failed'):
                sync.run(self.source, self.root, apply=True)
        remote_ledger = self.command('--git-dir', self.remote, 'show', 'collaboration:'+sync.STATE)
        self.assertIn(self.stem, json.loads(remote_ledger)['pending_tasks'])

    def test_nonforce_push_rejects_write_between_head_check_and_push(self):
        competitor = self.root.parent / 'competitor'
        self.command('clone', '--branch', 'collaboration', self.remote, competitor)
        self.command('-C', competitor, 'config', 'user.name', 'Test')
        self.command('-C', competitor, 'config', 'user.email', 'test@example.invalid')
        original_git = sync.git
        competitor_head = []
        def race(root, *args):
            if args[0] == 'push':
                draft = competitor / 'story/drafts/translation/live.csv'
                draft.parent.mkdir(parents=True)
                draft.write_text('unpublished work')
                self.commit(competitor, 'Autosave live translation')
                self.command('-C', competitor, 'push', 'origin', 'collaboration')
                competitor_head.append(self.command('-C', competitor, 'rev-parse', 'HEAD'))
            return original_git(root, *args)
        with patch.object(sync, 'git', side_effect=race), patch.object(sync.tasks, 'GitHub') as github:
            with self.assertRaisesRegex(RuntimeError, 'Git operation failed: push'):
                sync.run(self.source, self.root, apply=True)
            github.assert_not_called()
        self.assertEqual(self.command('--git-dir', self.remote, 'rev-parse', 'collaboration'), competitor_head[0])
        self.assertEqual(self.command('--git-dir', self.remote, 'show', 'collaboration:story/drafts/translation/live.csv'), 'unpublished work')
        self.assertEqual(self.command('--git-dir', self.remote, 'rev-parse', 'main'), self.initial)


class WorkflowRoutingTests(unittest.TestCase):
    def test_only_trusted_code_is_executed_with_separate_collaboration_data(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('story-task.yml', 'sync-story-sources.yml'):
            with self.subTest(workflow=name):
                text = (root / '.github/workflows' / name).read_text()
                self.assertIn('ref: main\n          path: trusted', text)
                self.assertIn('ref: collaboration\n          path: translations', text)
                self.assertIn('python3 trusted/scripts/sync_story_sources.py', text)
                self.assertIn('--data-dir translations --branch collaboration', text)
                self.assertNotIn('python3 translations/scripts/', text)
                self.assertIn("if: github.ref == 'refs/heads/main'", text)


if __name__ == '__main__':
    unittest.main()
