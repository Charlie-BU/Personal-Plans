"""Remote-only scheduler contracts; no live scheduler or messages."""
import copy
import json
import unittest
from test_plans import MemoryRemote, STAMP
from model import PlanError, NeedFiles
from cronjobs import JOBS, defaults, cron
import plans


class CronjobTests(unittest.TestCase):
    def setUp(self):
        self.remote = MemoryRemote(); self.remote.initialize()
        self.remote.configure('cronjob-defaults',dict(timezone='Asia/Shanghai',channel='self_app',consent_reference='onboarding accepted'))

    def job(self):
        return json.loads(self.remote.files[JOBS+'definitions/morning_plan.json'])

    def bind(self, owner='agent_a', state='active'):
        self.remote.configure('cronjob-binding',dict(job_id='morning_plan',owner=owner,provider='host',external_id='scheduler_one',state=state,verified_at=STAMP,job_revision=self.job()['revision']))

    def run_job(self, state='claimed', owner='agent_a', scheduled_at='2026-10-18T08:00:00+08:00'):
        self.remote.configure('cronjob-run',dict(job_id='morning_plan',owner=owner,scheduled_at=scheduled_at,state=state,receipt=None if state=='claimed' else dict(reference='provider/message_1',verified_at=STAMP)))

    def test_defaults_are_enabled_and_do_not_overwrite_user_choices(self):
        job=self.job(); self.assertTrue(job['enabled'])
        value={k:v for k,v in job.items() if k not in {'schema_version','revision','updated_at'}}
        value.update(enabled=False,cron='0 9 * * *')
        self.remote.configure('cronjob',value)
        self.remote.configure('cronjob-defaults',dict(timezone='Asia/Shanghai',channel='self_app',consent_reference='repeat onboarding'))
        self.assertFalse(self.job()['enabled']); self.assertEqual(self.job()['cron'],'0 9 * * *')
        self.assertEqual(self.remote.files['health/a.json'],b'health owned')

    def test_sparse_read_includes_bindings_and_no_run_history(self):
        self.bind(); self.run_job()
        available={}; fetched=[]
        while True:
            try:
                result=plans.cronjobs(plans.Store(self.remote.envelope(available,False)));break
            except NeedFiles as e:
                fetched.extend(e.paths)
                available.update({p:self.remote.files[p] for p in e.paths})
        self.assertEqual(len(result['items']),3)
        self.assertFalse(any('/runs/' in p for p in fetched))
        self.assertFalse(any(p.startswith('atlas/') for p in fetched))

    def test_takeover_requires_previous_scheduler_pause(self):
        self.bind()
        with self.assertRaisesRegex(PlanError,'Pause'):self.bind('agent_b')
        self.bind(state='paused'); self.bind('agent_b')
        with self.assertRaisesRegex(PlanError,'binding'):self.run_job(owner='agent_a')
        self.run_job(owner='agent_b')

    def test_run_replay_and_unknown_are_not_resent(self):
        self.bind();self.run_job()
        with self.assertRaisesRegex(PlanError,'already claimed'):self.run_job()
        self.run_job('unknown')
        with self.assertRaisesRegex(PlanError,'already claimed'):self.run_job()
        self.run_job('succeeded')
        with self.assertRaisesRegex(PlanError,'unresolved'):self.run_job('failed')
        runs=[json.loads(v) for p,v in self.remote.files.items() if '/cronjobs/runs/' in p]
        self.assertEqual(len(runs),1);self.assertEqual(runs[0]['state'],'succeeded')

    def test_configuration_change_blocks_old_binding(self):
        self.bind()
        value=defaults('Asia/Shanghai','self_app','accepted')[0]
        value['instructions']='updated prompt'
        self.remote.configure('cronjob',value)
        with self.assertRaisesRegex(PlanError,'binding'):self.run_job()
        self.bind();self.run_job()

    def test_invalid_schedule_and_payload_do_not_change_remote(self):
        before=copy.deepcopy(self.remote.files)
        for expression in ['60 8 * * *','0 24 * * *','0 8 * * 7','@daily','*/0 * * * *','0 8 1 * 1','0 8 * * *; echo bad']:
            with self.assertRaises(PlanError):cron(expression)
        value=defaults('Asia/Shanghai','self_app','accepted')[0];value['token']='forbidden'
        with self.assertRaises(PlanError):self.remote.configure('cronjob',value)
        self.assertEqual(self.remote.files,before)
        cron('*/15 8-18 * * 1-5')

    def test_wrong_slot_and_disabled_job_cannot_run(self):
        self.bind()
        with self.assertRaisesRegex(PlanError,'schedule'):self.run_job(scheduled_at='2026-10-18T08:01:00+08:00')
        value=defaults('Asia/Shanghai','self_app','accepted')[0];value['enabled']=False
        self.remote.configure('cronjob',value)
        with self.assertRaisesRegex(PlanError,'binding'):self.run_job()

    def test_quiet_hours_and_daily_cap(self):
        value=defaults('Asia/Shanghai','self_app','accepted')[0]
        value['cron']='0 * * * *'
        self.remote.configure('cronjob',value);self.bind();self.run_job()
        with self.assertRaisesRegex(PlanError,'daily cap'):
            self.run_job(scheduled_at='2026-10-18T09:00:00+08:00')
        value['quiet_start']='11:00';value['quiet_end']='13:00'
        self.remote.configure('cronjob',value);self.bind()
        with self.assertRaisesRegex(PlanError,'quiet'):
            self.run_job(scheduled_at='2026-10-18T10:00:00+08:00')

    def test_equivalent_offsets_share_one_claim_and_operation_retries(self):
        self.bind();self.run_job()
        with self.assertRaisesRegex(PlanError,'already claimed'):
            self.run_job(scheduled_at='2026-10-18T00:00:00Z')
        request=dict(operation_id='repeat_job',base_commit=self.remote.commit,actor='test',recorded_at=STAMP,kind='cronjob-defaults',value=dict(timezone='Asia/Shanghai',channel='self_app',consent_reference='accepted'))
        result=plans.configure(self.remote.store(),request);self.remote.accept(result)
        self.assertEqual(plans.configure(self.remote.store(),request)['status'],'already_applied')

    def test_empty_legacy_library_remains_valid(self):
        remote=MemoryRemote();remote.initialize()
        self.assertEqual(plans.cronjobs(remote.store())['items'],[])
        remote.store().checked()

    def test_stale_configuration_and_future_run_are_rejected(self):
        self.bind()
        with self.assertRaisesRegex(PlanError,'future'):self.run_job(scheduled_at='2026-10-19T08:00:00+08:00')
        request=dict(operation_id='stale_job',base_commit='f'*40,actor='test',recorded_at=STAMP,kind='cronjob-defaults',value=dict(timezone='Asia/Shanghai',channel='self_app',consent_reference='accepted'))
        with self.assertRaisesRegex(PlanError,'Stale'):plans.configure(self.remote.store(),request)


if __name__ == '__main__':unittest.main()
