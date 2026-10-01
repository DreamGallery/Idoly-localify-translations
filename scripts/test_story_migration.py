import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import story_migration as m


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.csv, self.repo = [self.root / p for p in ('Resource', 'CSV', 'repo')]
        for p in (self.source, self.csv, self.repo): p.mkdir()
        self.stem = 'adv_event_test'
        self.rel = 'event/1/' + self.stem + '.csv'

    def snapshot(self, script):
        raw = script.encode()
        (self.source / (self.stem + '.txt')).write_bytes(raw)
        rows = m.source_fields(script) + [dict(id='info', name=self.stem+'.txt', text=m.digest(raw), trans=''), dict(id='译者', name='', text='', trans='')]
        self.put(self.csv / self.rel, rows)
        return rows

    def put(self, path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(m.encode_csv(rows))

    def layer(self, layer, rows, rel=None):
        path = self.repo / 'story' / layer / (rel or self.rel)
        self.put(path, rows)
        return path

    def plan(self):
        return m.plan(self.source, self.csv, self.repo)

    def test_new_only_and_idempotent(self):
        self.snapshot('[message name=a text=hello]\n')
        receipt, writes, deletes = self.plan()
        self.assertEqual(receipt['task_candidates'][0]['status'], 'new')
        m.apply(self.repo, writes, deletes)
        receipt, writes, deletes = self.plan()
        self.assertEqual((writes, deletes, receipt['task_candidates']), ({}, set(), []))

    def test_changed_archives_and_invalidates_records(self):
        old = self.snapshot('[message name=a text=keep]\n[message name=a text=old]\n')
        old[0]['trans'], old[1]['trans'] = '保留', '旧文'
        self.layer('ai', old)
        formal = self.layer('reviewed', old)
        record = {'file_id': self.stem, 'translation': {'revision': 3, 'state': '完成'}, 'proofread': {'revision': 4, 'state': '完成', 'operator_github': 'editor'}, 'force_complete': {'translation': True, 'proofread': True}, 'artifacts': {'proofread_csv': {'path': formal.relative_to(self.repo).as_posix()}, 'proofread_txt': {'path': 'old.txt'}}, 'direct_machine_proofread': True}
        path = self.repo / 'records' / (self.stem + '.json')
        path.parent.mkdir(); path.write_text(json.dumps(record))
        self.snapshot('[wait]\n[message name=a text=keep]\n[message name=a text=new]\n')
        receipt, writes, deletes = self.plan()
        self.assertEqual(receipt['source_changed'], [self.stem])
        m.apply(self.repo, writes, deletes)
        self.assertFalse(formal.exists())
        rows = m.read_csv(self.repo / 'story/drafts/proofread' / self.rel)
        self.assertEqual([r['trans'] for r in rows[:-2]], ['保留', ''])
        migrated = json.loads(path.read_text())
        self.assertEqual(migrated['proofread']['revision'], 5)
        self.assertEqual(migrated['proofread']['state'], '进行中')
        self.assertEqual(migrated['proofread']['operator_github'], 'editor')
        self.assertEqual(migrated['artifacts']['proofread_draft']['based_on_revision'], 5)
        self.assertNotIn('proofread_csv', migrated['artifacts'])
        self.assertNotIn('proofread_txt', migrated['artifacts'])
        self.assertNotIn('direct_machine_proofread', migrated)
        self.assertTrue(list((self.repo / 'archive/backups' / old[-2]['text']).rglob('*.csv')))
        self.assertEqual(self.plan()[1:], ({}, set()))

    def test_duplicates_speaker_and_type_do_not_carry(self):
        old = self.snapshot('[message name=a text=same]\n[message name=a text=dup]\n[message name=a text=dup]\n[narration text=kind]\n')
        for r in old[:-2]: r['trans'] = '译文'
        self.layer('ai', old)
        self.snapshot('[message name=b text=same]\n[message name=a text=dup]\n[message name=a text=dup]\n[choice text=kind]\n')
        _, writes, _ = self.plan()
        rows = list(__import__('csv').DictReader(__import__('io').StringIO(writes['story/ai/'+self.rel].decode())))
        self.assertTrue(all(not r['trans'] for r in rows[:-2]))

    def test_legacy_same_hash(self):
        rows = self.snapshot('[narration text=test]\n')
        rows[0]['id'], rows[0]['trans'] = '1:text:1', '保留'
        self.layer('ai', rows)
        receipt, writes, deletes = self.plan()
        self.assertFalse(receipt['task_candidates'])
        m.apply(self.repo, writes, deletes)
        self.assertEqual(m.read_csv(self.repo/'story/ai'/self.rel)[0]['trans'], '保留')

    def test_classification_move(self):
        rows = self.snapshot('[title title=test]\n')
        rows[0]['trans'] = '标题'
        old = self.layer('human', rows, 'old/'+self.stem+'.csv')
        receipt, writes, deletes = self.plan()
        self.assertEqual(receipt['scripts'][0]['status'], 'path_changed')
        self.assertFalse(receipt['task_candidates'])
        m.apply(self.repo, writes, deletes)
        self.assertFalse(old.exists())
        self.assertEqual(m.read_csv(self.repo/'story/human'/self.rel)[0]['trans'], '标题')

    def test_bad_snapshot_no_mutation(self):
        self.snapshot('[narration text=test]\n')
        (self.source/(self.stem+'.txt')).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'snapshot mismatch'): self.plan()
        self.assertEqual(list(self.repo.iterdir()), [])

    def test_retired_preserved(self):
        rows = self.snapshot('[title title=test]\n')
        other = [dict(r) for r in rows]
        other[-2]['name'] = 'adv_retired.txt'
        old = self.layer('ai', other, 'adv_retired.csv')
        receipt, writes, deletes = self.plan()
        self.assertTrue(any(r['status']=='retired' for r in receipt['scripts']))
        m.apply(self.repo, writes, deletes)
        self.assertTrue(old.exists())

    def test_conflicting_draft_cleared_and_archived(self):
        old = self.snapshot('[title title=test]\n')
        old[0]['trans'] = '正式'
        self.layer('human', old)
        draft = [dict(r) for r in old]; draft[0]['trans'] = '草稿'
        self.layer('drafts/translation', draft)
        self.snapshot('[wait]\n[title title=test]\n')
        _, writes, deletes = self.plan(); m.apply(self.repo, writes, deletes)
        self.assertEqual(m.read_csv(self.repo/'story/drafts/translation'/self.rel)[0]['trans'], '')

    def test_same_hash_malformed_body_rejected(self):
        rows = self.snapshot('[title title=test]\n')
        rows[0]['text'] = 'tampered'
        self.layer('ai', rows)
        with self.assertRaisesRegex(ValueError, 'declared source hash'): self.plan()

    def test_source_translation_never_imported(self):
        old = self.snapshot('[title title=old]\n')
        self.layer('ai', old)
        current = self.snapshot('[title title=new]\n')
        current[0]['trans'] = 'untrusted'
        self.put(self.csv/self.rel, current)
        with self.assertRaisesRegex(ValueError, 'no translations'): self.plan()

    def test_incomplete_snapshot_rejected_empty_allowed(self):
        self.snapshot('[title title=test]\n')
        missing = self.source/'adv_missing.txt'
        missing.write_text('[wait]\n')
        self.plan()
        missing.write_text('[message name=a text=missing]\n')
        with self.assertRaisesRegex(ValueError, 'Incomplete CSV snapshot'): self.plan()

    def test_crlf_unicode_and_nested_escaped_values(self):
        rows = self.snapshot('[message name=あ text=同じ同じ[ruby text=漢字]続き\\]終わり]\r\n[choicegroup text=選ぶ]\r\n')
        self.assertEqual(rows[0]['text'], '同じ同じ[ruby text=漢字]続き\\]終わり')
        self.assertEqual(rows[-3]['id'], '2:choice:1')
        self.plan()

    def test_symlink_rejected(self):
        self.snapshot('[title title=test]\n')
        (self.repo/'story').symlink_to(self.csv, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'): self.plan()

    def test_apply_rolls_back(self):
        target = self.repo/'file'; target.write_bytes(b'old')
        original = m.atomic_write
        def fail(path, data):
            if path.name == 'z': raise OSError('failed')
            original(path, data)
        with patch.object(m, 'atomic_write', side_effect=fail):
            with self.assertRaises(OSError): m.apply(self.repo, {'file': b'new', 'z': b'bad'}, set())
        self.assertEqual(target.read_bytes(), b'old')


if __name__ == '__main__': unittest.main()
