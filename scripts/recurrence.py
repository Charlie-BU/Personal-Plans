"""Bounded RFC 5545 recurrence expansion with explicit holiday/DST boundaries."""

import copy
import datetime as dt
import re
from model import PlanError, NeedFiles, require, zone, temporal, day, digest


def occurrence_id(task_id, recurrence_id):
    return "occ_" + digest((task_id + "\n" + recurrence_id).encode())[:32]


def validate_rule(task):
    r = task["recurrence"]
    require(
        isinstance(r, dict)
        and not set(r)
        - {"dtstart", "rrule", "rdates", "exdates", "day_filter", "stop_before"},
        "Invalid recurrence fields",
    )
    require(
        task.get("schedule") and r.get("dtstart") == task["schedule"]["start"],
        "Recurrence starts at the first scheduled slot",
    )
    start = temporal(r.get("dtstart"), task["timezone"])
    if r.get("stop_before"):
        temporal(r["stop_before"], task["timezone"])
    rule = r.get("rrule")
    require(
        isinstance(rule, str) and "\n" not in rule and len(rule) <= 1000,
        "Single RFC recurrence rule required",
    )
    pairs = rule.split(";")
    require(all(p.count("=") == 1 for p in pairs), "Malformed recurrence")
    fields = dict(p.split("=", 1) for p in pairs)
    require(
        len(fields) == len(pairs)
        and not set(fields)
        - {
            "FREQ",
            "INTERVAL",
            "COUNT",
            "UNTIL",
            "BYDAY",
            "BYMONTHDAY",
            "BYMONTH",
            "BYSETPOS",
            "WKST",
            "BYYEARDAY",
            "BYWEEKNO",
        },
        "Unsupported or duplicate RRULE field",
    )
    require(
        fields.get("FREQ") in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"},
        "Supported recurrence frequencies: DAILY/WEEKLY/MONTHLY/YEARLY",
    )
    require(
        not ("COUNT" in fields and "UNTIL" in fields),
        "COUNT and UNTIL are mutually exclusive",
    )
    for key in ["INTERVAL", "COUNT"]:
        if key in fields:
            require(
                fields[key].isdigit() and int(fields[key]) > 0,
                "Positive recurrence interval/count required",
            )
    for key in ["rdates", "exdates"]:
        require(
            isinstance(r.get(key, []), list) and len(r.get(key, [])) <= 1000,
            "Invalid date exceptions",
        )
        for value in r.get(key, []):
            temporal(value, task["timezone"])
            require(
                (len(value) == 10) == (len(r["dtstart"]) == 10),
                "Recurrence dates must have consistent precision",
            )
    if r.get("day_filter"):
        f = r["day_filter"]
        require(
            isinstance(f, dict) and set(f) == {"calendar_id", "kind"},
            "Day filter needs calendar_id/kind",
        )
        require(
            f["kind"] in {"workday", "restday", "statutory_holiday"},
            "Invalid regional day filter",
        )
        from model import ident

        ident(f["calendar_id"])
    try:
        from dateutil.rrule import rrulestr

        until = fields.get("UNTIL")
        if until and re.fullmatch(r"\d{8}", until):
            require(len(r["dtstart"]) == 10, "Timed UNTIL needs UTC timestamp")
            bound = dt.datetime.combine(
                dt.datetime.strptime(until, "%Y%m%d").date() + dt.timedelta(days=1),
                dt.time(),
                zone(task["timezone"]),
            ) - dt.timedelta(seconds=1)
            rule = rule.replace(
                "UNTIL=" + until,
                "UNTIL=" + bound.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            )
        return rrulestr(rule, dtstart=start)
    except ImportError:
        raise PlanError(
            "Recurrence requires python-dateutil; see scripts/requirements.txt"
        )
    except (ValueError, TypeError, OverflowError) as e:
        raise PlanError("Invalid recurrence: " + str(e))


def holiday_paths(task, start_day, end_day):
    f = task.get("recurrence", {}).get("day_filter")
    if not f:
        return []
    return [
        "plans/calendars/holidays/" + f["calendar_id"] + "/" + str(year) + ".json"
        for year in range(start_day.year, end_day.year + 1)
    ]


def shift(value, first, slot, timezone):
    if value is None:
        return None
    if len(value) == 10:
        return (day(value) + (slot.date() - first.date())).isoformat()
    # Wall-clock shift keeps a recurring local deadline stable across DST.
    local = temporal(value, timezone)
    moved = slot.replace(tzinfo=None) + (
        local.replace(tzinfo=None) - first.replace(tzinfo=None)
    )
    return moved.replace(tzinfo=zone(timezone)).isoformat()


def expand(task, start_date, end_date, holidays=None, limit=2000):
    rule = validate_rule(task)
    from dateutil.tz import datetime_exists, datetime_ambiguous

    a, b = day(start_date), day(end_date)
    require(
        a <= b and (b - a).days <= 366, "Recurrence query window must be 0–366 days"
    )
    r = task["recurrence"]
    tz = zone(task["timezone"])
    first = temporal(r["dtstart"], task["timezone"])
    low = dt.datetime.combine(a, dt.time(), tz)
    high = dt.datetime.combine(b + dt.timedelta(days=1), dt.time(), tz)
    try:
        slots = []
        for slot in rule.xafter(low, count=limit + 1, inc=True):
            if slot >= high:
                break
            slots.append(slot)
            require(len(slots) <= limit, "Too many occurrences; narrow the window")
    except (ValueError, TypeError, OverflowError) as e:
        raise PlanError("Invalid recurrence expansion: " + str(e))
    slots.extend(
        temporal(v, task["timezone"])
        for v in r.get("rdates", [])
        if low <= temporal(v, task["timezone"]) < high
    )
    explicit = {temporal(v, task["timezone"]).isoformat() for v in r.get("rdates", [])}
    excluded = {temporal(v, task["timezone"]).isoformat() for v in r.get("exdates", [])}
    slots = sorted(
        {s.isoformat(): s for s in slots if s.isoformat() not in excluded}.values()
    )
    require(len(slots) <= limit, "Too many recurrence exceptions")
    result = []
    f = r.get("day_filter")
    for slot in slots:
        if r.get("stop_before") and slot >= temporal(
            r["stop_before"], task["timezone"]
        ):
            continue
        require(
            datetime_exists(slot)
            and (not datetime_ambiguous(slot) or slot.isoformat() in explicit),
            "DST slot is nonexistent/ambiguous; add an explicit exception with offset",
        )
        if f:
            calendar = (holidays or {}).get(slot.year)
            if calendar is None:
                raise NeedFiles(
                    [
                        "plans/calendars/holidays/"
                        + f["calendar_id"]
                        + "/"
                        + str(slot.year)
                        + ".json"
                    ]
                )
            require(
                calendar
                and calendar.get("calendar_id") == f["calendar_id"]
                and calendar.get("year") == slot.year
                and calendar.get("source_url")
                and calendar.get("version"),
                "Verified regional calendar required",
            )
            value = calendar.get("days", {}).get(slot.date().isoformat())
            require(
                isinstance(value, dict)
                and type(value.get("workday")) is bool
                and type(value.get("statutory_holiday")) is bool,
                "Regional calendar does not cover " + slot.date().isoformat(),
            )
            if not (
                value["workday"]
                if f["kind"] == "workday"
                else (
                    not value["workday"]
                    if f["kind"] == "restday"
                    else value["statutory_holiday"]
                )
            ):
                continue
        rid = slot.date().isoformat() if len(r["dtstart"]) == 10 else slot.isoformat()
        item = copy.deepcopy(task)
        item.update(
            id=occurrence_id(task["id"], rid),
            kind="occurrence",
            task_id=task["id"],
            recurrence_id=rid,
            parent_id=None,
            recurrence=None,
            status="pending",
            completed_at=None,
            completion_precision=None,
        )
        item["schedule"] = {
            k: shift(v, first, slot, task["timezone"])
            for k, v in task["schedule"].items()
        }
        item["due_at"] = shift(task.get("due_at"), first, slot, task["timezone"])
        item["original_due_at"] = shift(
            task.get("original_due_at"), first, slot, task["timezone"]
        )
        item["virtual"] = True
        result.append(item)
    return result
