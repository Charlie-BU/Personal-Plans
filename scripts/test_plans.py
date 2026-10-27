"""Synthetic, in-memory contracts. No live repository/calendar/messenger calls."""
import base64
import copy
import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import plans
from model import PlanError, NeedFiles, encode, digest, timing
from recurrence import expand

STAMP='2026-10-18T12:00:00+08:00'

class MemoryRemote:
    def __init__(self):
        self.files={'README.md':b'existing shared repository','atlas/vault.json':b'private atlas','health/a.json':b'health owned'}
        self.commit='a'*40;self.counter=0

    def envelope(self,files=None,complete=True):
        chosen=self.files if files is None else files
        return {'snapshot':self.commit,'complete':complete,'files_base64':{p:base64.b64encode(v).decode() for p,v in chosen.items()}}

    def store(self):return plans.Store(self.envelope())

    def accept(self,result):
        assert result['status']=='prepared' and result['remote_confirmed'] is False
        assert result['base_commit']==self.commit
        for p in result['delete_paths']:
            assert p.startswith('plans/');self.files.pop(p)
        for p,v in result['files_base64'].items():
            assert p.startswith('plans/');self.files[p]=base64.b64decode(v)
        self.commit=digest(encode({p:digest(v) for p,v in sorted(self.files.items())}))[:40]

    def initialize(self):
        self.accept(plans.initialize(self.store(),{'vault_id':'vault_demo','timezone':'Asia/Shanghai','calendar':{'mode':'pending'},'recorded_at':STAMP}))

    def apply(self,changes,accept=True,operation_id=None):
        self.counter+=1
        request={'operation_id':operation_id or 'op_'+str(self.counter),'base_commit':self.commit,'actor':'synthetic-user','recorded_at':STAMP,'changes':changes}
        result=plans.apply(self.store(),request)
        if accept and result['status']=='prepared':self.accept(result)
        return request,result

    def configure(self,kind,value):
        self.counter+=1
        request={'operation_id':'op_cfg_'+str(self.counter),'base_commit':self.commit,'actor':'synthetic-user','recorded_at':STAMP,'kind':kind,'value':value}
        result=plans.configure(self.store(),request);self.accept(result)


def task(rid='task_a',**overrides):
    r={'id':rid,'kind':'task','title':'生态设计：整理花园方案','summary':'为长期学习目标准备可验证的小原型。',
       'body':'预算一千元，向同伴说明设计理由。','timezone':'Asia/Shanghai',
       'priority':{'quadrant':'important_not_urgent','source':'inferred','reason':'关联长期目标；没有明确紧急风险。'},
       'schedule':{'start':'2026-10-18T09:00:00+08:00','end':'2026-10-18T10:00:00+08:00'},'due_at':'2026-10-18T11:00:00+08:00'}
    r.update(overrides);return r


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.remote=MemoryRemote();self.remote.initialize()

    def create(self,*records):return self.remote.apply([{'action':'create','record':r} for r in records])
    def read(self,rid='task_a'):return plans.read(self.remote.store(),rid)
    def change(self,action,rid='task_a',**fields):
        return {'action':action,'id':rid,'expected_sha256':self.read(rid)['sha256'],**fields}

    def test_shared_namespace_init_no_clobber(self):
        self.assertEqual(self.remote.files['health/a.json'],b'health owned')
        self.assertEqual(self.remote.files['README.md'],b'existing shared repository')
        self.assertEqual(plans.initialize(self.remote.store(),{} )['status'],'already_initialized')
        self.assertEqual(self.remote.store().checked()[1],{})
        blocked=MemoryRemote();blocked.files['plans/tasks/unknown.json']=b'{}'
        with self.assertRaises(NeedFiles):blocked.initialize()

    def test_partial_read_requests_only_relevant_index_and_record(self):
        self.create(task(),task('other',title='买一袋咖啡',summary='日常采购',body='和设计没有关系'))
        available={};q={'text':'生态设计'};rounds=0;reads=[]
        while True:
            store=plans.Store(self.remote.envelope(available,False))
            try:result=plans.query(store,q);break
            except NeedFiles as e:
                rounds+=1;reads+=e.paths
                for path in e.paths:available[path]=self.remote.files[path]
        self.assertLessEqual(rounds,5)
        self.assertEqual([v['id'] for v in result['items']],['task_a'])
        self.assertNotIn('plans/tasks/other.json',reads)
        self.assertFalse(any(p.startswith('atlas/') for p in reads))
        self.assertFalse(any('manifests/' in p for p in reads))
        self.assertEqual(result['verification'],'indexed_candidates')

    def test_lexical_aliases_and_phrase_verification(self):
        self.create(task(aliases=['花园小实验']),task('disconnected',title='生态',summary='设计',body='两个字段不是连续短语'))
        result=plans.query(self.remote.store(),{'text':'生态设计'})
        self.assertEqual([v['id'] for v in result['items']],['task_a'])
        self.assertEqual(plans.query(self.remote.store(),{'text':['园艺方案','花园小实验']})['total'],1)
        self.assertEqual(plans.query(self.remote.store(),{'text':'不存在内容'})['total'],0)
        self.assertEqual(plans.query(self.remote.store(),{'text':'设计','quadrant':'important_urgent'})['total'],0)

    def test_hash_verification_and_stale_index(self):
        self.create(task());path='plans/tasks/task_a.json'
        r=json.loads(self.remote.files[path]);r['body']='changed outside atomic commit';self.remote.files[path]=encode(r)
        with self.assertRaisesRegex(PlanError,'indexed hash'):self.read()
        with self.assertRaisesRegex(PlanError,'Incomplete or modified'):self.remote.store().checked()

    def test_stale_commit_idempotency_and_hash_conflict(self):
        request,result=self.create(task())
        self.assertEqual(plans.apply(self.remote.store(),request)['status'],'already_applied')
        changed=copy.deepcopy(request);changed['actor']='different'
        with self.assertRaisesRegex(PlanError,'reused'):plans.apply(self.remote.store(),changed)
        change=self.change('note',text='记录一条备注。');change['expected_sha256']='wrong'
        with self.assertRaisesRegex(PlanError,'hash conflict'):self.remote.apply([change])
        stale={'operation_id':'op_stale','base_commit':'a'*40,'actor':'user','changes':[self.change('note',text='stale')]}
        with self.assertRaisesRegex(PlanError,'Stale'):plans.apply(self.remote.store(),stale)

    def test_pagination_and_hierarchy_without_global_graph(self):
        self.create(task('goal',kind='goal',schedule=None,due_at=None),*[task('task_'+str(i),parent_id='goal') for i in range(70)])
        first=plans.query(self.remote.store(),{'parent_id':'goal','limit':20})
        second=plans.query(self.remote.store(),{'parent_id':'goal','limit':100,'offset':first['next_offset']})
        self.assertEqual(first['total'],70);self.assertEqual(len(first['items'])+len(second['items']),70)
        self.assertIsNone(second['next_offset'])
        child=self.read('task_0')['record']
        self.create(task('goal_two',kind='goal',parent_id='goal',schedule=None,due_at=None))
        with self.assertRaisesRegex(PlanError,'Cycle'):
            self.remote.apply([self.change('update','goal',fields={'parent_id':'goal_two'})])
        self.assertEqual(self.read('task_0')['record'],child)

    def test_dependencies_and_deletion_guards(self):
        self.create(task('a'),task('b',depends_on=['a']))
        self.assertEqual(plans.query(self.remote.store(),{'dependent_of':'a'})['total'],1)
        with self.assertRaisesRegex(PlanError,'Cycle'):
            self.remote.apply([self.change('update','a',fields={'depends_on':['b']})])
        with self.assertRaisesRegex(PlanError,'Missing'):
            self.remote.apply([self.change('delete','a',reason='remove')])
        self.remote.apply([self.change('delete','a',reason='remove'),self.change('delete','b',reason='remove')])
        with self.assertRaisesRegex(PlanError,'Deleted IDs'):self.create(task('a'))

    def test_priority_required_and_direct_deadline_edit_rejected(self):
        r=task();r.pop('priority')
        with self.assertRaisesRegex(PlanError,'Priority'):self.create(r)
        self.create(task())
        with self.assertRaisesRegex(PlanError,'protected'):
            self.remote.apply([self.change('update',fields={'due_at':'2026-10-30'})])

    def test_reschedule_preserves_original_and_audit(self):
        self.create(task())
        self.remote.apply([self.change('reschedule',fields={'due_at':'2026-10-20'},reason='等待同伴反馈')])
        r=self.read()['record'];self.assertEqual(r['original_due_at'],'2026-10-18T11:00:00+08:00')
        report=plans.report(self.remote.store(),'2026-10-18','2026-10-18',STAMP)
        self.assertEqual(report['original_due_count'],1);self.assertEqual(report['due_count'],0)
        self.assertEqual(timing(r,STAMP),'not_due');self.assertEqual(timing(r,STAMP,True),'overdue_unconfirmed')
        events=[json.loads(v) for p,v in self.remote.files.items() if p.startswith('plans/activities/')]
        self.assertTrue(any(e['reason']=='等待同伴反馈' and e['before']['due_at']!=e['after']['due_at'] for e in events))

    def test_complete_precision_and_no_automatic_success(self):
        self.create(task())
        a=plans.agenda(self.remote.store(),'2026-10-18','2026-10-18',STAMP)
        self.assertEqual(a['items'][0]['status'],'pending');self.assertEqual(a['items'][0]['timing'],'overdue_unconfirmed')
        self.remote.apply([self.change('complete',completed_at='2026-10-18',completion_precision='date')])
        self.assertEqual(timing(self.read()['record'],STAMP),'unknown_precision')
        with self.assertRaisesRegex(PlanError,'future'):
            self.create(task('future'));self.remote.apply([self.change('complete','future',completed_at='2026-10-19T10:00:00+08:00',completion_precision='instant')])

    def series(self,**kw):
        return task('series',recurrence={'dtstart':'2026-10-18T09:00:00+08:00','rrule':'FREQ=DAILY;INTERVAL=2'},**kw)

    def test_repeat_every_two_days_and_independent_instances(self):
        self.create(self.series())
        a=plans.agenda(self.remote.store(),'2026-10-18','2026-10-22',STAMP)
        self.assertEqual(len(a['items']),3)
        ids=[r['id'] for r in a['items']];self.assertEqual(len(set(ids)),3)
        occurrence=copy.deepcopy(a['items'][0])
        for k in ['virtual','timing','original_timing','materialized','path']:occurrence.pop(k,None)
        self.remote.apply([{'action':'materialize','record':occurrence,'expected_series_sha256':self.read('series')['sha256']}])
        self.remote.apply([self.change('complete',ids[0],completed_at='2026-10-18T10:30:00+08:00',completion_precision='instant')])
        a=plans.agenda(self.remote.store(),'2026-10-18','2026-10-22',STAMP)
        self.assertEqual([r['status'] for r in a['items']],['completed','pending','pending'])
        with self.assertRaisesRegex(PlanError,'occurrence'):
            self.remote.apply([self.change('complete','series',completed_at=STAMP,completion_precision='instant')])

    def test_moved_out_occurrence_does_not_reappear(self):
        self.create(self.series())
        occurrence=plans.agenda(self.remote.store(),'2026-10-18','2026-10-18',STAMP)['items'][0]
        raw={k:v for k,v in occurrence.items() if k not in {'virtual','timing','original_timing','materialized','path'}}
        self.remote.apply([{'action':'materialize','record':raw,'expected_series_sha256':self.read('series')['sha256']}])
        self.remote.apply([self.change('reschedule',raw['id'],fields={'schedule':{'start':'2026-10-19T09:00:00+08:00','end':'2026-10-19T10:00:00+08:00'},'due_at':'2026-10-19T11:00:00+08:00'},reason='本次顺延')])
        self.assertEqual(plans.agenda(self.remote.store(),'2026-10-18','2026-10-18',STAMP)['total'],0)
        moved=plans.agenda(self.remote.store(),'2026-10-19','2026-10-19',STAMP)['items']
        self.assertEqual([r['id'] for r in moved],[raw['id']])

    def test_monthly_31st_exceptions_and_date_only_until(self):
        r=plans.defaults(task(schedule={'start':'2026-01-31'},due_at='2026-01-31',recurrence={'dtstart':'2026-01-31','rrule':'FREQ=MONTHLY;COUNT=3','rdates':['2026-02-28'],'exdates':['2026-03-31']}),STAMP)
        slots=expand(r,'2026-01-01','2026-05-31')
        self.assertEqual([v['recurrence_id'] for v in slots],['2026-01-31','2026-02-28','2026-05-31'])
        r['recurrence']={'dtstart':'2026-01-31','rrule':'FREQ=DAILY;UNTIL=20260202'}
        self.assertEqual(len(expand(r,'2026-01-31','2026-02-28')),3)

    def test_holiday_data_required_not_weekday_guess(self):
        r=self.series();r['recurrence']={'dtstart':r['schedule']['start'],'rrule':'FREQ=DAILY','day_filter':{'calendar_id':'cn_demo','kind':'workday'}}
        self.create(r)
        with self.assertRaises(NeedFiles) as cm:plans.agenda(self.remote.store(),'2026-10-18','2026-10-18',STAMP)
        self.assertEqual(cm.exception.paths,['plans/calendars/holidays/cn_demo/2026.json'])
        self.remote.configure('holiday',{'schema_version':1,'calendar_id':'cn_demo','year':2026,'version':'synthetic-v1','source_url':'https://example.org/fictional-calendar','verified_at':STAMP,'days':{'2026-10-18':{'workday':True,'statutory_holiday':False}}})
        self.assertEqual(plans.agenda(self.remote.store(),'2026-10-18','2026-10-18',STAMP)['total'],1)
        with self.assertRaisesRegex(PlanError,'does not cover'):plans.agenda(self.remote.store(),'2026-10-19','2026-10-19',STAMP)

    def test_dst_nonexistent_slot_explicit_failure(self):
        r=plans.defaults(task(timezone='America/New_York',schedule={'start':'2026-03-07T02:30:00-05:00'},due_at=None,recurrence={'dtstart':'2026-03-07T02:30:00-05:00','rrule':'FREQ=DAILY;COUNT=3'}),STAMP)
        with self.assertRaisesRegex(PlanError,'DST'):expand(r,'2026-03-07','2026-03-09')

    def test_calendar_intent_receipt_not_remote_success(self):
        self.create(task());request={'operation_id':'op_calendar','base_commit':self.remote.commit,'kind':'calendar','record_id':'task_a','action':'upsert','payload':{},'recorded_at':STAMP}
        with self.assertRaisesRegex(PlanError,'verified'):plans.integration(self.remote.store(),request)
        self.remote.configure('calendar',{'mode':'connected','provider':'synthetic','calendar_id':'synthetic-calendar','verified_at':STAMP})
        request['base_commit']=self.remote.commit;prepared=plans.integration(self.remote.store(),request);self.remote.accept(prepared)
        request['base_commit']=self.remote.commit
        self.assertEqual(plans.integration(self.remote.store(),request)['status'],'already_queued')
        effect=plans.effects(self.remote.store(),'2026-10-18','2026-10-18',['queued'])['items'][0]
        self.remote.apply([self.change('note',text='同步过程补充备注')])
        receipt={**request,'base_commit':self.remote.commit,'intent_id':effect['id'],'receipt':{'state':'confirmed','external_id':'fake-event','verified_at':STAMP}}
        result=plans.integration(self.remote.store(),receipt);self.assertFalse(result['remote_confirmed']);self.remote.accept(result)
        self.assertEqual(plans.effects(self.remote.store(),'2026-10-18','2026-10-18',['confirmed'])['items'][0]['receipt']['external_id'],'fake-event')

    def test_notification_opt_in_dedup_and_cancellation(self):
        r=task(reminders=[{'id':'before_start','anchor':'start','offset_minutes':-10,'channel':'app'}]);self.create(r)
        request={'operation_id':'op_notice','base_commit':self.remote.commit,'kind':'notification','record_id':'task_a','action':'send','payload':{'trigger_at':'2026-10-18T08:50:00+08:00','rule_id':'before_start','channel':'app'},'recorded_at':STAMP}
        with self.assertRaisesRegex(PlanError,'opt-in'):plans.integration(self.remote.store(),request)
        self.remote.configure('reminders',{'schema_version':1,'enabled':True,'consent_reference':'synthetic-user-message','timezone':'Asia/Shanghai','quiet_start':'22:00','quiet_end':'08:00','max_per_day':5})
        request['base_commit']=self.remote.commit;self.remote.accept(plans.integration(self.remote.store(),request))
        self.remote.apply([self.change('note',text='不应导致重复通知')]);request['base_commit']=self.remote.commit
        self.assertEqual(plans.integration(self.remote.store(),request)['status'],'already_queued')
        self.remote.apply([self.change('complete',completed_at='2026-10-18T10:00:00+08:00',completion_precision='instant')])
        self.assertEqual(len(plans.effects(self.remote.store(),'2026-10-18','2026-10-18',['cancelled'])['items']),1)

    def test_report_leaf_denominator_and_uncertainty(self):
        self.create(task('parent'),task('child',parent_id='parent'),task('cancelled'),task('not_due',due_at='2026-10-25'))
        self.remote.apply([self.change('complete','child',completed_at='2026-10-18',completion_precision='date'),self.change('state','cancelled',status='cancelled',reason='不再需要')])
        report=plans.report(self.remote.store(),'2026-10-18','2026-10-18',STAMP)
        self.assertEqual(report['due_count'],1);self.assertEqual(report['completed_count'],1)
        self.assertEqual(report['unknown_completion_precision'],1);self.assertEqual(report['excluded_count'],1)

    def test_ics_uid_utf8_folding_and_no_sync_claim(self):
        self.create(task(title='虚构日程与原始记录'*18))
        result=plans.ics(self.remote.store(),'2026-10-18','2026-10-18',STAMP)
        self.assertFalse(result['calendar_synced']);self.assertIn('UID:task_a.vault_demo@personal-plans',result['content'])
        self.assertTrue(all(len(line.encode())<=75 for line in result['content'].split('\r\n')))
        self.assertIn('DTSTART:20261018T010000Z',result['content'])

    def test_cli_missing_files_machine_readable(self):
        script=Path(__file__).with_name('plans.py')
        result=subprocess.run([sys.executable,'-B',str(script),'--stdin','query'],input=json.dumps({'snapshot':'a'*40,'files_base64':{},'query':{'id':'task_a'}}),text=True,capture_output=True)
        self.assertEqual(result.returncode,0)
        self.assertEqual(json.loads(result.stdout)['status'],'needs_files')

    def test_cross_timezone_and_deadline_offsets(self):
        self.create(task('ny',timezone='America/New_York',schedule={'start':'2026-10-17T23:30:00-04:00','end':'2026-10-18T00:30:00-04:00'},due_at=None))
        self.assertEqual([r['id'] for r in plans.agenda(self.remote.store(),'2026-10-18','2026-10-18',STAMP)['items']],['ny'])
        r=task('offset',schedule={'start':'2026-10-16T09:00:00+08:00','end':'2026-10-16T10:00:00+08:00'},due_at='2026-10-18T11:00:00+08:00',recurrence={'dtstart':'2026-10-16T09:00:00+08:00','rrule':'FREQ=DAILY'})
        self.create(r)
        rows=plans.agenda(self.remote.store(),'2026-10-18','2026-10-18',STAMP)['items']
        self.assertTrue(any(x.get('task_id')=='offset' and x['recurrence_id'].startswith('2026-10-16') for x in rows))

    def test_original_month_override_for_overnight_instance(self):
        r=task('night',schedule={'start':'2026-09-30T23:00:00+08:00','end':'2026-10-01T01:00:00+08:00'},due_at=None,recurrence={'dtstart':'2026-09-30T23:00:00+08:00','rrule':'FREQ=DAILY'})
        self.create(r)
        row=next(x for x in plans.agenda(self.remote.store(),'2026-10-01','2026-10-01',STAMP)['items'] if x['recurrence_id'].startswith('2026-09-30'))
        raw={k:v for k,v in row.items() if k not in {'virtual','timing','original_timing','materialized','path'}}
        self.remote.apply([{'action':'materialize','record':raw,'expected_series_sha256':self.read('night')['sha256']}])
        self.remote.apply([self.change('state',raw['id'],status='cancelled',reason='本次取消')])
        rows=plans.agenda(self.remote.store(),'2026-10-01','2026-10-01',STAMP)['items']
        self.assertEqual(next(x for x in rows if x['id']==raw['id'])['status'],'cancelled')

    def test_series_split_preserves_past_cancels_open_future(self):
        self.create(self.series())
        row=plans.agenda(self.remote.store(),'2026-10-20','2026-10-20',STAMP)['items'][0]
        raw={k:v for k,v in row.items() if k not in {'virtual','timing','original_timing','materialized','path'}}
        self.remote.apply([{'action':'materialize','record':raw,'expected_series_sha256':self.read('series')['sha256']}])
        request={'operation_id':'op_split','base_commit':self.remote.commit,'actor':'synthetic-user','recorded_at':STAMP,'series_id':'series','expected_sha256':self.read('series')['sha256'],'cutover':'2026-10-20','future_policy':'cancel_open','reason':'从此改为每周练习','new_record':{'id':'series_new','schedule':{'start':'2026-10-20T19:00:00+08:00','end':'2026-10-20T20:00:00+08:00'},'recurrence':{'dtstart':'2026-10-20T19:00:00+08:00','rrule':'FREQ=WEEKLY'}}}
        from unittest.mock import patch
        with patch.object(plans,'prepare',wraps=plans.prepare) as prepare:
            self.remote.accept(plans.split_series(self.remote.store(),request))
        self.assertEqual(prepare.call_count,1)
        self.assertEqual(plans.split_series(self.remote.store(),request)['status'],'already_applied')
        past=plans.agenda(self.remote.store(),'2026-10-18','2026-10-18',STAMP)['items']
        self.assertEqual([x['task_id'] for x in past],['series'])
        future=plans.agenda(self.remote.store(),'2026-10-20','2026-10-20',STAMP)['items']
        self.assertEqual({(x['task_id'],x['status']) for x in future},{('series','cancelled'),('series_new','pending')})

    def test_saved_report_keeps_evidence_snapshot_and_paginated_routes(self):
        self.create(task())
        source=self.remote.commit
        request={'operation_id':'op_report','base_commit':source,'report_id':'report_week','actor':'synthetic-user','recorded_at':STAMP,'from':'2026-10-18','to':'2026-10-18','as_of':STAMP,'narrative':'合成演示，仅陈述已核验的计划。'}
        self.remote.accept(plans.save_report(self.remote.store(),request))
        self.assertEqual(plans.save_report(self.remote.store(),request)['status'],'already_applied')
        cards=plans.saved_reports(self.remote.store(),'2026-10-18','2026-10-18')['items']
        self.assertEqual(len(cards),1)
        saved=json.loads(self.remote.files[cards[0]['path']])
        self.assertEqual(saved['source_snapshot'],source);self.assertEqual(saved['metrics']['due_count'],1)
        self.assertEqual(digest(self.remote.files[cards[0]['path']]),cards[0]['sha256'])

    def test_search_budget_does_not_silently_truncate(self):
        self.create(task('a'),task('b'))
        result=plans.query(self.remote.store(),{'text':'生态设计','max_candidates':1})
        self.assertEqual(result['status'],'refine_query');self.assertEqual(result['candidate_count'],2)
        self.assertFalse(result['complete'])

    def test_no_historical_state_claim_for_current_snapshot(self):
        self.create(task())
        with self.assertRaisesRegex(PlanError,'historical'):
            plans.report(self.remote.store(),'2026-10-18','2026-10-18','2026-10-18T09:00:00+08:00')

    def test_missing_file_requests_cannot_escape_namespace(self):
        with self.assertRaisesRegex(PlanError,'outside plans'):
            self.remote.store().need(['health/private.json'])

    def test_unsafe_paths_rejected_foreign_credentials_not_decoded(self):
        with self.assertRaises(PlanError):plans.Store({'snapshot':None,'files_base64':{'plans/../health/x':'e30='}})
        with self.assertRaises(PlanError):plans.Store({'snapshot':None,'files_base64':{'plans':'e30='}})
        store=plans.Store({'snapshot':'a'*40,'files_base64':{'health/private.json':'not-base64-credential'}})
        self.assertEqual(store.files,{})

    def test_cancelled_effect_survives_record_deletion(self):
        self.create(task())
        self.remote.configure('calendar',{'mode':'connected','provider':'synthetic','calendar_id':'test','verified_at':STAMP})
        request={'operation_id':'op_deleted_effect','base_commit':self.remote.commit,'kind':'calendar','record_id':'task_a','action':'upsert','payload':{},'recorded_at':STAMP}
        self.remote.accept(plans.integration(self.remote.store(),request))
        self.remote.apply([self.change('delete',reason='remove task')])
        result=plans.effects(self.remote.store(),'2026-10-18','2026-10-18',['cancelled'])
        self.assertEqual(len(result['items']),1)
        self.assertEqual(result['items'][0]['state'],'cancelled')
        self.assertFalse(result['effect_executed'])

    def test_ics_normalizes_all_newline_forms(self):
        self.create(task(title='hello\rDESCRIPTION:unexpected\r\nnext\nlast'))
        content=plans.ics(self.remote.store(),'2026-10-18','2026-10-18',STAMP)['content']
        self.assertNotIn('\r',content.replace('\r\n',''))
        self.assertIn('SUMMARY:hello\\nDESCRIPTION:unexpected\\nnext\\nlast',content)

    def test_checked_parses_records_once_and_rejects_corrupt_index(self):
        from unittest.mock import patch
        self.create(task())
        with patch.object(plans,'records_from',wraps=plans.records_from) as parse:
            self.remote.store().checked()
        self.assertEqual(parse.call_count,1)
        path=plans.record_index_path('task_a')
        self.remote.files[path]=b'[]'
        with self.assertRaisesRegex(PlanError,'Indexes'):
            self.remote.store().checked()

    def test_id_combined_filters_do_not_fetch_wide_postings(self):
        self.create(task(),task('other'))
        available={};fetched=[]
        query={'id':'task_a','status':'pending','kind':'task','text':'生态设计','on':'2026-10-18','original_on':'2026-10-18'}
        while True:
            try:
                result=plans.query(plans.Store(self.remote.envelope(available,False)),query)
                break
            except NeedFiles as missing:
                fetched.extend(missing.paths)
                available.update({p:self.remote.files[p] for p in missing.paths})
        self.assertEqual([c['id'] for c in result['items']],['task_a'])
        self.assertEqual([p for p in fetched if '/indexes/records/' in p],[plans.record_index_path('task_a')])
        self.assertNotIn('plans/tasks/other.json',fetched)
        for filters in [{'status':'completed'},{'on':'2026-10-19'},{'text':'不存在'},{'original_on':'2026-10-20'},{'parent_id':'missing'}]:
            self.assertEqual(plans.query(self.remote.store(),{'id':'task_a',**filters})['total'],0)

    def test_schema_shared_dates_resolve_without_changing_constraints(self):
        schema=json.loads((Path(__file__).parents[1]/'references/record.schema.json').read_text())
        def resolve(value):
            if isinstance(value,dict):
                if '$ref' in value:
                    self.assertTrue(value['$ref'].startswith('#/$defs/'))
                    return resolve(schema['$defs'][value['$ref'].split('/')[-1]])
                return {k:resolve(v) for k,v in value.items()}
            if isinstance(value,list):
                return [resolve(v) for v in value]
            return value
        expanded=resolve(schema)
        date={'type':'string','format':'date','pattern':r'^\d{4}-\d{2}-\d{2}$'}
        timestamp={'type':'string','format':'date-time'}
        temporal={'anyOf':[date,timestamp]}
        for field in ['due_at','original_due_at','completed_at','recurrence_id']:
            self.assertEqual(expanded['properties'][field],{'anyOf':[temporal,{'type':'null'}]})
        self.assertEqual(expanded['properties']['created_at'],timestamp)

if __name__=='__main__':unittest.main()
