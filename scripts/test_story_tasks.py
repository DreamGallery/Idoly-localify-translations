import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('story_tasks', Path(__file__).with_name('story_tasks.py'))
tasks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tasks)


class Fake:
    def __init__(self, issues=()):
        self.existing = list(issues)
        self.writes = []
    def issues(self):
        return self.existing
    def ensure_task_label(self):
        pass
    def request(self, method, endpoint, payload=None):
        if method == 'GET':
            return next(i for i in self.existing if endpoint == f'issues/{i["number"]}')
        self.writes.append((method, endpoint, payload))
        if method == 'POST':
            issue = {**payload, 'number': len(self.existing) + 1, 'state': 'open'}
            self.existing.append(issue)
            return issue
        issue = next(i for i in self.existing if endpoint == f'issues/{i["number"]}')
        issue.update(payload)
        return issue


class StoryTaskTests(unittest.TestCase):
    def setUp(self):
        self.story = {'id': 'adv_hbd_ai_2026', 'path': 'story/ai/hbd/ai/adv_hbd_ai_2026.csv', 'source_sha256': 'a' * 64}
    def test_unicode_group_path_preserved(self):
        story = {**self.story, 'path': 'story/ai/group/サニーピース/ⅢX/a.csv'}
        self.assertIn('story/reviewed/group/サニーピース/ⅢX/a.csv', tasks.task_body(story))
        self.assertTrue(tasks.PATH.fullmatch(story['path']))
    def test_real_path_and_viewer_tracks(self):
        body = tasks.task_body(self.story)
        self.assertIn('<!-- translated_path: story/human/hbd/ai/adv_hbd_ai_2026.csv -->', body)
        self.assertIn('<!-- proofread_path: story/reviewed/hbd/ai/adv_hbd_ai_2026.csv -->', body)
        self.assertIn('<!-- tr::待认领 -->', body)
        self.assertIn('<!-- pr::待认领 -->', body)
    def test_new_issue_has_pending_translation_label(self):
        self.assertEqual(tasks.plan(self.story)['payload']['labels'], ['待翻译'])
    def test_existing_label_is_not_overwritten(self):
        class Labels(tasks.GitHub):
            def request(self, method, endpoint, payload=None):
                self.calls.append((method, endpoint, payload))
                return [{'name': '待翻译'}]
        client = Labels('owner/repo')
        client.calls = []
        client.ensure_task_label()
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], 'GET')
    def test_closed_task_deduplicated_and_human_state_preserved(self):
        body = tasks.task_body(self.story).replace('tr::待认领', 'tr:alice:完成').replace('pr::待认领', 'pr:bob:完成') + '\n人工备注'
        client = Fake([{'number': 9, 'title': self.story['id'], 'body': body, 'state': 'closed', 'assignees': ['alice', 'bob']}])
        result = tasks.synchronize(client, {self.story['id']: self.story}, True)
        self.assertEqual(result[0]['action'], 'skip')
        self.assertFalse(client.writes)
    def test_source_change_reopens_without_overwriting_tracks_or_assignees(self):
        old = {**self.story, 'source_sha256': 'b' * 64}
        body = tasks.task_body(old).replace('tr::待认领', 'tr:alice:完成') + '\n人工备注'
        issue = {'number': 9, 'title': self.story['id'], 'body': body, 'state': 'closed', 'assignees': ['alice']}
        result = tasks.plan(self.story, issue)
        self.assertEqual(result['payload']['state'], 'open')
        self.assertIn('<!-- tr: alice:进行中 -->', result['payload']['body'])
        self.assertIn('人工备注', result['payload']['body'])
        self.assertNotIn('assignees', result['payload'])
    def test_replayed_receipt_does_not_reopen_newly_completed_task(self):
        issue = {'number': 1, 'title': self.story['id'], 'body': tasks.task_body(self.story), 'state': 'closed'}
        self.assertEqual(tasks.plan(self.story, issue, True)['action'], 'skip')
    def test_duplicate_issue_aborts_all_writes(self):
        issue = {'number': 1, 'title': self.story['id'], 'body': '', 'state': 'open'}
        client = Fake([issue, {**issue, 'number': 2}])
        with self.assertRaisesRegex(ValueError, 'Duplicate task'):
            tasks.synchronize(client, {self.story['id']: self.story}, True)
        self.assertFalse(client.writes)
    def test_dry_run_and_retry_are_idempotent(self):
        client = Fake()
        selected = {self.story['id']: self.story}
        self.assertEqual(tasks.synchronize(client, selected)[0]['action'], 'create')
        self.assertFalse(client.writes)
        tasks.synchronize(client, selected, True)
        self.assertEqual(tasks.synchronize(client, selected, True)[0]['action'], 'skip')
        self.assertEqual(len(client.writes), 1)
    def test_fresh_claim_preserved(self):
        class Claiming(Fake):
            def request(self, method, endpoint, payload=None):
                if method == 'GET':
                    self.existing[0]['body'] = '<!-- tr:new-user:进行中 -->\n<!-- pr::待认领 -->'
                return super().request(method, endpoint, payload)
        client = Claiming([{'number': 1, 'title': self.story['id'], 'body': '', 'state': 'open'}])
        tasks.synchronize(client, {self.story['id']: self.story}, True)
        self.assertIn('tr:new-user:进行中', client.writes[0][2]['body'])
    def test_inventory_uses_metadata_and_skips_empty_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / self.story['path']
            path.parent.mkdir(parents=True)
            rows = [['1:title:title', '', '原文', '译文'], ['info', self.story['id'] + '.txt', 'a' * 64, ''], ['译者', '', '', '']]
            def write(target, data):
                with target.open('w', newline='') as f:
                    writer = csv.writer(f); writer.writerow(tasks.FIELDS); writer.writerows(data)
            write(path, rows)
            write(path.with_name('empty.csv'), [['info', 'adv_empty.txt', 'b' * 64, ''], ['译者', '', '', '']])
            self.assertEqual(list(tasks.stories(root, [self.story['id'] + '.txt'])), [self.story['id']])
            self.assertEqual(len(tasks.stories(root)), 1)
            with self.assertRaises(ValueError):
                tasks.stories(root, ['../unknown.txt'])
            write(path.with_name('duplicate.csv'), rows)
            with self.assertRaisesRegex(ValueError, 'Duplicate story'):
                tasks.stories(root)
    def test_path_change_does_not_invalidate_completion(self):
        old = {**self.story, 'path': 'story/ai/old/' + self.story['id'] + '.csv'}
        body = tasks.task_body(old).replace('tr::待认领', 'tr:alice:完成')
        result = tasks.plan(self.story, {'number': 1, 'body': body, 'state': 'closed'})
        self.assertNotIn('state', result['payload'])
        self.assertIn('tr:alice:完成', result['payload']['body'])
        self.assertIn(self.story['path'], result['payload']['body'])
    def test_empty_selection_never_calls_github(self):
        self.assertEqual(tasks.synchronize(None, {}, True), [])
    def test_pagination_ignores_prs(self):
        class Paged(tasks.GitHub):
            def request(self, method, endpoint, payload=None):
                if endpoint.endswith('&page=1'):
                    return [{'number': i, 'pull_request': {}} for i in range(100)]
                return [{'number': 101, 'title': 'adv_found', 'state': 'closed'}]
        self.assertEqual(Paged('owner/repo').issues()[0]['number'], 101)
    def test_conflicting_markers_and_invalid_tracks_fail(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            tasks.task_body(self.story, '<!-- tr::完成 -->\n<!-- tr::待认领 -->')
        with self.assertRaisesRegex(ValueError, 'Invalid task'):
            tasks.task_body(self.story, '<!-- tr:bad:unexpected -->')


if __name__ == '__main__':
    unittest.main()
