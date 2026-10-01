import copy
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
            command('init', '-b', 'main', checkout)
            command('-C', checkout, 'config', 'user.name', 'Test')
            command('-C', checkout, 'config', 'user.email', 'test@example.invalid')
            old = 'story/human/group/月のテンペスト/adv_group_01.csv'
            new = 'archive/backups/source/group/月のテンペスト/adv_group_01.csv'
            (checkout / old).parent.mkdir(parents=True)
            (checkout / old).write_text('historical translation\n')
            command('-C', checkout, 'add', '.')
            command('-C', checkout, 'commit', '-m', 'initial')
            command('-C', checkout, 'remote', 'add', 'origin', remote)
            command('-C', checkout, 'push', 'origin', 'main')
            head = command('-C', checkout, 'rev-parse', 'HEAD')
            (checkout / new).parent.mkdir(parents=True)
            (checkout / old).rename(checkout / new)
            result = sync.publish_data(checkout, [old, new], head, 'archive translation')
            self.assertNotEqual(result, head)
            self.assertEqual(command('--git-dir', remote, 'rev-parse', 'main'), result)
            self.assertEqual(command('-C', checkout, 'status', '--porcelain'), '')


if __name__ == '__main__':
    unittest.main()
