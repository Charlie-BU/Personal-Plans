"""Versioned, provider-neutral personal planning records. No I/O or network."""

import datetime as dt
import hashlib
import json
import re
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PREFIX = "plans/"
ID = re.compile(r"^[a-z][a-z0-9_-]{0,95}$")
KINDS = {"goal", "milestone", "task", "occurrence"}
STATES = {
    "pending",
    "in_progress",
    "blocked",
    "completed",
    "cancelled",
    "skipped",
    "paused",
}
PRIORITIES = {
    "important_urgent",
    "important_not_urgent",
    "urgent_not_important",
    "neither",
}
PAGE_SIZE = 16
MAX_WINDOW_DAYS = 366


class PlanError(ValueError):
    pass


class NeedFiles(PlanError):
    def __init__(self, paths):
        self.paths = sorted(set(paths))
        super().__init__("More files required at the same repository commit")


def require(test, message):
    if not test:
        raise PlanError(message)


def encode(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def ident(value):
    require(isinstance(value, str) and ID.fullmatch(value), "Invalid stable ID")
    return value


def zone(name):
    require(isinstance(name, str), "IANA timezone required")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise PlanError("Unknown IANA timezone")


def day(value):
    require(
        isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value),
        "Expected YYYY-MM-DD",
    )
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise PlanError("Invalid calendar date")


def instant(value):
    require(isinstance(value, str), "Timestamp required")
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PlanError("Invalid timestamp")
    require(
        result.tzinfo is not None and result.utcoffset() is not None,
        "Timestamp needs UTC offset",
    )
    return result


def temporal(value, timezone, end_of_day=False):
    if value is None:
        return None
    if isinstance(value, str) and len(value) == 10:
        date = day(value) + dt.timedelta(days=int(end_of_day))
        result = dt.datetime.combine(date, dt.time(), zone(timezone))
        return result - dt.timedelta(microseconds=1) if end_of_day else result
    return instant(value).astimezone(zone(timezone))


def valid_schedule(schedule, timezone):
    require(
        isinstance(schedule, dict) and set(schedule) <= {"start", "end"},
        "Invalid schedule",
    )
    require(schedule.get("start"), "Schedule needs a start")
    start = temporal(schedule["start"], timezone)
    end = temporal(schedule.get("end"), timezone)
    if schedule.get("end"):
        require(
            (len(schedule["start"]) == 10) == (len(schedule["end"]) == 10),
            "Do not mix dates and timestamps in schedule",
        )
        require(end > start, "Schedule end must follow start (date end is exclusive)")
        require(
            (end - start).days <= MAX_WINDOW_DAYS,
            "Use a goal period for schedules longer than a year",
        )
    return start, end


def record_path(record):
    ident(record["id"])
    if record["kind"] == "occurrence":
        date = record["recurrence_id"][:10]
        day(date)
        return PREFIX + "occurrences/" + date[:7] + "/" + record["id"] + ".json"
    return (
        PREFIX
        + {"goal": "goals", "milestone": "goals", "task": "tasks"}[record["kind"]]
        + "/"
        + record["id"]
        + ".json"
    )


def tokens(text):
    """Exact lexical routes, with CJK unigrams/bigrams and Latin words; not semantic search."""
    text = unicodedata.normalize("NFKC", text).casefold()
    values = set(re.findall(r"[a-z0-9_]+", text))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        values.update(run)
        values.update(run[i : i + 2] for i in range(len(run) - 1))
    return values


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold()


def priority(record):
    p = record.get("priority")
    require(
        isinstance(p, dict) and set(p) == {"quadrant", "source", "reason"},
        "Priority needs quadrant, source, reason",
    )
    require(
        p["quadrant"] in PRIORITIES
        and p["source"] in {"user", "inferred", "inherited"},
        "Invalid priority",
    )
    require(
        isinstance(p["reason"], str) and 0 < len(p["reason"]) <= 1000,
        "Priority rationale required",
    )


def validate(record):
    require(isinstance(record, dict), "Record must be an object")
    common = {
        "schema_version",
        "id",
        "kind",
        "title",
        "summary",
        "body",
        "aliases",
        "tags",
        "parent_id",
        "depends_on",
        "status",
        "priority",
        "timezone",
        "schedule",
        "time_window",
        "due_at",
        "original_due_at",
        "completed_at",
        "completion_precision",
        "period",
        "recurrence",
        "task_id",
        "recurrence_id",
        "notes",
        "references",
        "reminders",
        "created_at",
        "updated_at",
        "revision",
        "measure",
    }
    require(
        not set(record) - common,
        "Unknown record fields: " + ", ".join(sorted(set(record) - common)),
    )
    require(record.get("schema_version") == 1, "Unsupported record schema")
    ident(record.get("id"))
    require(
        record.get("kind") in KINDS and record.get("status") in STATES,
        "Invalid record kind/status",
    )
    for key, maximum in [("title", 180), ("summary", 600), ("body", 16000)]:
        require(
            isinstance(record.get(key), str) and len(record[key]) <= maximum,
            "Invalid " + key,
        )
    require(
        record["title"].strip() and record["summary"].strip(),
        "Title and routing summary required",
    )
    zone(record.get("timezone"))
    priority(record)
    for key in ["aliases", "tags", "depends_on"]:
        require(
            isinstance(record.get(key), list)
            and all(isinstance(x, str) and x for x in record[key]),
            "Invalid " + key,
        )
        require(
            len(record[key]) <= 100 and len(set(record[key])) == len(record[key]),
            "Duplicate or excessive " + key,
        )
    require(record["id"] not in record["depends_on"], "Self dependency")
    for related in record["depends_on"]:
        ident(related)
    if record.get("parent_id") is not None:
        ident(record["parent_id"])
    for key in ["created_at", "updated_at"]:
        instant(record.get(key))
    require(
        instant(record["updated_at"]) >= instant(record["created_at"]),
        "Record update predates creation",
    )
    require(
        type(record.get("revision")) is int and record["revision"] >= 1,
        "Invalid revision",
    )
    for key in ["due_at", "original_due_at"]:
        temporal(record.get(key), record["timezone"], True)
    if record.get("schedule") is not None:
        valid_schedule(record["schedule"], record["timezone"])
    if record.get("time_window") is not None:
        window = record["time_window"]
        require(
            isinstance(window, dict)
            and set(window) <= {"date", "label", "estimated_minutes"},
            "Invalid time window",
        )
        day(window.get("date"))
        require(
            isinstance(window.get("label"), str) and window["label"],
            "Time-window wording required",
        )
        if "estimated_minutes" in window:
            require(
                type(window["estimated_minutes"]) is int
                and window["estimated_minutes"] > 0,
                "Invalid estimated duration",
            )
    if record.get("period") is not None:
        require(record["kind"] in {"goal", "milestone"}, "Only goals have periods")
        period = record["period"]
        require(
            isinstance(period, dict) and set(period) == {"start", "end"},
            "Period needs start/end (null permitted)",
        )
        a, b = period["start"], period["end"]
        if a:
            day(a)
        if b:
            day(b)
        require(not a or not b or a <= b, "Reversed goal period")
    if record["status"] == "skipped":
        require(record["kind"] == "occurrence", "Only an occurrence can be skipped")
    if record["status"] == "completed":
        require(
            not record.get("recurrence"),
            "Complete an occurrence, not a recurring series",
        )
        precision = record.get("completion_precision")
        require(precision in {"instant", "date"}, "Completion precision required")
        if precision == "date":
            day(record.get("completed_at"))
        else:
            instant(record.get("completed_at"))
    else:
        require(
            record.get("completed_at") is None
            and record.get("completion_precision") is None,
            "Open records cannot retain completion time",
        )
    if record["kind"] == "occurrence":
        ident(record.get("task_id"))
        temporal(record.get("recurrence_id"), record["timezone"])
        require(
            record.get("recurrence_id") is not None and not record.get("recurrence"),
            "Occurrence needs original recurrence ID, never a rule",
        )
        require(
            record.get("parent_id") is None,
            "Occurrence task link is separate from hierarchy",
        )
    else:
        require(
            record.get("task_id") is None and record.get("recurrence_id") is None,
            "Only occurrences have series identity",
        )
    require(
        isinstance(record.get("notes"), list) and len(record["notes"]) <= 500,
        "Invalid notes",
    )
    for note in record["notes"]:
        require(
            isinstance(note, dict) and set(note) == {"at", "actor", "text"},
            "Invalid note",
        )
        instant(note["at"])
        require(
            isinstance(note["text"], str)
            and 0 < len(note["text"]) <= 8000
            and isinstance(note["actor"], str)
            and note["actor"],
            "Invalid note content",
        )
    require(isinstance(record.get("references"), list), "References must be a list")
    for ref in record["references"]:
        require(
            isinstance(ref, dict)
            and set(ref) <= {"namespace", "id", "path", "snapshot", "note"}
            and ref.get("namespace")
            and ref.get("id"),
            "Typed reference requires namespace/id",
        )
        require(
            all(isinstance(v, str) and v for v in ref.values()),
            "Reference values must be strings",
        )
        if ref.get("path"):
            path = ref["path"]
            require(
                not path.startswith("/")
                and "\\" not in path
                and all(p not in {"", ".", ".."} for p in path.split("/")),
                "Unsafe reference path",
            )
    require(isinstance(record.get("reminders"), list), "Reminders must be a list")
    for rule in record["reminders"]:
        require(
            isinstance(rule, dict)
            and set(rule) == {"id", "anchor", "offset_minutes", "channel"},
            "Invalid reminder rule",
        )
        ident(rule["id"])
        require(
            rule["anchor"] in {"start", "due"}
            and type(rule["offset_minutes"]) is int
            and isinstance(rule["channel"], str)
            and rule["channel"],
            "Invalid reminder timing/channel",
        )
    require(
        len({r["id"] for r in record["reminders"]}) == len(record["reminders"]),
        "Duplicate reminder ID",
    )
    if record.get("measure") is not None:
        measure = record["measure"]
        require(
            isinstance(measure, dict)
            and set(measure) == {"unit", "target", "current", "method"},
            "Measure requires unit/target/current/method",
        )
        require(
            isinstance(measure["unit"], str)
            and measure["unit"]
            and measure["method"] in {"manual", "leaf_completion"},
            "Invalid progress measure",
        )
        require(
            measure["method"] != "leaf_completion" or measure["current"] is None,
            "Derived leaf progress is queried, not copied into current",
        )
        require(
            type(measure["target"]) in {int, float}
            and measure["target"] > 0
            and (
                measure["current"] is None
                or type(measure["current"]) in {int, float}
                and measure["current"] >= 0
            ),
            "Invalid progress values",
        )
    if record.get("recurrence"):
        require(record["kind"] == "task", "Only a task can define recurrence")
        from recurrence import validate_rule

        validate_rule(record)
    return record


def validate_relations(records):
    for record in records.values():
        if record.get("parent_id"):
            require(record["parent_id"] in records, "Missing parent")
            parent = records[record["parent_id"]]
            require(
                parent["kind"] in {"goal", "milestone", "task"}
                and not parent.get("recurrence"),
                "Invalid parent",
            )
            require(
                record["kind"] not in {"goal"} or parent["kind"] == "goal",
                "Goal parent must be a goal",
            )
        for dep in record["depends_on"]:
            require(
                dep in records and records[dep]["kind"] != "occurrence",
                "Missing or unstable dependency",
            )
        if record["kind"] == "occurrence":
            task = records.get(record["task_id"])
            require(
                task and task["kind"] == "task" and task.get("recurrence"),
                "Occurrence needs a recurring task",
            )
            from recurrence import occurrence_id

            require(
                record["id"]
                == occurrence_id(record["task_id"], record["recurrence_id"]),
                "Occurrence ID must retain original slot identity",
            )
    for edge in ["parent_id", "depends_on"]:
        done, visiting = set(), set()

        def visit(rid):
            require(rid not in visiting, "Cycle in " + edge)
            if rid in done:
                return
            visiting.add(rid)
            r = records[rid]
            neighbors = (
                ([r["parent_id"]] if r.get("parent_id") else [])
                if edge == "parent_id"
                else r["depends_on"]
            )
            for nid in neighbors:
                visit(nid)
            visiting.remove(rid)
            done.add(rid)

        for rid in records:
            visit(rid)


def timing(record, as_of, original=False):
    due = record.get("original_due_at" if original else "due_at")
    if record["status"] in {"cancelled", "skipped"}:
        return "excluded"
    if not due:
        return "no_deadline"
    deadline = temporal(due, record["timezone"], True)
    if record["status"] != "completed":
        return "overdue_unconfirmed" if instant(as_of) > deadline else "not_due"
    if record["completion_precision"] == "date":
        completed = day(record["completed_at"])
        if len(due) == 10:
            return "on_time" if completed <= day(due) else "late"
        if completed == deadline.date():
            return "unknown_precision"
        return "on_time" if completed < deadline.date() else "late"
    return "on_time" if instant(record["completed_at"]) <= deadline else "late"
