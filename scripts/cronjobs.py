"""Portable job definitions, scheduler bindings and at-most-one run claims. No I/O."""
import datetime as dt
import json
import re
from model import PREFIX, require, ident, zone, instant, encode, digest

JOBS = PREFIX + 'cronjobs/'


def cron(expression):
    require(isinstance(expression, str) and len(expression) <= 160, 'Five-field numeric cron required')
    fields = expression.split()
    require(len(fields) == 5, 'Five-field numeric cron required')
    allowed = []
    for field, (low, high) in zip(fields, [(0,59),(0,23),(1,31),(1,12),(0,6)]):
        values = set()
        for part in field.split(','):
            match = re.fullmatch(r'(\*|\d+(?:-\d+)?)(?:/(\d+))?', part)
            require(match is not None, 'Unsupported cron field')
            base, step = match.groups()
            require(step is None or 0 < int(step) <= high-low+1, 'Invalid cron step')
            if base != '*':
                bounds = [int(n) for n in base.split('-')]
                require(low <= bounds[0] <= bounds[-1] <= high, 'Cron value outside range')
            else:
                bounds = [low, high]
            start, end = bounds[0], bounds[-1] if len(bounds) > 1 else (high if step else bounds[0])
            values.update(range(start, end+1, int(step or 1)))
        allowed.append(values)
    require(fields[2] == '*' or fields[4] == '*', 'Use either month-day or weekday to avoid scheduler ambiguity')
    return allowed


def definition(value):
    require(set(value) == {'id','title','enabled','cron','timezone','purpose','instructions','channel','consent_reference','quiet_start','quiet_end','max_per_day'}, 'Invalid cronjob fields')
    ident(value['id']); zone(value['timezone']); cron(value['cron'])
    require(type(value['enabled']) is bool, 'Job enabled must be boolean')
    require(value['purpose'] in {'morning_plan','evening_report','plan_reminders','weekly_review','goal_checkin','custom'}, 'Invalid job purpose')
    for key, limit in [('title',180),('instructions',8000),('consent_reference',1000)]:
        require(isinstance(value[key],str) and 0 < len(value[key].strip()) <= limit, 'Invalid job '+key)
    ident(value['channel'])
    for key in ['quiet_start','quiet_end']:
        require(isinstance(value[key],str) and re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',value[key]), 'Quiet hours require HH:MM')
    require(type(value['max_per_day']) is int and 1 <= value['max_per_day'] <= 100, 'Invalid job daily cap')


def defaults(timezone, channel, consent_reference):
    return [dict(id=rid, title=title, enabled=True, cron=schedule, timezone=timezone,
                 purpose=purpose, instructions=instructions, channel=channel,
                 consent_reference=consent_reference, quiet_start='22:00', quiet_end='07:00', max_per_day=1)
            for rid,title,schedule,purpose,instructions in [
                ('morning_plan','早间计划提醒与制定','0 8 * * *','morning_plan','按用户时区读取今日计划，用简洁表格列出已有计划的时间、事项、优先级和状态；同时提醒设立或补充当日计划。无计划时明确说明暂无已记录计划，不编造事项；已有计划时不称尚未制定。'),
                ('evening_report','晚间计划完成报告','0 21 * * *','evening_report','读取今日计划与完成反馈，生成有来源的当日完成报告；未反馈标为未知，不自动判为失败。以简洁表格展示结果和待确认事项。')]]


def update(authority, kind, value, stamp):
    """Called inside configure's existing atomic operation and CAS boundary."""
    if kind == 'cronjob-defaults':
        require(set(value) == {'timezone','channel','consent_reference'}, 'Defaults need timezone/channel/consent_reference')
        for job in defaults(**value):
            if JOBS+'definitions/'+job['id']+'.json' not in authority:
                update(authority,'cronjob',job,stamp)
        return
    if kind == 'cronjob':
        definition(value)
        path = JOBS+'definitions/'+value['id']+'.json'
        old = json.loads(authority[path]) if path in authority else None
        authority[path] = encode(dict(value, schema_version=1, revision=old['revision']+1 if old else 1, updated_at=stamp))
        return
    rid = ident(value.get('job_id'))
    job_path = JOBS+'definitions/'+rid+'.json'
    require(job_path in authority, 'Unknown cronjob')
    job = json.loads(authority[job_path])
    binding_path = JOBS+'bindings/'+rid+'.json'
    binding = json.loads(authority[binding_path]) if binding_path in authority else None
    if kind == 'cronjob-binding':
        require(set(value) == {'job_id','owner','provider','external_id','state','verified_at','job_revision'}, 'Invalid scheduler binding')
        ident(value['owner']); ident(value['provider'])
        require(value['state'] in {'pending','active','paused','error'}, 'Invalid scheduler state')
        require(value['job_revision'] == job['revision'], 'Binding uses stale job revision')
        require(value['external_id'] is None or isinstance(value['external_id'],str) and 0 < len(value['external_id']) <= 500, 'Invalid scheduler ID')
        if binding and binding['state'] == 'active':
            require(value['state'] in {'active','paused'}, 'Pause and verify previous scheduler before clearing binding')
        if binding and binding['state'] == 'active' and any(binding[k] != value[k] for k in ['owner','provider','external_id']):
            require(False, 'Pause and verify previous scheduler before takeover')
        if value['state'] in {'active','paused'}:
            require(value['external_id'] and value['verified_at'], 'Scheduler readback required')
            require(instant(value['verified_at']) <= instant(stamp), 'Future scheduler verification')
        else:
            require(value['verified_at'] is None, 'Unverified binding must use null verified_at')
        require(value['state'] != 'active' or job['enabled'], 'Disabled job cannot activate')
        authority[binding_path] = encode(dict(value, schema_version=1, updated_at=stamp))
        return
    require(kind == 'cronjob-run', 'Unknown cronjob operation')
    require(set(value) == {'job_id','owner','scheduled_at','state','receipt'}, 'Invalid job run fields')
    ident(value['owner'])
    slot = instant(value['scheduled_at']).astimezone(dt.timezone.utc).isoformat()
    require(instant(slot) <= instant(stamp), 'Cannot claim a future run')
    key = digest((rid+'\n'+slot).encode())
    path = JOBS+'runs/'+slot[:7]+'/'+key+'.json'
    previous = json.loads(authority[path]) if path in authority else None
    state = value['state']
    require(state in {'claimed','succeeded','failed','unknown','skipped'}, 'Invalid run state')
    if state == 'claimed':
        require(previous is None, 'Run slot already claimed; reconcile instead of replay')
        local = instant(slot).astimezone(zone(job['timezone']))
        require(local.second == local.microsecond == 0 and all(v in choices for v,choices in zip([local.minute,local.hour,local.day,local.month,(local.weekday()+1)%7],cron(job['cron']))), 'Run slot does not match cron schedule')
        clock = instant(stamp).astimezone(zone(job['timezone'])).strftime('%H:%M')
        start,end = job['quiet_start'],job['quiet_end']
        if start < end:
            quiet = start <= clock < end
        else:
            quiet = start != end and (clock >= start or clock < end)
        require(not quiet, 'Job is inside quiet hours')
        used = sum(1 for p,raw in authority.items() if p.startswith(JOBS+'runs/') and (run := json.loads(raw))['job_id'] == rid and run['state'] in {'claimed','unknown','succeeded'} and instant(run['claimed_at']).astimezone(zone(job['timezone'])).date() == instant(stamp).astimezone(zone(job['timezone'])).date())
        require(used < job['max_per_day'], 'Job daily cap reached')
        require(value['receipt'] is None, 'Claim cannot contain a receipt')
        require(job['enabled'] and binding and binding['state'] == 'active'
                and binding['owner'] == value['owner'] and binding['job_revision'] == job['revision'], 'Active current scheduler binding required')
        record = dict(value, schema_version=1, scheduled_at=slot, job_revision=job['revision'], claimed_at=stamp)
    else:
        require(previous and previous['owner'] == value['owner'] and previous['state'] in {'claimed','unknown'}, 'No matching unresolved claim')
        receipt = value['receipt']
        require(isinstance(receipt,dict) and set(receipt) == {'reference','verified_at'}
                and isinstance(receipt['reference'],str) and 0 < len(receipt['reference']) <= 1000, 'Result receipt reference required')
        require(instant(previous['claimed_at']) <= instant(receipt['verified_at']) <= instant(stamp), 'Invalid run verification time')
        record = dict(previous, state=state, receipt=receipt)
    authority[path] = encode(dict(record, updated_at=stamp))
