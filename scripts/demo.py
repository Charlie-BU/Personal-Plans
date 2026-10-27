#!/usr/bin/env python3
"""Exercise fictional planning data in RAM. No account, network, or personal data files."""
import base64
import copy
import json
import sys
sys.dont_write_bytecode=True
import plans
from model import NeedFiles, encode, digest

STAMP='2026-10-19T12:00:00+08:00'

class SyntheticRepository:
    def __init__(self):
        self.files={'README.md':b'synthetic shared root','atlas/demo.json':b'fictional Atlas data','health/demo.json':b'fictional health data'}
        self.commit='a'*40;self.operation=0

    def envelope(self,files=None,complete=True):
        return {'snapshot':self.commit,'complete':complete,'files_base64':{p:base64.b64encode(v).decode() for p,v in (self.files if files is None else files).items()}}

    def store(self):return plans.Store(self.envelope())

    def accept(self,prepared):
        assert prepared['status']=='prepared' and prepared['base_commit']==self.commit
        for path in prepared['delete_paths']:
            assert path.startswith('plans/');self.files.pop(path)
        for path,value in prepared['files_base64'].items():
            assert path.startswith('plans/');self.files[path]=base64.b64decode(value)
        self.commit=digest(encode({p:digest(v) for p,v in sorted(self.files.items())}))[:40]

    def apply(self,changes):
        self.operation+=1
        self.accept(plans.apply(self.store(),{'operation_id':'op_demo_'+str(self.operation),'base_commit':self.commit,'actor':'fictional-demo','recorded_at':STAMP,'changes':changes}))


def record(rid,kind,title,summary,**fields):
    return {'id':rid,'kind':kind,'title':title,'summary':summary,'timezone':'Asia/Shanghai',
            'priority':{'quadrant':'important_not_urgent','source':'inferred','reason':'虚构演示：关联长期目标，无额外紧急风险。'},
            'references':[{'namespace':'demo','id':'fictional-scenario'}],**fields}


def main():
    repo=SyntheticRepository();foreign=dict(repo.files)
    repo.accept(plans.initialize(repo.store(),{'vault_id':'vault_fictional_demo','timezone':'Asia/Shanghai','calendar':{'mode':'pending'},'recorded_at':STAMP}))
    items=[
        record('goal_language','goal','能清楚地介绍自己的项目','半年内能够用英文说明项目背景、证据和结果。',period={'start':'2026-10-01','end':'2027-03-31'}),
        record('milestone_share','milestone','完成三分钟介绍','把长期目标转成一次可验证的项目介绍。',parent_id='goal_language',period={'start':'2026-10-01','end':'2026-10-31'}),
        record('task_intro','task','练习项目英文介绍','准备三分钟开场，并整理同伴可能提出的问题。',parent_id='milestone_share',aliases=['英语表达','口语练习'],body='虚构资料：先说项目背景，再说明验证方法。',schedule={'start':'2026-10-19T09:00:00+08:00','end':'2026-10-19T10:00:00+08:00'},due_at='2026-10-19T11:00:00+08:00'),
        record('task_repeat','task','隔日练习问答','每两个自然日练习一次，保留各次执行记录。',parent_id='milestone_share',schedule={'start':'2026-10-19T19:00:00+08:00','end':'2026-10-19T19:30:00+08:00'},due_at='2026-10-19T20:00:00+08:00',recurrence={'dtstart':'2026-10-19T19:00:00+08:00','rrule':'FREQ=DAILY;INTERVAL=2'}),
        record('task_other','task','整理虚构采购清单','与英文介绍无关的日常事项。',body='只用于证明查询不会读取未命中的正文。',time_window={'date':'2026-10-20','label':'下午','estimated_minutes':20}),
    ]
    repo.apply([{'action':'create','record':r} for r in items])
    sparse={};read_paths=[];query={'parent_id':'milestone_share','text':['英语表达','口语练习']}
    while True:
        try:match=plans.query(plans.Store(repo.envelope(sparse,False)),query);break
        except NeedFiles as missing:
            for path in missing.paths:sparse[path]=repo.files[path]
            read_paths.extend(missing.paths)
    assert match['total']==1 and match['items'][0]['id']=='task_intro'
    assert 'plans/tasks/task_other.json' not in read_paths
    original=plans.read(repo.store(),'task_intro')
    repo.apply([{'action':'complete','id':'task_intro','expected_sha256':original['sha256'],'completed_at':'2026-10-19T10:30:00+08:00','completion_precision':'instant'}])
    first=next(r for r in plans.agenda(repo.store(),'2026-10-19','2026-10-23',STAMP)['items'] if r.get('task_id')=='task_repeat')
    raw={k:v for k,v in first.items() if k not in {'virtual','timing','original_timing','materialized','path'}}
    repo.apply([{'action':'materialize','record':raw,'expected_series_sha256':plans.read(repo.store(),'task_repeat')['sha256']}])
    persisted=plans.read(repo.store(),raw['id'])
    repo.apply([{'action':'reschedule','id':raw['id'],'expected_sha256':persisted['sha256'],'reason':'虚构示例：等待同伴反馈，本次顺延。','fields':{'schedule':{'start':'2026-10-20T19:00:00+08:00','end':'2026-10-20T19:30:00+08:00'},'due_at':'2026-10-20T20:00:00+08:00'}}])
    summary=plans.report(repo.store(),'2026-10-19','2026-10-19',STAMP)
    assert all(repo.files[p]==value for p,value in foreign.items())
    print(json.dumps({'mode':'synthetic_in_memory_only','calendar_connected':False,'notifications_sent':False,'personal_data_files_written':0,
       'checks':{'shared_namespace_preserved':True,'precision_query_ids':[r['id'] for r in match['items']],'files_fetched_for_query':len(set(read_paths)),'unrelated_body_read':False,'record_hash_verified':True,'single_instance_rescheduled':True},
       'today':[{k:r.get(k) for k in ['id','title','status','timing','original_timing','materialized']} for r in plans.agenda(repo.store(),'2026-10-19','2026-10-19',STAMP)['items']],
       'report':{k:summary[k] for k in ['due_count','completed_count','on_time','late','original_due_count','unconfirmed']},
       'verification':plans.run(repo.envelope(),'verify',None)['status']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
