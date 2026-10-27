#!/usr/bin/env python3
"""In-memory planning store, staged retrieval, recurrence, calendar intents and reports.
All repository operations are prepared only. Host tools own authentication and publication.
"""
import argparse
import base64
import copy
import datetime as dt
import json
import re
import sys
from collections import defaultdict

sys.dont_write_bytecode = True
from model import (
    PREFIX,
    PAGE_SIZE,
    PlanError,
    NeedFiles,
    require,
    encode,
    digest,
    ident,
    day,
    instant,
    temporal,
    zone,
    validate,
    validate_relations,
    record_path,
    tokens,
    normalize,
    timing,
    PRIORITIES,
    KINDS,
    STATES,
)
from recurrence import expand, holiday_paths, occurrence_id

ROOT = PREFIX + "indexes/root.json"
VAULT = PREFIX + "vault.json"
MANIFEST = PREFIX + "manifests/files.json"
GENERATED = (PREFIX + "indexes/", PREFIX + "manifests/")
RECORD_DIRS = (PREFIX + "goals/", PREFIX + "tasks/", PREFIX + "occurrences/")
AUTHORITY = RECORD_DIRS + (
    PREFIX + "activities/",
    PREFIX + "operations/",
    PREFIX + "calendars/holidays/",
    PREFIX + "policies/",
    PREFIX + "integrations/",
    PREFIX + "reports/",
    PREFIX + "cronjobs/",
    PREFIX + "periods/",
)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def safe_path(path):
    require(
        isinstance(path, str)
        and path.startswith(PREFIX)
        and "\\" not in path
        and all(x not in {"", ".", ".."} for x in path.split("/")),
        "Output outside plans/ or unsafe path",
    )
    require(
        path == VAULT or path.startswith(AUTHORITY + GENERATED),
        "Unknown managed plans/ path",
    )
    return path


class Store:
    def __init__(self, envelope):
        self.commit = envelope.get("snapshot")
        require(
            self.commit is None
            or isinstance(self.commit, str)
            and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.commit),
            "Snapshot must be a repository commit",
        )
        self.complete = envelope.get("complete") is True
        require(
            isinstance(envelope.get("files_base64"), dict),
            "files_base64 mapping required",
        )
        require(
            "plans" not in envelope["files_base64"], "plans is a file, not a directory"
        )
        self.files = {}
        for path, value in envelope["files_base64"].items():
            if not path.startswith(PREFIX):
                continue
            safe_path(path)
            try:
                self.files[path] = base64.b64decode(value, validate=True)
            except (ValueError, TypeError):
                raise PlanError("Invalid file encoding")

    def need(self, paths):
        paths = list(paths)
        for path in paths:
            safe_path(path)
        missing = set(paths) - set(self.files)
        if missing:
            raise NeedFiles(missing)

    def obj(self, path):
        self.need([path])
        try:
            return json.loads(self.files[path])
        except (ValueError, UnicodeDecodeError):
            raise PlanError("Invalid JSON at " + path)

    def entry(self):
        self.need([VAULT, ROOT])
        v, r = self.obj(VAULT), self.obj(ROOT)
        require(
            v.get("schema_version") == r.get("schema_version") == 1
            and v.get("id") == r.get("vault_id")
            and v.get("revision") == r.get("revision"),
            "Invalid/stale planning entrypoint",
        )
        require(
            self.commit is not None, "Existing data requires a pinned repository commit"
        )
        return v, r

    def checked(self, rebuild=False):
        require(
            self.complete,
            "Writes/verify require the complete plans/ subtree, confirmed by the host",
        )
        self.need([MANIFEST, VAULT, ROOT])
        self.entry()
        manifest = self.obj(MANIFEST)
        canonical = {p: v for p, v in self.files.items() if not p.startswith(GENERATED)}
        require(
            manifest == {p: digest(v) for p, v in sorted(canonical.items())},
            "Incomplete or modified authoritative files",
        )
        records = records_from(canonical)
        generated = derive(canonical, records)
        require(
            rebuild or {p: v for p, v in self.files.items() if p.startswith(GENERATED)}
            == generated,
            "Indexes are missing, stale or corrupt",
        )
        return canonical, records


def records_from(files):
    records = {}
    for path, value in files.items():
        if not path.startswith(RECORD_DIRS):
            continue
        r = json.loads(value)
        validate(r)
        require(
            path == record_path(r) and r["id"] not in records,
            "Record ID/path collision",
        )
        records[r["id"]] = r
    return records


def route_digest(route):
    return digest(route.encode())


def catalog_path(route):
    return PREFIX + "indexes/catalog/" + route_digest(route)[:1] + ".json"


def record_index_path(rid):
    return PREFIX + "indexes/records/" + digest(ident(rid).encode())[:1] + ".json"


def card(r, raw):
    return {
        k: r.get(k)
        for k in [
            "id",
            "kind",
            "title",
            "parent_id",
            "status",
            "timezone",
            "schedule",
            "time_window",
            "due_at",
            "task_id",
            "recurrence_id",
            "revision",
        ]
    } | {
        "summary_preview": r["summary"][:180],
        "summary_truncated": len(r["summary"]) > 180,
        "quadrant": r["priority"]["quadrant"],
        "path": record_path(r),
        "sha256": digest(raw),
        "series": bool(r.get("recurrence")),
    }


def date_keys(r, display_timezone=None):
    values = set()
    tz = r["timezone"]
    display = zone(display_timezone or tz)

    def add_interval(start, end):
        first = start.astimezone(display).date()
        last = (
            (end - dt.timedelta(microseconds=1)).astimezone(display).date()
            if end > start
            else first
        )
        require((last - first).days <= 367, "Schedule range exceeds bounded date index")
        for i in range((last - first).days + 1):
            values.add((first + dt.timedelta(days=i)).isoformat())

    if r.get("due_at"):
        values.add(
            temporal(r["due_at"], tz, True).astimezone(display).date().isoformat()
        )
    if r.get("time_window"):
        start = temporal(r["time_window"]["date"], tz)
        add_interval(start, start + dt.timedelta(days=1))
    if r.get("schedule"):
        schedule = r["schedule"]
        start = temporal(schedule["start"], tz)
        end = temporal(schedule.get("end"), tz)
        if end is None:
            end = (
                start + dt.timedelta(days=1) if len(schedule["start"]) == 10 else start
            )
        add_interval(start, end)
    return values


def record_routes(r, timezone):
    keys = {
        "all",
        "id:" + r["id"],
        "kind:" + r["kind"],
        "status:" + r["status"],
        "quadrant:" + r["priority"]["quadrant"],
    }
    if r.get("parent_id"):
        keys.add("parent:" + r["parent_id"])
    for dep in r["depends_on"]:
        keys.add("dependent:" + dep)
    for ref in r["references"]:
        keys.add("ref:" + ref["namespace"] + ":" + ref["id"])
    for term in tokens(
        " ".join([r["title"], r["summary"], r["body"]] + r["aliases"] + r["tags"])
    ):
        keys.add("term:" + term)
    for date in date_keys(r, timezone):
        keys.add("day:" + date)
    if r.get("original_due_at"):
        keys.add(
            "original-day:"
            + temporal(r["original_due_at"], r["timezone"], True)
            .astimezone(zone(timezone))
            .date()
            .isoformat()
        )
    if r.get("recurrence"):
        keys.add("series")
    if r["kind"] == "occurrence":
        keys.add("override:" + r["task_id"] + ":" + r["recurrence_id"][:7])
    return keys


def derive(authority, records=None):
    v = json.loads(authority[VAULT])
    if records is None:
        records = records_from(authority)
    validate_relations(records)
    routes = defaultdict(dict)
    record_shards = defaultdict(dict)
    for rid, r in sorted(records.items()):
        c = card(r, authority[record_path(r)])
        record_shards[record_index_path(rid)][rid] = c
        for route in record_routes(r, v["timezone"]):
            routes[route][rid] = None
    generated = {}
    catalogs = defaultdict(dict)
    for route, values in sorted(routes.items()):
        catalogs[catalog_path(route)][route] = list(values)
    for path, mapping in catalogs.items():
        generated[path] = encode(mapping)
    generated.update({path:encode(cards) for path,cards in record_shards.items()})
    manifest = {p: digest(value) for p, value in sorted(authority.items())}
    generated[MANIFEST] = encode(manifest)
    effect_groups = defaultdict(list)
    for path, value in sorted(authority.items()):
        if not path.startswith(PREFIX + "integrations/"):
            continue
        intent = json.loads(value)
        when = intent.get("payload", {}).get("trigger_at") or intent["created_at"]
        when = instant(when).astimezone(zone(v["timezone"])).isoformat()
        key = intent["state"] + ":" + when[:7]
        effect_groups[key].append(
            {
                "id": intent["id"],
                "kind": intent["kind"],
                "state": intent["state"],
                "when": when,
                "path": path,
                "sha256": digest(value),
            }
        )
    effect_routes = {}
    for key, items in effect_groups.items():
        items.sort(key=lambda c: (c["when"], c["id"]))
        count = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
        effect_routes[key] = count
        for page in range(count):
            generated[
                PREFIX
                + "indexes/effects/"
                + key.replace(":", "/")
                + "/"
                + str(page)
                + ".json"
            ] = encode(items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE])
    report_groups = defaultdict(list)
    for path, value in sorted(authority.items()):
        if not path.startswith(PREFIX + "reports/"):
            continue
        saved = json.loads(value)
        report_groups[saved["created_at"][:7]].append({
            "id": saved["id"], "path": path, "sha256": digest(value),
            "from": saved["metrics"]["from"], "to": saved["metrics"]["to"],
            "source_snapshot": saved["source_snapshot"], "created_at": saved["created_at"],
        })
    report_routes = {}
    for month, items in report_groups.items():
        items.sort(key=lambda c: (c["created_at"], c["id"]))
        count = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
        report_routes[month] = count
        for page in range(count):
            generated[PREFIX + "indexes/reports/" + month + "/" + str(page) + ".json"] = encode(items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE])
    from cronjobs import JOBS
    job_cards = [{"path":p,"sha256":digest(raw)} for p,raw in sorted(authority.items()) if p.startswith(JOBS+"definitions/") or p.startswith(JOBS+"bindings/")]
    for page in range((len(job_cards)+PAGE_SIZE-1)//PAGE_SIZE):
        generated[PREFIX+"indexes/cronjobs/"+str(page)+".json"] = encode(job_cards[page*PAGE_SIZE:(page+1)*PAGE_SIZE])
    generated[ROOT] = encode(
        {
            "schema_version": 1,
            "vault_id": v["id"],
            "revision": v["revision"],
            "record_count": len(records),
            "page_size": PAGE_SIZE,
            "catalog_shards": 16,
            "catalog_shards_present": sorted(
                path.rsplit("/", 1)[1].removesuffix(".json") for path in catalogs
            ),
            "index_format": 2,
            "routing": "route shard -> record IDs -> card shard -> verified record",
            "manifest_sha256": digest(generated[MANIFEST]),
            "effect_routes": effect_routes,
            "report_routes": report_routes,
            **({"cronjob_pages": (len(job_cards)+PAGE_SIZE-1)//PAGE_SIZE} if job_cards else {}),
        }
    )
    return generated


def routes_read(store, routes):
    _, root = store.entry()
    require(root.get('index_format') == 2, 'Rebuild planning indexes with reindex before reading')
    present = root.get('catalog_shards_present')
    require(isinstance(present,list) and all(isinstance(x,str) and re.fullmatch(r'[0-9a-f]',x) for x in present), 'Invalid catalog shard inventory')
    routes = set(routes)
    store.need({catalog_path(r) for r in routes if route_digest(r)[:1] in present})
    ids = {}
    for route in routes:
        values = store.obj(catalog_path(route)).get(route,[]) if route_digest(route)[:1] in present else []
        require(isinstance(values,list) and all(isinstance(rid,str) for rid in values), 'Invalid route ID list')
        require(len(set(values)) == len(values), 'Duplicate route IDs')
        ids[route] = values
    paths = {record_index_path(rid) for values in ids.values() for rid in values}
    store.need(paths)
    shards = {path:store.obj(path) for path in paths}
    found = {}
    for route, values in ids.items():
        found[route] = {}
        for rid in values:
            c = shards[record_index_path(rid)].get(rid)
            require(isinstance(c,dict) and c.get('id') == rid and c.get('path') and isinstance(c.get('sha256'),str) and re.fullmatch(r'[0-9a-f]{64}',c['sha256']), 'Missing or malformed record card')
            safe_path(c['path'])
            require(c['path'].startswith(RECORD_DIRS), 'Index points outside planning records')
            found[route][rid] = c
    return found


def full_records(store, cards):
    store.need([c["path"] for c in cards.values()])
    result = {}
    for rid, c in cards.items():
        raw = store.files[c["path"]]
        require(digest(raw) == c["sha256"], "Record differs from indexed hash")
        r = store.obj(c["path"])
        validate(r)
        require(
            r["id"] == rid and record_path(r) == c["path"], "Record identity mismatch"
        )
        result[rid] = r
    return result


def query(store, q):
    allowed = {
        "id",
        "kind",
        "status",
        "quadrant",
        "parent_id",
        "dependent_of",
        "reference",
        "text",
        "on",
        "original_on",
        "offset",
        "limit",
        "max_candidates",
    }
    require(isinstance(q, dict) and not set(q) - allowed, "Unsupported query fields")
    for field, values in [("kind", KINDS), ("status", STATES), ("quadrant", PRIORITIES)]:
        if field in q:
            require(q[field] in values, "Invalid query " + field)
    constraints = []
    alternatives = []
    for field, prefix in [
        ("id", "id"),
        ("kind", "kind"),
        ("status", "status"),
        ("quadrant", "quadrant"),
        ("parent_id", "parent"),
        ("dependent_of", "dependent"),
        ("on", "day"),
        ("original_on", "original-day"),
    ]:
        if q.get(field):
            if field in {"on", "original_on"}:
                day(q[field])
            constraints.append(prefix + ":" + q[field])
    if q.get("reference"):
        ref = q["reference"]
        require(
            set(ref) == {"namespace", "id"}, "Reference query requires namespace/id"
        )
        constraints.append("ref:" + ref["namespace"] + ":" + ref["id"])
    texts = q.get("text", [])
    if isinstance(texts, str):
        texts = [texts]
    require(
        isinstance(texts, list)
        and len(texts) <= 12
        and all(isinstance(t, str) and t.strip() and len(t) <= 200 for t in texts),
        "Text accepts up to 12 explicit aliases/phrases",
    )
    for text in texts:
        terms = tokens(text)
        require(terms, "Search text has no supported lexical terms")
        alternatives.append(["term:" + t for t in sorted(terms, key=lambda term: (-len(term), term))])
    # A literal phrase must contain its anchor. Start with one posting per alias;
    # fetch additional necessary tokens only when verification would exceed budget.
    routes = set(constraints + [group[0] for group in alternatives]) or {"all"}
    if q.get("id"):
        ident(q["id"])
        id_route = "id:" + q["id"]
        selected = routes_read(store, [id_route])[id_route]
        originals = full_records(store, selected)
        vault, _ = store.entry()
        indexes = {route: {} for route in routes}
        for rid, record in originals.items():
            keys = record_routes(record, vault["timezone"])
            for route in routes & keys:
                indexes[route][rid] = selected[rid]
    else:
        indexes = routes_read(store, routes)
    matches = None
    for route in constraints:
        matches = (
            set(indexes[route]) if matches is None else matches & set(indexes[route])
        )
    if alternatives:
        lexical = set()
        for group in alternatives:
            ids = set(indexes[group[0]])
            lexical.update(ids)
        matches = lexical if matches is None else matches & lexical
    if matches is None:
        matches = set(indexes["all"])
    cards = {
        rid: c
        for route in routes
        for rid, c in indexes[route].items()
        if rid in matches
    }
    # Index postings are candidates; re-read text hits to verify literal phrases, not token coincidence.
    if texts:
        budget = q.get("max_candidates", 60)
        require(
            type(budget) is int and 1 <= budget <= 200,
            "Invalid search candidate budget",
        )
        if len(cards) > budget:
            other_routes = {route for group in alternatives for route in group[1:]}
            more = routes_read(store, other_routes) if other_routes else {}
            narrowed = set()
            for group in alternatives:
                ids = set(indexes[group[0]])
                for route in group[1:]:
                    ids.intersection_update(more.get(route, indexes.get(route, {})))
                narrowed.update(ids)
            cards = {rid: c for rid, c in cards.items() if rid in narrowed}
        if len(cards) > budget:
            return {
                "status": "refine_query",
                "snapshot": store.commit,
                "candidate_count": len(cards),
                "reason": "Candidate set exceeds the explicit original-record verification budget",
                "suggestions": [
                    "add a kind, status, date or parent constraint",
                    "use more specific wording or aliases",
                ],
                "complete": False,
            }
        originals = full_records(store, cards)
        cards = {
            rid: c
            for rid, c in cards.items()
            if any(
                normalize(text)
                in normalize(
                    " ".join(
                        [
                            originals[rid]["title"],
                            originals[rid]["summary"],
                            originals[rid]["body"],
                        ]
                        + originals[rid]["aliases"]
                        + originals[rid]["tags"]
                    )
                )
                for text in texts
            )
        }
    ordered = sorted(cards.values(), key=lambda c: c["id"])
    offset = q.get("offset", 0)
    limit = q.get("limit", 30)
    require(
        type(offset) is int
        and offset >= 0
        and type(limit) is int
        and 1 <= limit <= 100,
        "Invalid pagination",
    )
    page = ordered[offset : offset + limit]
    end = offset + len(page)
    return {
        "status": "ok",
        "snapshot": store.commit,
        "verification": "indexed_candidates",
        "items": page,
        "total": len(ordered),
        "next_offset": end if end < len(ordered) else None,
        "read_before_answer": True,
    }


def read(store, rid):
    ident(rid)
    cards = routes_read(store, ["id:" + rid])["id:" + rid]
    require(rid in cards, "Record not found at supplied snapshot")
    record = full_records(store, cards)[rid]
    return {
        "status": "ok",
        "snapshot": store.commit,
        "verification": "record_hash",
        "record": record,
        "path": cards[rid]["path"],
        "sha256": cards[rid]["sha256"],
    }


def days_between(a, b):
    start, end = day(a), day(b)
    require(
        start <= end and (end - start).days <= 366, "Query window must be 0–366 days"
    )
    return [
        (start + dt.timedelta(days=i)).isoformat()
        for i in range((end - start).days + 1)
    ]


def agenda(store, a, b, as_of, carry_over=False):
    instant(as_of)
    dates = days_between(a, b)
    route_names = ["day:" + d for d in dates] + ["series"]
    if carry_over:
        route_names += ["status:" + s for s in ["pending", "in_progress", "blocked"]]
    postings = routes_read(store, route_names)
    cards = {rid: c for values in postings.values() for rid, c in values.items()}
    loaded = full_records(store, cards)
    series = [r for r in loaded.values() if r.get("recurrence")]
    vault, _ = store.entry()
    view_zone = zone(vault["timezone"])
    require(instant(as_of) >= instant(vault["updated_at"]), "As-of predates snapshot revision; use a historical repository snapshot")
    global_low = dt.datetime.combine(day(a), dt.time(), view_zone)
    global_high = dt.datetime.combine(
        day(b) + dt.timedelta(days=1), dt.time(), view_zone
    )
    bounds = {}
    for task in series:
        tz = zone(task["timezone"])
        first = temporal(task["schedule"]["start"], task["timezone"])
        offsets = [0]
        for value in [
            task["schedule"].get("end"),
            task.get("due_at"),
            task.get("original_due_at"),
        ]:
            if value:
                offsets.append(
                    (temporal(value, task["timezone"]).date() - first.date()).days
                )
        require(
            max(abs(v) for v in offsets) <= 366,
            "Recurring offsets beyond a year require a separate goal/milestone",
        )
        low = global_low.astimezone(tz).date() - dt.timedelta(days=max(offsets))
        high = (global_high - dt.timedelta(microseconds=1)).astimezone(
            tz
        ).date() - dt.timedelta(days=min(offsets))
        bounds[task["id"]] = (low, high)
    override_routes = []
    for task in series:
        low, high = bounds[task["id"]]
        months = sorted(
            {
                (low + dt.timedelta(days=i)).isoformat()[:7]
                for i in range((high - low).days + 1)
            }
        )
        override_routes += ["override:" + task["id"] + ":" + month for month in months]
    overrides = routes_read(store, override_routes) if override_routes else {}
    extra = {rid: c for values in overrides.values() for rid, c in values.items()}
    loaded.update(full_records(store, extra))
    virtual = {}
    for task in series:
        if task["status"] in {"cancelled", "paused"}:
            continue
        low, high = bounds[task["id"]]
        paths = holiday_paths(task, low, high)
        holidays = {
            int(p.rsplit("/", 1)[1][:4]): store.obj(p)
            for p in paths
            if p in store.files
        }
        while low <= high:
            end = min(high, low + dt.timedelta(days=366))
            for r in expand(task, low.isoformat(), end.isoformat(), holidays):
                virtual[r["id"]] = r
            require(
                len(virtual) <= 10000, "Too many agenda instances; narrow the window"
            )
            low = end + dt.timedelta(days=1)
    # Materialized exceptions always win, including a cancelled or moved-out original slot.
    all_records = {
        **virtual,
        **{rid: r for rid, r in loaded.items() if not r.get("recurrence")},
    }
    rows = []
    for rid, r in all_records.items():
        if r["kind"] not in {"task", "occurrence"}:
            continue
        in_window = bool(set(date_keys(r, vault["timezone"])) & set(dates))
        overdue = (
            carry_over
            and r["status"] in {"pending", "in_progress", "blocked"}
            and r.get("due_at")
            and temporal(r["due_at"], r["timezone"], True) < global_low
        )
        if not (in_window or overdue):
            continue
        value = copy.deepcopy(r)
        value["timing"] = timing(r, as_of)
        value["original_timing"] = timing(r, as_of, True)
        value["materialized"] = not r.get("virtual", False)
        value["path"] = record_path(r) if value["materialized"] else None
        rows.append(value)
    rows.sort(
        key=lambda r: (
            (r.get("schedule") or {}).get("start")
            or r.get("due_at")
            or (r.get("time_window") or {}).get("date", ""),
            r["id"],
        )
    )
    return {
        "status": "ok",
        "snapshot": store.commit,
        "as_of": as_of,
        "from": a,
        "to": b,
        "items": rows,
        "total": len(rows),
        "verification": "records_and_bounded_recurrence",
        "complete": True,
    }


def report(store, a, b, as_of):
    result = agenda(store, a, b, as_of)
    vault, _ = store.entry()
    view_zone = zone(vault["timezone"])
    originals = routes_read(store, ["original-day:" + d for d in days_between(a, b)])
    extra_cards = {rid: c for group in originals.values() for rid, c in group.items()}
    extra = full_records(store, extra_cards)
    combined = {r["id"]: r for r in result["items"]}
    for rid, r in extra.items():
        if (
            r.get("recurrence")
            or r["kind"] not in {"task", "occurrence"}
            or rid in combined
        ):
            continue
        combined[rid] = {
            **r,
            "timing": timing(r, as_of),
            "original_timing": timing(r, as_of, True),
            "path": record_path(r),
            "materialized": True,
        }
    candidates = list(combined.values())
    roots = (
        routes_read(store, ["parent:" + r["id"] for r in candidates])
        if candidates
        else {}
    )
    leaves = [r for r in candidates if not roots["parent:" + r["id"]]]
    due = []
    excluded = []
    no_deadline = []
    for r in leaves:
        if not r.get("due_at"):
            no_deadline.append(r)
            continue
        d = temporal(r["due_at"], r["timezone"], True).astimezone(view_zone)
        if not a <= d.date().isoformat() <= b or d > instant(as_of):
            continue
        if r["status"] in {"cancelled", "skipped"}:
            excluded.append(r)
        else:
            due.append(r)
    completed = [r for r in due if r["status"] == "completed"]
    original_due = [
        r
        for r in leaves
        if r.get("original_due_at")
        and a
        <= temporal(r["original_due_at"], r["timezone"], True)
        .astimezone(view_zone)
        .date()
        .isoformat()
        <= b
        and temporal(r["original_due_at"], r["timezone"], True) <= instant(as_of)
        and r["status"] not in {"cancelled", "skipped"}
    ]
    return {
        "status": "ok",
        "snapshot": store.commit,
        "from": a,
        "to": b,
        "as_of": as_of,
        "denominator": "leaf task/occurrence; current deadline in period and matured by as_of; cancellations/skips excluded",
        "due_count": len(due),
        "completed_count": len(completed),
        "completion_rate": len(completed) / len(due) if due else None,
        "on_time": sum(r["timing"] == "on_time" for r in completed),
        "late": sum(r["timing"] == "late" for r in completed),
        "unknown_completion_precision": sum(
            r["timing"] == "unknown_precision" for r in completed
        ),
        "original_due_count": len(original_due),
        "original_completed_count": sum(
            r["status"] == "completed" for r in original_due
        ),
        "original_deadline_on_time": sum(
            r["status"] == "completed" and r["original_timing"] == "on_time"
            for r in original_due
        ),
        "unconfirmed": sum(r["status"] != "completed" for r in due),
        "excluded_count": len(excluded),
        "without_deadline": len(no_deadline),
        "evidence": [
            {"id": r["id"], "path": r["path"], "virtual": not r["materialized"]}
            for r in leaves
        ],
        "limitations": [
            "Current snapshot; current and original deadline cohorts are separate. Deleted records require a separate activity-ledger/historical-snapshot review.",
            "No actual-duration or historical-as-of-state inference.",
        ],
    }


def defaults(raw, stamp):
    r = {
        "schema_version": 1,
        "body": "",
        "aliases": [],
        "tags": [],
        "parent_id": None,
        "depends_on": [],
        "status": "pending",
        "schedule": None,
        "time_window": None,
        "due_at": None,
        "original_due_at": None,
        "completed_at": None,
        "completion_precision": None,
        "period": None,
        "recurrence": None,
        "task_id": None,
        "recurrence_id": None,
        "notes": [],
        "references": [],
        "reminders": [],
        "created_at": stamp,
        "updated_at": stamp,
        "revision": 1,
    }
    r.update(copy.deepcopy(raw))
    r.update(created_at=stamp, updated_at=stamp, revision=1)
    r["original_due_at"] = r.get("due_at")
    validate(r)
    return r


def prepare(store, authority, message):
    generated = derive(authority)
    proposed = {**authority, **generated}
    result = {
        "status": "prepared",
        "base_commit": store.commit,
        "message": message,
        "remote_confirmed": False,
        "files_base64": {
            p: base64.b64encode(v).decode()
            for p, v in proposed.items()
            if store.files.get(p) != v
        },
        "delete_paths": sorted(set(store.files) - set(proposed)),
    }
    for path in list(result["files_base64"]) + result["delete_paths"]:
        safe_path(path)
    return result


def initialize(store, settings):
    require(
        store.complete, "Initialization requires a complete target-directory listing"
    )
    if store.files:
        store.checked()
        return {
            "status": "already_initialized",
            "snapshot": store.commit,
            "remote_confirmed": False,
        }
    zone(settings.get("timezone"))
    require(settings.get("vault_id"), "Stable unique vault_id required")
    ident(settings["vault_id"])
    require(
        isinstance(settings.get("calendar"), dict)
        and set(settings["calendar"])
        <= {"provider", "calendar_id", "mode", "verified_at"},
        "Calendar configuration required, even if pending",
    )
    calendar = settings["calendar"]
    require(
        calendar.get("mode") in {"pending", "connected"},
        "Calendar mode must be pending/connected",
    )
    if calendar["mode"] == "connected":
        require(
            calendar.get("provider")
            and calendar.get("calendar_id")
            and calendar.get("verified_at"),
            "Connected calendar needs verified identity",
        )
    stamp = settings.get("recorded_at") or now()
    instant(stamp)
    vault = {
        "schema_version": 1,
        "id": settings["vault_id"],
        "revision": 1,
        "timezone": settings["timezone"],
        "created_at": stamp,
        "updated_at": stamp,
        "calendar": calendar,
    }
    authority = {
        VAULT: encode(vault),
        PREFIX
        + "policies/onboarding.json": encode(
            {
                "schema_version": 1,
                "stage": (
                    "calendar_pending"
                    if calendar["mode"] == "pending"
                    else "first_plan"
                ),
                "completed_steps": ["repository_initialized"],
                "reminders_enabled": False,
            }
        ),
    }
    return prepare(store, authority, "Initialize personal plans namespace")


def apply(store, plan):
    authority, records = store.checked()
    return _apply(store, plan, authority, records)


def _apply(store, plan, authority, records, split_signature=None):
    require(isinstance(plan, dict), "Operation object required")
    ident(plan.get("operation_id"))
    op_path = PREFIX + "operations/" + plan["operation_id"] + ".json"
    signature = digest(encode(plan))
    if op_path in authority:
        require(
            json.loads(authority[op_path])["request_sha256"] == signature,
            "Operation ID reused with different content",
        )
        return {
            "status": "already_applied",
            "snapshot": store.commit,
            "remote_confirmed": False,
        }
    require(
        plan.get("base_commit") == store.commit,
        "Stale base_commit: re-read subtree and rebuild the proposal",
    )
    stamp = plan.get("recorded_at") or now()
    instant(stamp)
    actor = plan.get("actor")
    require(isinstance(actor, str) and actor, "Actor required")
    changes = plan.get("changes")
    require(
        isinstance(changes, list) and 0 < len(changes) <= 100,
        "Use 1–100 changes per operation",
    )
    touched = set()
    invalidated = set()
    events = []
    for i, change in enumerate(changes):
        require(isinstance(change, dict), "Change must be an object")
        action = change.get("action")
        rid = change.get("id") or change.get("record", {}).get("id")
        ident(rid)
        require(rid not in touched, "One change per record per operation")
        touched.add(rid)
        old = records.get(rid)
        before = copy.deepcopy(old)
        if old:
            require(
                change.get("expected_sha256") == digest(authority[record_path(old)]),
                "Record hash conflict",
            )
        if action in {"create", "materialize"}:
            require(old is None, "Record already exists")
            require(
                not any(
                    rid in json.loads(value).get("deleted_ids", [])
                    for path, value in authority.items()
                    if path.startswith(PREFIX + "operations/")
                ),
                "Deleted IDs cannot be reused",
            )
            r = defaults(change.get("record", {}), stamp)
            require(
                r["status"] == "pending",
                "Create pending records, then record a separate lifecycle transition",
            )
            if action == "materialize":
                require(r["kind"] == "occurrence", "Materialize requires an occurrence")
                task = records.get(r["task_id"])
                require(task and task.get("recurrence"), "Missing recurring task")
                require(
                    change.get("expected_series_sha256")
                    == digest(authority[record_path(task)]),
                    "Series changed; regenerate occurrence",
                )
                date = r["recurrence_id"][:10]
                paths = holiday_paths(task, day(date), day(date))
                store.need(paths)
                regional = {int(p.rsplit("/", 1)[1][:4]): store.obj(p) for p in paths}
                expected = next(
                    (v for v in expand(task, date, date, regional) if v["id"] == rid),
                    None,
                )
                require(
                    expected is not None,
                    "Occurrence does not belong to the recurrence set",
                )
                expected.pop("virtual", None)
                # A materialization is a snapshot of the series; edits follow in a later operation.
                for key in set(expected) - {"created_at", "updated_at", "revision"}:
                    require(
                        r.get(key) == expected.get(key),
                        "Materialized occurrence differs from its series: " + key,
                    )
            else:
                require(
                    r["kind"] != "occurrence", "Use materialize for recurring instances"
                )
        else:
            require(old is not None, "Record not found")
            r = copy.deepcopy(old)
            if action == "update":
                fields = change.get("fields")
                require(isinstance(fields, dict) and fields, "Update fields required")
                immutable = {
                    "id",
                    "kind",
                    "schema_version",
                    "created_at",
                    "updated_at",
                    "revision",
                    "status",
                    "completed_at",
                    "completion_precision",
                    "original_due_at",
                    "due_at",
                    "schedule",
                    "task_id",
                    "recurrence_id",
                    "notes",
                }
                require(
                    not set(fields) & immutable,
                    "Use lifecycle/reschedule/note actions for protected fields",
                )
                if "recurrence" in fields and any(v.get("task_id") == rid for v in records.values()):
                    previous = copy.deepcopy(r.get("recurrence") or {})
                    changed = copy.deepcopy(fields["recurrence"] or {})
                    old_stop = previous.pop("stop_before", None)
                    new_stop = changed.pop("stop_before", None)
                    require(
                        previous == changed and new_stop and (not old_stop or temporal(new_stop, r["timezone"]) <= temporal(old_stop, r["timezone"])),
                        "Use split-series; preserve the historical rule with a monotone stop boundary",
                    )
                r.update(copy.deepcopy(fields))
            elif action == "reschedule":
                require(
                    not r.get("recurrence"),
                    "Reschedule an instance; version the series for future changes",
                )
                require(
                    isinstance(change.get("reason"), str) and change["reason"],
                    "Reschedule reason required",
                )
                require(
                    set(change.get("fields", {}))
                    and set(change["fields"]) <= {"schedule", "due_at", "time_window"},
                    "Reschedule only time fields",
                )
                require(
                    r["status"] not in {"completed", "cancelled", "skipped"},
                    "Reopen a terminal record before rescheduling",
                )
                r.update(copy.deepcopy(change["fields"]))
            elif action == "complete":
                require(
                    r["status"] not in {"completed", "cancelled", "skipped"},
                    "Cannot complete a terminal record",
                )
                require(
                    not r.get("recurrence"), "Complete one occurrence, not the series"
                )
                r.update(
                    status="completed",
                    completed_at=change.get("completed_at"),
                    completion_precision=change.get("completion_precision"),
                )
                event_time = temporal(r["completed_at"], r["timezone"])
                require(
                    event_time and event_time <= instant(stamp),
                    "Completion cannot be in the future",
                )
            elif action == "state":
                state = change.get("status")
                require(
                    state
                    in {
                        "pending",
                        "in_progress",
                        "blocked",
                        "cancelled",
                        "skipped",
                        "paused",
                    },
                    "Invalid lifecycle transition",
                )
                require(
                    isinstance(change.get("reason"), str) and change["reason"],
                    "State/reopen reason required",
                )
                r.update(status=state, completed_at=None, completion_precision=None)
            elif action == "note":
                text = change.get("text")
                require(
                    isinstance(text, str) and text.strip(), "Nonempty note required"
                )
                r["notes"].append({"at": stamp, "actor": actor, "text": text})
            elif action == "delete":
                require(
                    isinstance(change.get("reason"), str) and change["reason"],
                    "Deletion reason required",
                )
                records.pop(rid)
                authority.pop(record_path(old))
                r = None
            else:
                raise PlanError("Unknown action")
            if r:
                r.update(revision=old["revision"] + 1, updated_at=stamp)
        if r:
            validate(r)
            records[rid] = r
            authority[record_path(r)] = encode(r)
        if before and (
            r is None
            or r["status"] in {"completed", "cancelled", "skipped"}
            or any(
                before.get(k) != r.get(k) for k in ["schedule", "due_at", "reminders"]
            )
        ):
            invalidated.add(rid)
        events.append(
            {
                "id": plan["operation_id"] + "_" + str(i),
                "record_id": rid,
                "action": action,
                "actor": actor,
                "recorded_at": stamp,
                "reason": change.get("reason"),
                "before": (
                    {
                        k: before.get(k)
                        for k in [
                            "status",
                            "schedule",
                            "due_at",
                            "original_due_at",
                            "completed_at",
                            "revision",
                        ]
                    }
                    if before
                    else None
                ),
                "after": (
                    {
                        k: r.get(k)
                        for k in [
                            "status",
                            "schedule",
                            "due_at",
                            "original_due_at",
                            "completed_at",
                            "revision",
                        ]
                    }
                    if r
                    else None
                ),
            }
        )
    validate_relations(records)
    for path, value in list(authority.items()):
        if not path.startswith(PREFIX + "integrations/"):
            continue
        intent = json.loads(value)
        if intent.get("record_id") in invalidated and intent.get("state") in {
            "queued",
            "unknown",
            "failed",
        }:
            if intent["kind"] == "notification" or intent["record_id"] not in records:
                intent.update(state="cancelled", updated_at=stamp)
                authority[path] = encode(intent)
    vault = json.loads(authority[VAULT])
    vault["revision"] += 1
    vault["updated_at"] = stamp
    authority[VAULT] = encode(vault)
    for event in events:
        authority[PREFIX + "activities/" + stamp[:7] + "/" + event["id"] + ".json"] = (
            encode(event)
        )
    authority[op_path] = encode(
        {
            "schema_version": 1,
            "id": plan["operation_id"],
            "request_sha256": signature,
            "recorded_at": stamp,
            "actor": actor,
            "changed_ids": sorted(touched),
            "deleted_ids": sorted(touched - set(records)),
        }
    )
    if split_signature is not None:
        operation = json.loads(authority[op_path])
        operation["split_request_sha256"] = split_signature
        authority[op_path] = encode(operation)
    return prepare(store, authority, "Update personal plans: " + plan["operation_id"])


def integration(store, request):
    """Prepare an external-effect intent or readback receipt; never calls a calendar/messenger."""
    authority, records = store.checked()
    ident(request.get("operation_id"))
    require(request.get("base_commit") == store.commit, "Stale integration baseline")
    stamp = request.get("recorded_at") or now()
    instant(stamp)
    kind = request.get("kind")
    require(kind in {"calendar", "notification"}, "Unknown integration kind")
    rid = request.get("record_id")
    require(
        rid in records, "Persist the task/occurrence before requesting external effects"
    )
    r = records[rid]
    require(
        not r.get("recurrence"),
        "Integration effects target a persisted task/occurrence",
    )
    payload = request.get("payload", {})
    require(isinstance(payload, dict), "Payload must be an object")
    key = digest(
        encode(
            {
                "kind": kind,
                "record_id": rid,
                "revision": r["revision"],
                "action": request.get("action"),
                "payload": payload,
            }
        )
    )
    if kind == "notification":
        key = digest(
            encode(
                {
                    "kind": kind,
                    "record_id": rid,
                    "rule_id": payload.get("rule_id"),
                    "trigger_at": payload.get("trigger_at"),
                    "channel": payload.get("channel"),
                }
            )
        )
    if request.get("intent_id"):
        require(
            isinstance(request["intent_id"], str)
            and re.fullmatch(r"[0-9a-f]{64}", request["intent_id"]),
            "Invalid intent ID",
        )
        key = request["intent_id"]
    path = PREFIX + "integrations/" + kind + "/" + key + ".json"
    existing = json.loads(authority[path]) if path in authority else None
    if existing:
        require(
            existing["record_id"] == rid and existing["kind"] == kind,
            "Intent identity mismatch",
        )
    if request.get("receipt"):
        require(
            existing is not None,
            "No matching intent; retain the original record revision/request",
        )
        receipt = request["receipt"]
        require(
            receipt.get("state") in {"confirmed", "unknown", "failed"},
            "Invalid receipt",
        )
        if receipt["state"] == "confirmed":
            require(
                receipt.get("external_id") and receipt.get("verified_at"),
                "Confirmation needs provider ID and actual readback timestamp",
            )
        if existing["state"] == "confirmed":
            return {
                "status": "already_confirmed",
                "snapshot": store.commit,
                "remote_confirmed": False,
            }
        existing.update(state=receipt["state"], receipt=receipt, updated_at=stamp)
        intent = existing
    else:
        if existing and existing["state"] != "cancelled":
            return {
                "status": "already_queued",
                "snapshot": store.commit,
                "intent": existing,
                "remote_confirmed": False,
            }
        if kind == "notification":
            require(
                r["status"] not in {"completed", "cancelled", "skipped"},
                "Terminal record cannot notify",
            )
            policy_path = PREFIX + "policies/reminders.json"
            require(
                policy_path in authority
                and json.loads(authority[policy_path]).get("enabled") is True,
                "Reminder opt-in and policy configuration required",
            )
            require(
                instant(payload.get("trigger_at"))
                and payload.get("rule_id")
                and payload.get("channel"),
                "Notification requires trigger/rule/channel",
            )
            rule = next(
                (v for v in r["reminders"] if v["id"] == payload["rule_id"]), None
            )
            require(
                rule and rule["channel"] == payload["channel"],
                "Unknown reminder rule/channel",
            )
            anchor = (
                (r.get("schedule") or {}).get("start")
                if rule["anchor"] == "start"
                else r.get("due_at")
            )
            require(
                anchor and len(anchor) > 10,
                "Timed reminders require a precise anchor; resolve a date-only window first",
            )
            expected = temporal(anchor, r["timezone"]) + dt.timedelta(
                minutes=rule["offset_minutes"]
            )
            require(
                expected == instant(payload["trigger_at"]),
                "Reminder trigger differs from current record/rule",
            )
        else:
            require(
                request.get("action") in {"upsert", "delete"},
                "Calendar action must be upsert/delete",
            )
            require(
                json.loads(authority[VAULT])["calendar"].get("mode") == "connected",
                "Calendar connection must be verified before queueing synchronization",
            )
            if request["action"] == "upsert":
                require(r.get("schedule"), "Schedule required for calendar event")
        intent = {
            "schema_version": 1,
            "id": key,
            "kind": kind,
            "record_id": rid,
            "record_revision": r["revision"],
            "action": request.get("action"),
            "payload": payload,
            "state": "queued",
            "created_at": stamp,
            "updated_at": stamp,
            "operation_id": request["operation_id"],
        }
    authority[path] = encode(intent)
    vault = json.loads(authority[VAULT])
    vault["revision"] += 1
    vault["updated_at"] = stamp
    authority[VAULT] = encode(vault)
    return prepare(store, authority, "Record integration intent/readback")


def split_series(store,request):
    authority,records=store.checked()
    ident(request.get('operation_id'))
    signature=digest(encode(request));op_path=PREFIX+'operations/'+request['operation_id']+'.json'
    if op_path in authority:
        require(json.loads(authority[op_path]).get('split_request_sha256')==signature,'Split operation ID reused')
        return {'status':'already_applied','snapshot':store.commit,'remote_confirmed':False}
    require(request.get('base_commit')==store.commit,'Stale split baseline')
    sid=request.get('series_id');old=records.get(sid)
    require(old and old.get('recurrence'),'Existing recurring task required')
    require(request.get('expected_sha256')==digest(authority[record_path(old)]),'Series hash conflict')
    cutoff=temporal(request.get('cutover'),old['timezone']);require(cutoff is not None,'Cutover required')
    require(cutoff>=temporal(old['recurrence']['dtstart'],old['timezone']),'Cutover precedes original series')
    if old['recurrence'].get('stop_before'):require(cutoff<=temporal(old['recurrence']['stop_before'],old['timezone']),'Split must not extend the historical rule')
    require(request.get('future_policy') in {'reject','cancel_open'},'Choose explicit future-instance policy')
    require(isinstance(request.get('reason'),str) and request['reason'],'Series-change reason required')
    supplied=request.get('new_record');require(isinstance(supplied,dict) and supplied.get('id') and supplied.get('id')!=sid,'New series needs a new stable ID')
    new=copy.deepcopy(old);new.update(supplied);new['notes']=[];new.update(status='pending',completed_at=None,completion_precision=None)
    require(new.get('recurrence') and new.get('schedule'),'New series recurrence and schedule required')
    first=temporal(new['recurrence']['dtstart'],new['timezone']);require(first>=cutoff,'New series begins before cutover')
    if 'due_at' not in supplied:
        require(new['timezone']==old['timezone'] or old.get('due_at') is None,'Timezone change requires an explicit new deadline')
        from recurrence import shift
        new['due_at']=shift(old.get('due_at'),temporal(old['recurrence']['dtstart'],old['timezone']),first,new['timezone'])
    new['references']=copy.deepcopy(supplied.get('references',old['references']))+[{'namespace':'plans','id':sid,'note':'replaces future instances from '+request['cutover']}]
    historical=copy.deepcopy(old['recurrence']);historical['stop_before']=request['cutover']
    changes=[{'action':'update','id':sid,'expected_sha256':request['expected_sha256'],'fields':{'recurrence':historical},'reason':request['reason']}]
    affected=[]
    for rid,r in records.items():
        if r.get('task_id')==sid and temporal(r['recurrence_id'],r['timezone'])>=cutoff and r['status'] not in {'completed','cancelled','skipped'}:
            affected.append(rid)
            require(request['future_policy']=='cancel_open','Existing future instances require an explicit cancel_open decision')
            changes.append({'action':'state','id':rid,'expected_sha256':digest(authority[record_path(r)]),'status':'cancelled','reason':request['reason']})
    changes.append({'action':'create','record':new})
    prepared=_apply(store,{'operation_id':request['operation_id'],'base_commit':store.commit,'actor':request.get('actor'),'recorded_at':request.get('recorded_at') or now(),'changes':changes},authority,records,signature)
    prepared['message']='Version recurring series at '+request['cutover']
    prepared['cancelled_future_instance_ids']=affected
    prepared['preserved_completed_instances']=True
    return prepared


def save_report(store,request):
    authority,_=store.checked();ident(request.get('operation_id'));ident(request.get('report_id'))
    signature=digest(encode(request));op_path=PREFIX+'operations/'+request['operation_id']+'.json'
    if op_path in authority:
        require(json.loads(authority[op_path])['request_sha256']==signature,'Report operation ID reused')
        return {'status':'already_applied','snapshot':store.commit,'remote_confirmed':False}
    require(request.get('base_commit')==store.commit,'Stale report baseline')
    stamp=request.get('recorded_at') or now();instant(stamp)
    require(isinstance(request.get('actor'),str) and request['actor'],'Actor required')
    narrative=request.get('narrative','');require(isinstance(narrative,str) and len(narrative)<=16000,'Report narrative exceeds budget')
    metrics=report(store,request['from'],request['to'],request['as_of'])
    path=PREFIX+'reports/'+stamp[:7]+'/'+request['report_id']+'.json'
    require(not any(json.loads(v).get('id')==request['report_id'] for p,v in authority.items() if p.startswith(PREFIX+'reports/')),'Report ID already exists')
    authority[path]=encode({'schema_version':1,'id':request['report_id'],'created_at':stamp,'actor':request['actor'],'source_snapshot':store.commit,'metrics':metrics,'narrative':narrative,'narrative_basis':'agent interpretation; metrics and evidence are calculated'})
    vault=json.loads(authority[VAULT]);vault['revision']+=1;vault['updated_at']=stamp;authority[VAULT]=encode(vault)
    authority[op_path]=encode({'schema_version':1,'id':request['operation_id'],'request_sha256':signature,'recorded_at':stamp,'report_path':path})
    return prepare(store,authority,'Archive evidence-grounded report')


def saved_reports(store,a,b):
    _,root=store.entry();months=sorted({d[:7] for d in days_between(a,b)})
    paths=[PREFIX+'indexes/reports/'+month+'/'+str(page)+'.json' for month in months for page in range(root.get('report_routes',{}).get(month,0))]
    store.need(paths)
    cards=[c for path in paths for c in store.obj(path) if a<=c['created_at'][:10]<=b]
    for c in cards:
        safe_path(c['path']);require(c['path'].startswith(PREFIX+'reports/'),'Report index points outside reports/')
    return {'status':'ok','snapshot':store.commit,'verification':'indexed_candidates','items':cards,'read_before_answer':True,'total':len(cards)}


def configure(store, request):
    authority, records = store.checked()
    ident(request.get("operation_id"))
    signature = digest(encode(request))
    op_path = PREFIX + "operations/" + request["operation_id"] + ".json"
    if op_path in authority:
        require(
            json.loads(authority[op_path])["request_sha256"] == signature,
            "Operation ID reused",
        )
        return {
            "status": "already_applied",
            "snapshot": store.commit,
            "remote_confirmed": False,
        }
    require(request.get("base_commit") == store.commit, "Stale configuration baseline")
    stamp = request.get("recorded_at") or now()
    instant(stamp)
    require(
        isinstance(request.get("actor"), str) and request["actor"], "Actor required"
    )
    kind = request.get("kind")
    value = request.get("value")
    require(isinstance(value, dict), "Configuration object required")
    vault = json.loads(authority[VAULT])
    if kind == "calendar":
        require(
            set(value) <= {"provider", "calendar_id", "mode", "verified_at"},
            "Only noncredential calendar metadata belongs in plans/",
        )
        require(value.get("mode") in {"connected", "pending"}, "Invalid calendar mode")
        if value["mode"] == "connected":
            require(
                value.get("provider")
                and value.get("calendar_id")
                and value.get("verified_at"),
                "Verified calendar identity required",
            )
            instant(value["verified_at"])
        vault["calendar"] = value
    elif kind == "holiday":
        require(
            set(value)
            == {
                "schema_version",
                "calendar_id",
                "year",
                "version",
                "source_url",
                "verified_at",
                "days",
            },
            "Invalid regional calendar schema",
        )
        require(
            value["schema_version"] == 1
            and type(value["year"]) is int
            and 1900 <= value["year"] <= 2200,
            "Invalid calendar year",
        )
        ident(value["calendar_id"])
        instant(value["verified_at"])
        from urllib.parse import urlparse

        url = urlparse(value["source_url"])
        require(
            url.scheme == "https"
            and url.hostname
            and not url.username
            and not url.password
            and value["version"],
            "Public HTTPS source/version required",
        )
        require(
            isinstance(value["days"], dict) and 0 < len(value["days"]) <= 366,
            "Calendar needs dated coverage",
        )
        for date, flags in value["days"].items():
            require(
                day(date).year == value["year"]
                and isinstance(flags, dict)
                and set(flags) == {"workday", "statutory_holiday"}
                and all(type(v) is bool for v in flags.values()),
                "Invalid regional calendar date/flags",
            )
        authority[
            PREFIX
            + "calendars/holidays/"
            + value["calendar_id"]
            + "/"
            + str(value["year"])
            + ".json"
        ] = encode(value)
    elif kind == "onboarding":
        require(
            set(value)
            == {"schema_version", "stage", "completed_steps", "reminders_enabled"},
            "Invalid onboarding fields",
        )
        require(
            value["schema_version"] == 1
            and isinstance(value["stage"], str)
            and isinstance(value["completed_steps"], list)
            and all(isinstance(v, str) for v in value["completed_steps"])
            and type(value["reminders_enabled"]) is bool,
            "Invalid onboarding state",
        )
        authority[PREFIX + "policies/onboarding.json"] = encode(value)
    elif kind == "period-note":
        from periods import append
        append(authority, records, value, stamp, request["actor"], store.commit)
    elif kind in {"cronjob", "cronjob-defaults", "cronjob-binding", "cronjob-run"}:
        from cronjobs import update
        update(authority, kind, value, stamp)
    elif kind == "reminders":
        require(
            set(value)
            == {
                "schema_version",
                "enabled",
                "consent_reference",
                "timezone",
                "quiet_start",
                "quiet_end",
                "max_per_day",
            },
            "Invalid reminder policy",
        )
        require(
            value["schema_version"] == 1
            and type(value["enabled"]) is bool
            and isinstance(value["consent_reference"], str)
            and value["consent_reference"],
            "Reminder consent reference required",
        )
        zone(value["timezone"])
        for key in ["quiet_start", "quiet_end"]:
            require(
                isinstance(value[key], str)
                and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value[key]),
                "Quiet hours use HH:MM",
            )
        require(
            type(value["max_per_day"]) is int and 1 <= value["max_per_day"] <= 100,
            "Invalid daily reminder cap",
        )
        authority[PREFIX + "policies/reminders.json"] = encode(value)
    else:
        raise PlanError("Unknown configuration kind")
    vault["revision"] += 1
    vault["updated_at"] = stamp
    authority[VAULT] = encode(vault)
    authority[op_path] = encode(
        {
            "schema_version": 1,
            "id": request["operation_id"],
            "request_sha256": signature,
            "actor": request["actor"],
            "recorded_at": stamp,
            "configuration_kind": kind,
        }
    )
    return prepare(store, authority, "Configure personal planning " + kind)


def cronjobs(store):
    _, root = store.entry()
    pages = [PREFIX+'indexes/cronjobs/'+str(i)+'.json' for i in range(root.get('cronjob_pages',0))]
    store.need(pages)
    cards = [card for page in pages for card in store.obj(page)]
    store.need([card['path'] for card in cards])
    items = []
    for card in cards:
        require(card['path'].startswith((PREFIX+'cronjobs/definitions/', PREFIX+'cronjobs/bindings/')), 'Invalid cronjob index path')
        require(digest(store.files[card['path']]) == card['sha256'], 'Cronjob differs from indexed hash')
        items.append({'path':card['path'], 'sha256':card['sha256'], 'value':store.obj(card['path'])})
    return {'status':'ok','snapshot':store.commit,'items':items,'scheduler_connected':False}


def effects(store, a, b, states):
    _, root = store.entry()
    dates = days_between(a, b)
    months = sorted({d[:7] for d in dates})
    require(
        states
        and set(states) <= {"queued", "unknown", "failed", "confirmed", "cancelled"},
        "Invalid effect state",
    )
    pages = []
    for state in states:
        for month in months:
            pages.extend(
                PREFIX
                + "indexes/effects/"
                + state
                + "/"
                + month
                + "/"
                + str(page)
                + ".json"
                for page in range(root["effect_routes"].get(state + ":" + month, 0))
            )
    store.need(pages)
    cards = [c for path in pages for c in store.obj(path) if a <= c["when"][:10] <= b]
    store.need([c["path"] for c in cards])
    items = []
    for c in cards:
        require(
            digest(store.files[c["path"]]) == c["sha256"],
            "Effect differs from indexed hash",
        )
        item = store.obj(c["path"])
        if item["state"] == "cancelled":
            items.append(item)
            continue
        r = read(store, item["record_id"])["record"]
        item["stale_record_revision"] = item["record_revision"] != r["revision"]
        item["terminal_record"] = r["status"] in {"completed", "cancelled", "skipped"}
        items.append(item)
    return {
        "status": "ok",
        "snapshot": store.commit,
        "items": items,
        "effect_executed": False,
        "runtime_must_recheck": [
            "fresh record and consent",
            "quiet hours and daily cap",
            "provider dedup/readback",
            "single active worker",
        ],
    }


def ics(store, a, b, as_of):
    result = agenda(store, a, b, as_of)
    vault, _ = store.entry()

    def escaped(value):
        return (
            value.replace("\r\n", "\n").replace("\r", "\n").replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace(";", "\\;")
            .replace(",", "\\,")
        )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Personal Plans//EN",
        "CALSCALE:GREGORIAN",
    ]
    for r in result["items"]:
        if not r.get("schedule") or r["status"] in {"cancelled", "skipped"}:
            continue
        lines += [
            "BEGIN:VEVENT",
            "UID:" + r["id"] + "." + vault["id"] + "@personal-plans",
            "DTSTAMP:"
            + instant(as_of).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "SUMMARY:" + escaped(r["title"]),
        ]
        s = r["schedule"]
        for field, key in [("start", "DTSTART"), ("end", "DTEND")]:
            value = s.get(field)
            if not value and field == "end" and len(s["start"]) == 10:
                value = (day(s["start"]) + dt.timedelta(days=1)).isoformat()
            if not value:
                continue
            lines.append(
                key
                + (
                    ";VALUE=DATE:" + value.replace("-", "")
                    if len(value) == 10
                    else ":"
                    + instant(value)
                    .astimezone(dt.timezone.utc)
                    .strftime("%Y%m%dT%H%M%SZ")
                )
            )
        lines += ["END:VEVENT"]
    lines += ["END:VCALENDAR"]
    # RFC 5545 folding counts UTF-8 octets, never splits a multibyte character.
    folded = []
    for line in lines:
        segment = ""
        size = 0
        for char in line:
            n = len(char.encode())
            if size + n > 75:
                folded.append(segment)
                segment = " "
                size = 1
            segment += char
            size += n
        folded.append(segment)
    return {
        "status": "export_prepared",
        "snapshot": store.commit,
        "content": "\r\n".join(folded) + "\r\n",
        "calendar_synced": False,
    }


def run(envelope, command, arguments):
    store = Store(envelope)
    if command == "init":
        return initialize(store, envelope.get("settings", {}))
    if command == "apply":
        return apply(store, envelope.get("plan"))
    if command == "split-series":
        return split_series(store, envelope.get("request", {}))
    if command == "save-report":
        return save_report(store, envelope.get("request", {}))
    if command == "saved-reports":
        return saved_reports(store, arguments.start, arguments.end)
    if command == "configure":
        return configure(store, envelope.get("request", {}))
    if command == "period-notes":
        from periods import read as read_period
        return read_period(store, envelope.get("query", {}))
    if command == "cronjobs":
        return cronjobs(store)
    if command == "effects":
        return effects(store, arguments.start, arguments.end, arguments.state)
    if command == "integration":
        return integration(store, envelope.get("request", {}))
    if command == "reindex":
        authority, _ = store.checked(rebuild=True)
        result = prepare(store, authority, "Compact planning indexes")
        index_paths = envelope.get("index_paths", [])
        require(isinstance(index_paths, list), "Index inventory must be a list")
        for path in index_paths:
            safe_path(path)
            require(path.startswith(GENERATED), "Reindex inventory includes authoritative data")
        kept = (set(store.files) - set(result["delete_paths"])) | set(result["files_base64"])
        result["delete_paths"] = sorted(set(result["delete_paths"]) | (set(index_paths) - kept))
        return result
    if command == "verify":
        _, records = store.checked()
        return {
            "status": "valid",
            "snapshot": store.commit,
            "record_count": len(records),
            "verification": "complete_plans_subtree",
            "remote_confirmed": False,
        }
    if command == "query":
        return query(store, envelope.get("query", {}))
    if command == "read":
        return read(store, arguments.id)
    if command == "agenda":
        return agenda(
            store, arguments.start, arguments.end, arguments.as_of, arguments.carry_over
        )
    if command == "report":
        return report(store, arguments.start, arguments.end, arguments.as_of)
    if command == "ics":
        return ics(store, arguments.start, arguments.end, arguments.as_of)
    if command == "status":
        v, r = store.entry()
        return {
            "status": "ok",
            "snapshot": store.commit,
            "vault": v,
            "index": r,
            "verification": "entrypoint_only",
            "remote_confirmed": False,
        }
    raise PlanError("Unknown command")


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stdin", required=True, action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    for name in [
        "init",
        "apply",
        "configure",
        "cronjobs",
        "period-notes",
        "split-series",
        "save-report",
        "integration",
        "query",
        "verify",
        "reindex",
        "status",
    ]:
        sub.add_parser(name)
    sub.add_parser("read").add_argument("id")
    reports = sub.add_parser("saved-reports")
    reports.add_argument("--from", dest="start", required=True)
    reports.add_argument("--to", dest="end", required=True)
    effect = sub.add_parser("effects")
    effect.add_argument("--from", dest="start", required=True)
    effect.add_argument("--to", dest="end", required=True)
    effect.add_argument("--state", action="append", required=True)
    for name in ["agenda", "report", "ics"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--from", dest="start", required=True)
        cmd.add_argument("--to", dest="end", required=True)
        cmd.add_argument("--as-of", required=True)
        if name == "agenda":
            cmd.add_argument("--carry-over", action="store_true")
    return p


def main():
    args = parser().parse_args()
    try:
        result = run(json.load(sys.stdin), args.command, args)
    except NeedFiles as e:
        result = {
            "status": "needs_files",
            "paths": e.paths,
            "same_snapshot_required": True,
        }
    except (
        PlanError,
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
        RecursionError,
    ) as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
