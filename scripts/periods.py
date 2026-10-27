"""Append-only user feedback for days and arbitrary bounded periods. No I/O."""
import json
from model import PREFIX, require, ident, day, zone, instant, digest, encode


def scope(value):
    keys = {'kind','start','end','timezone','label','plan_id'}
    require(isinstance(value,dict) and set(value) == keys, 'Period scope requires kind/start/end/timezone/label/plan_id')
    require(value['kind'] in {'day','week','month','quarter','year','custom'}, 'Invalid period kind')
    require(day(value['start']) <= day(value['end']), 'Reversed period')
    require(value['kind'] != 'day' or value['start'] == value['end'], 'Daily feedback needs one day')
    zone(value['timezone'])
    require(isinstance(value['label'],str) and 0 < len(value['label'].strip()) <= 180, 'Period label required')
    if value['plan_id'] is not None:
        ident(value['plan_id'])
    return value


def path_for(value):
    scope(value)
    # Labels can change without creating a second identity for the same period.
    key = {k:v for k,v in value.items() if k != 'label'}
    return PREFIX+'periods/'+value['kind']+'/'+value['start'][:4]+'/'+digest(encode(key))+'.json'


def append(authority, records, value, stamp, actor, snapshot):
    require(set(value) == {'scope','text','basis','completion'}, 'Invalid period feedback fields')
    period = scope(value['scope']); path = path_for(period)
    require(isinstance(value['text'],str) and 0 < len(value['text'].strip()) <= 8000, 'Feedback text required')
    require(value['basis'] in {'user_statement','agent_observation'}, 'Feedback basis required')
    require(value['completion'] is None or isinstance(value['completion'],str) and 0 < len(value['completion'].strip()) <= 2000, 'Completion feedback must be explicit wording or null')
    require(period['plan_id'] is None or period['plan_id'] in records, 'Unknown feedback plan')
    record = json.loads(authority[path]) if path in authority else dict(schema_version=1,scope=period,notes=[])
    require(record.get('schema_version') == 1 and path_for(record['scope']) == path and isinstance(record['notes'],list), 'Invalid saved period feedback')
    require(len(record['notes']) < 500, 'Period note limit reached; choose a narrower scope')
    record['scope'] = period
    record['notes'].append(dict(at=stamp,actor=actor,source_snapshot=snapshot,text=value['text'],basis=value['basis'],completion=value['completion']))
    authority[path] = encode(record)


def read(store, value):
    store.entry()
    path = path_for(value)
    # A partial read must request this exact path; a host-confirmed full tree can prove absence.
    if store.complete and path not in store.files:
        return dict(status='ok',snapshot=store.commit,path=path,notes=[],exists=False)
    record = store.obj(path)
    require(record.get('schema_version') == 1 and path_for(record['scope']) == path, 'Period identity mismatch')
    return dict(status='ok',snapshot=store.commit,path=path,sha256=digest(store.files[path]),record=record,exists=True)
