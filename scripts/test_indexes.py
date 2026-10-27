"""Compact indexes store ID lists and one card per record, with explicit rebuilds."""
import copy
import json
import unittest
import plans
from model import PlanError, NeedFiles, encode
from test_plans import MemoryRemote, task, STAMP


class CompactIndexTests(unittest.TestCase):
    def setUp(self):
        self.remote=MemoryRemote();self.remote.initialize()
        self.remote.apply([{'action':'create','record':task('task_'+str(i),title='生态设计项目'+str(i))} for i in range(20)])

    def test_rebuild_preserves_authority_and_results(self):
        authority={p:v for p,v in self.remote.store().files.items() if not p.startswith(plans.GENERATED)}
        queries=[{'text':'生态设计'},{'status':'pending','limit':3},{'id':'task_1','on':'2026-10-18'},{'text':'不存在'}]
        expected=[plans.query(self.remote.store(),q) for q in queries]
        self.remote.files['plans/indexes/postings/obsolete/0.json']=b'obsolete derived bytes'
        result=plans.run(self.remote.envelope(),'reindex',None)
        self.assertEqual(result['delete_paths'],['plans/indexes/postings/obsolete/0.json'])
        self.remote.accept(result)
        self.assertTrue(all(self.remote.files[p]==raw for p,raw in authority.items()))
        self.assertEqual(self.remote.files['health/a.json'],b'health owned')
        self.remote.store().checked()
        for q,before in zip(queries,expected):
            after=plans.query(self.remote.store(),q)
            self.assertEqual(after['items'],before['items']);self.assertEqual(after['total'],before['total'])
        again=plans.run(self.remote.envelope(),'reindex',None)
        self.assertEqual(again['delete_paths'],[]);self.assertEqual(again['files_base64'],{})

    def test_unknown_index_format_requires_rebuild(self):
        root=json.loads(self.remote.files[plans.ROOT]);root['index_format']=999
        self.remote.files[plans.ROOT]=encode(root)
        with self.assertRaisesRegex(PlanError,'reindex'):plans.read(self.remote.store(),'task_1')
        self.remote.accept(plans.run(self.remote.envelope(),'reindex',None))
        self.assertEqual(plans.read(self.remote.store(),'task_1')['record']['id'],'task_1')

    def test_modified_or_partial_authority_cannot_rebuild(self):
        with self.assertRaisesRegex(PlanError,'complete'):
            plans.run(self.remote.envelope(complete=False),'reindex',None)
        self.remote.files['plans/tasks/task_1.json']=b'{}'
        with self.assertRaisesRegex(PlanError,'authoritative'):
            plans.run(self.remote.envelope(),'reindex',None)

    def test_compact_index_has_bounded_file_count_and_single_cards(self):
        generated={p:v for p,v in self.remote.files.items() if p.startswith(plans.GENERATED)}
        self.assertLessEqual(len(generated),34)
        copies=sum(len(json.loads(raw)) for path,raw in generated.items() if '/indexes/records/' in path)
        self.assertEqual(copies,20)
        self.assertFalse(any('/postings/' in p for p in generated))
        catalogs=[json.loads(raw) for path,raw in generated.items() if '/catalog/' in path]
        self.assertTrue(all(isinstance(rid,str) for shard in catalogs for ids in shard.values() for rid in ids))

    def test_missing_card_requests_exact_shard_and_rejects_bad_ids(self):
        path=plans.record_index_path('task_1')
        available={p:v for p,v in self.remote.files.items() if p != path}
        with self.assertRaises(NeedFiles) as error:
            plans.read(plans.Store(self.remote.envelope(available,False)),'task_1')
        self.assertEqual(error.exception.paths,[path])
        catalog=plans.catalog_path('id:task_1')
        value=json.loads(self.remote.files[catalog]);value['id:task_1']=['../unsafe']
        self.remote.files[catalog]=encode(value)
        with self.assertRaisesRegex(PlanError,'stable ID'):
            plans.read(self.remote.store(),'task_1')

    def test_rebuild_from_authority_and_complete_index_inventory(self):
        all_files = self.remote.files
        indexes = [p for p in all_files if p.startswith(plans.GENERATED)]
        retained = {p:v for p,v in all_files.items() if not p.startswith(plans.GENERATED) or p in {plans.ROOT, plans.MANIFEST}}
        envelope = self.remote.envelope(retained)
        envelope['index_paths'] = indexes + ['plans/indexes/obsolete.json']
        result = plans.run(envelope,'reindex',None)
        self.assertEqual(result['delete_paths'], ['plans/indexes/obsolete.json'])
        envelope['index_paths'].append('plans/tasks/task_1.json')
        with self.assertRaisesRegex(PlanError,'authoritative'):
            plans.run(envelope,'reindex',None)

    def test_note_changes_only_one_card_shard_not_route_shards(self):
        before=copy.deepcopy(self.remote.files)
        read=plans.read(self.remote.store(),'task_1')
        self.remote.apply([{'action':'note','id':'task_1','expected_sha256':read['sha256'],'text':'用户补充说明'}])
        catalogs=[p for p in self.remote.files if '/indexes/catalog/' in p]
        self.assertTrue(all(before[p]==self.remote.files[p] for p in catalogs))
        changed=[p for p in self.remote.files if '/indexes/records/' in p and before.get(p)!=self.remote.files[p]]
        self.assertEqual(changed,[plans.record_index_path('task_1')])


if __name__=='__main__':unittest.main()
