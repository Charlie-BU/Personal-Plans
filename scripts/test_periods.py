"""Period feedback persists independently from generated reports."""
import copy
import unittest
import plans
from periods import path_for, read
from model import PlanError, NeedFiles
from test_plans import MemoryRemote, task, STAMP


class PeriodTests(unittest.TestCase):
    def setUp(self):
        self.remote=MemoryRemote();self.remote.initialize()
        self.scope=dict(kind='day',start='2026-10-18',end='2026-10-18',timezone='Asia/Shanghai',label='今日反馈',plan_id=None)

    def append(self,text='用户补充：今天只完成了资料整理。',basis='user_statement',completion='部分完成'):
        self.remote.configure('period-note',dict(scope=self.scope,text=text,basis=basis,completion=completion))

    def test_daily_feedback_append_and_source(self):
        source=self.remote.commit;self.append();self.append('后来补充：临时有事，剩余工作顺延。')
        result=read(self.remote.store(),self.scope)
        self.assertEqual(len(result['record']['notes']),2)
        self.assertEqual(result['record']['notes'][0]['source_snapshot'],source)
        self.assertEqual(result['record']['notes'][0]['completion'],'部分完成')
        self.assertEqual(self.remote.files['health/a.json'],b'health owned')
        self.remote.store().checked()

    def test_all_period_kinds_and_plan_specific_feedback(self):
        self.remote.apply([{'action':'create','record':task()}])
        for kind in ['week','month','quarter','year','custom']:
            self.scope.update(kind=kind,end='2027-03-31',plan_id='task_a',label='目标周期')
            self.append()
            self.assertEqual(read(self.remote.store(),self.scope)['record']['scope']['kind'],kind)
        renamed={**self.scope,'label':'重命名周期'}
        self.assertEqual(path_for(renamed),path_for(self.scope))
        self.assertNotEqual(path_for({**self.scope,'plan_id':None}),path_for(self.scope))

    def test_partial_read_requests_exact_period(self):
        self.append()
        partial={p:v for p,v in self.remote.files.items() if p in [plans.VAULT,plans.ROOT]}
        with self.assertRaises(NeedFiles) as error:
            read(plans.Store(self.remote.envelope(partial,False)),self.scope)
        self.assertEqual(error.exception.paths,[path_for(self.scope)])
        empty={**self.scope,'start':'2026-10-19','end':'2026-10-19'}
        self.assertFalse(read(self.remote.store(),empty)['exists'])

    def test_invalid_feedback_rejected_without_writes(self):
        before=copy.deepcopy(self.remote.files)
        with self.assertRaises(PlanError):self.append('  ')
        with self.assertRaises(PlanError):self.append(basis='fact')
        self.scope['end']='2026-10-17'
        with self.assertRaises(PlanError):self.append()
        self.assertEqual(self.remote.files,before)

    def test_repeat_operation_is_not_duplicate_feedback(self):
        request=dict(operation_id='period_note_once',base_commit=self.remote.commit,actor='user',recorded_at=STAMP,kind='period-note',value=dict(scope=self.scope,text='补充说明',basis='user_statement',completion=None))
        self.remote.accept(plans.configure(self.remote.store(),request))
        self.assertEqual(plans.configure(self.remote.store(),request)['status'],'already_applied')
        self.assertEqual(len(read(self.remote.store(),self.scope)['record']['notes']),1)


if __name__=='__main__':unittest.main()
