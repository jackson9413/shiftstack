"""Deterministic volunteer-shift scheduling and coverage diagnostics."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

DAY_INDEX = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
AVAILABILITY_RE = re.compile(
    r"^(?:(mon(?:day)?|tue(?:sday|s)?|wed(?:nesday|s)?|thu(?:rsday|s)?|fri(?:day)?|"
    r"sat(?:urday)?|sun(?:day)?|daily|everyday|\*)\s*)?"
    r"(\d{1,2}:\d{2})\s*(?:-|–|to)\s*(\d{1,2}:\d{2})$",
    re.IGNORECASE,
)
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def parse_clock(value: str) -> int:
    """Return minutes after midnight for an HH:MM string."""
    match = TIME_RE.fullmatch((value or "").strip())
    if not match:
        raise ValueError("time must use HH:MM")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("time must use HH:MM")
    return hour * 60 + minute


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError as exc:
        raise ValueError("date must use YYYY-MM-DD") from exc


def split_skills(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def role_matches(volunteer: dict, role: str) -> bool:
    """A volunteer with no stated skills can fill any role."""
    skills = split_skills(volunteer.get("skills", ""))
    if not skills:
        return True
    role_tokens = _tokens(role)
    role_text = (role or "").casefold().strip()
    for skill in skills:
        skill_text = skill.casefold().strip()
        skill_tokens = _tokens(skill)
        if skill_text == role_text or skill_text in role_text:
            return True
        if skill_tokens and skill_tokens.issubset(role_tokens):
            return True
    return False


def parse_availability(value: str) -> list[dict]:
    """Parse weekly windows such as 'Sat 09:00-17:00; Sun 10:00-14:00'."""
    entries: list[dict] = []
    for token in re.split(r"[,;\n]+", value or ""):
        token = token.strip()
        if not token:
            continue
        match = AVAILABILITY_RE.fullmatch(token)
        if not match:
            continue
        day_text, start_text, end_text = match.groups()
        try:
            start = parse_clock(start_text)
            end = parse_clock(end_text)
        except ValueError:
            continue
        if end <= start:
            continue
        if day_text is None or day_text.casefold() in {"daily", "everyday", "*"}:
            days = list(range(7))
        else:
            days = [DAY_INDEX[day_text.casefold()]]
        entries.append({"days": days, "start": start, "end": end, "raw": token})
    return entries


def availability_covers(volunteer: dict, shift: dict, shift_date: str) -> bool | None:
    """Return True/False when known, or None when availability was not declared."""
    entries = parse_availability(volunteer.get("availability", ""))
    if not entries:
        return None
    weekday = parse_iso_date(shift_date).weekday()
    shift_start = parse_clock(shift["start_time"])
    shift_end = parse_clock(shift["end_time"])
    day_entries = [entry for entry in entries if weekday in entry["days"]]
    if not day_entries:
        return False
    return any(entry["start"] <= shift_start and entry["end"] >= shift_end for entry in day_entries)


def shift_overlaps(first: dict, second: dict) -> bool:
    a_start, a_end = parse_clock(first["start_time"]), parse_clock(first["end_time"])
    b_start, b_end = parse_clock(second["start_time"]), parse_clock(second["end_time"])
    return a_start < b_end and b_start < a_end


def _week_key(iso_date: str) -> tuple[int, int]:
    parsed = parse_iso_date(iso_date)
    calendar = parsed.isocalendar()
    return int(calendar.year), int(calendar.week)


def _assignment_shift(assignment: dict, shift_by_id: dict) -> dict | None:
    shift_id = assignment.get("shift_id")
    return shift_by_id.get(shift_id)


def _assignment_hour(assignment: dict, shift_by_id: dict, shift_dates: dict) -> float:
    shift = _assignment_shift(assignment, shift_by_id)
    if not shift:
        return 0.0
    return max(0.0, (parse_clock(shift["end_time"]) - parse_clock(shift["start_time"])) / 60)


def auto_assign(
    volunteers: list[dict],
    all_shifts: list[dict],
    assignments: list[dict],
    shift_dates: dict[int, str],
    target_event_id: int,
) -> dict:
    """Greedily fill open slots while preserving existing assignments.

    Existing assignments are never removed. New assignments are deterministic:
    role match first, declared availability second, then lowest weekly load.
    """
    volunteers_by_id = {int(volunteer["id"]): volunteer for volunteer in volunteers}
    shift_by_id = {int(shift["id"]): shift for shift in all_shifts}
    assignments_by_shift: dict[int, list[dict]] = defaultdict(list)
    assignments_by_volunteer: dict[int, list[dict]] = defaultdict(list)
    weekly_hours: dict[tuple[int, tuple[int, int]], float] = defaultdict(float)
    total_hours: dict[int, float] = defaultdict(float)

    for assignment in assignments:
        shift_id = int(assignment["shift_id"])
        volunteer_id = int(assignment["volunteer_id"])
        assignments_by_shift[shift_id].append(assignment)
        assignments_by_volunteer[volunteer_id].append(assignment)
        shift = shift_by_id.get(shift_id)
        if not shift or shift_id not in shift_dates:
            continue
        hours = _assignment_hour(assignment, shift_by_id, shift_dates)
        weekly_hours[(volunteer_id, _week_key(shift_dates[shift_id]))] += hours
        total_hours[volunteer_id] += hours

    target_shifts = sorted(
        [shift for shift in all_shifts if int(shift["event_id"]) == int(target_event_id)],
        key=lambda shift: (parse_clock(shift["start_time"]), int(shift["id"])),
    )
    new_assignments: list[dict] = []
    rejections: list[dict] = []

    for shift in target_shifts:
        shift_id = int(shift["id"])
        shift_date = shift_dates[shift_id]
        shift_hours = (parse_clock(shift["end_time"]) - parse_clock(shift["start_time"])) / 60
        assigned_ids = {int(item["volunteer_id"]) for item in assignments_by_shift[shift_id]}
        open_slots = max(0, int(shift.get("needed", 1)) - len(assigned_ids))
        candidates: list[tuple[tuple, int, list[str]]] = []

        for volunteer_id, volunteer in volunteers_by_id.items():
            if volunteer_id in assigned_ids:
                continue
            reasons: list[str] = []
            availability = availability_covers(volunteer, shift, shift_date)
            if availability is False:
                reasons.append("outside declared availability")
            if not role_matches(volunteer, shift["role"]):
                reasons.append("no matching skill")
            for existing in assignments_by_volunteer.get(volunteer_id, []):
                other = _assignment_shift(existing, shift_by_id)
                if other and shift_overlaps(shift, other):
                    reasons.append("already assigned to an overlapping shift")
                    break
            week = (volunteer_id, _week_key(shift_date))
            if weekly_hours[week] + shift_hours > int(volunteer.get("max_hours_per_week", 12)):
                reasons.append("would exceed weekly hour limit")
            if reasons:
                rejections.append(
                    {
                        "shift_id": shift_id,
                        "volunteer_id": volunteer_id,
                        "volunteer": volunteer["name"],
                        "reasons": reasons,
                    }
                )
                continue
            key = (
                0 if role_matches(volunteer, shift["role"]) else 1,
                0 if availability is not None else 1,
                round(weekly_hours[week], 2),
                round(total_hours[volunteer_id], 2),
                volunteer["name"].casefold(),
            )
            candidates.append((key, volunteer_id, reasons))

        candidates.sort(key=lambda item: item[0])
        for _, volunteer_id, _ in candidates[:open_slots]:
            assignment = {"shift_id": shift_id, "volunteer_id": volunteer_id}
            new_assignments.append(assignment)
            assignments_by_shift[shift_id].append(assignment)
            assignments_by_volunteer[volunteer_id].append(assignment)
            weekly_hours[(volunteer_id, _week_key(shift_date))] += shift_hours
            total_hours[volunteer_id] += shift_hours
            assigned_ids.add(volunteer_id)

    return {
        "new_assignments": new_assignments,
        "rejections": rejections,
        "target_shift_count": len(target_shifts),
    }


def build_issues(
    volunteers: list[dict],
    selected_shifts: list[dict],
    all_shifts: list[dict],
    assignments: list[dict],
    shift_dates: dict[int, str],
) -> list[dict]:
    """Find coverage gaps and risky assignments with actionable messages."""
    volunteers_by_id = {int(volunteer["id"]): volunteer for volunteer in volunteers}
    shift_by_id = {int(shift["id"]): shift for shift in all_shifts}
    assignments_by_shift: dict[int, list[dict]] = defaultdict(list)
    assignments_by_volunteer: dict[int, list[dict]] = defaultdict(list)
    for assignment in assignments:
        assignments_by_shift[int(assignment["shift_id"])].append(assignment)
        assignments_by_volunteer[int(assignment["volunteer_id"])].append(assignment)

    issues: list[dict] = []
    weekly_hours: dict[tuple[int, tuple[int, int]], float] = defaultdict(float)
    seen_unknown: set[int] = set()

    for shift in selected_shifts:
        shift_id = int(shift["id"])
        assigned = assignments_by_shift.get(shift_id, [])
        unfilled = max(0, int(shift.get("needed", 1)) - len(assigned))
        if unfilled:
            issues.append(
                {
                    "code": "unfilled_shift",
                    "severity": "medium",
                    "message": (
                        f'{shift["role"]} at {shift["start_time"]} still needs '
                        f'{unfilled} volunteer{"s" if unfilled != 1 else ""}.'
                    ),
                    "shift_id": shift_id,
                    "volunteer_id": None,
                }
            )
        for assignment in assigned:
            volunteer = volunteers_by_id.get(int(assignment["volunteer_id"]))
            if not volunteer:
                continue
            shift_date = shift_dates.get(shift_id)
            if not shift_date:
                continue
            availability = availability_covers(volunteer, shift, shift_date)
            if availability is False:
                issues.append(
                    {
                        "code": "availability_conflict",
                        "severity": "high",
                        "message": f'{volunteer["name"]} is outside their stated availability for this shift.',
                        "shift_id": shift_id,
                        "volunteer_id": int(volunteer["id"]),
                    }
                )
            elif availability is None and int(volunteer["id"]) not in seen_unknown:
                seen_unknown.add(int(volunteer["id"]))
                issues.append(
                    {
                        "code": "availability_not_declared",
                        "severity": "info",
                        "message": f'Confirm {volunteer["name"]}\'s availability for this shift.',
                        "shift_id": shift_id,
                        "volunteer_id": int(volunteer["id"]),
                    }
                )
            if not role_matches(volunteer, shift["role"]):
                issues.append(
                    {
                        "code": "role_mismatch",
                        "severity": "medium",
                        "message": f'{volunteer["name"]} has no listed skill matching {shift["role"]}.',
                        "shift_id": shift_id,
                        "volunteer_id": int(volunteer["id"]),
                    }
                )
            shift_hours = (
                parse_clock(shift["end_time"]) - parse_clock(shift["start_time"])
            ) / 60
            weekly_hours[(int(volunteer["id"]), _week_key(shift_date))] += shift_hours

    for volunteer_id, volunteer_assignments in assignments_by_volunteer.items():
        volunteer = volunteers_by_id.get(volunteer_id)
        if not volunteer:
            continue
        seen_pairs: set[tuple[int, int]] = set()
        for index, assignment in enumerate(volunteer_assignments):
            shift = _assignment_shift(assignment, shift_by_id)
            if not shift or int(shift["event_id"]) not in {
                int(item["event_id"]) for item in selected_shifts
            }:
                continue
            for other_assignment in volunteer_assignments[index + 1 :]:
                other = _assignment_shift(other_assignment, shift_by_id)
                if not other or int(other["event_id"]) not in {
                    int(item["event_id"]) for item in selected_shifts
                }:
                    continue
                pair = tuple(sorted((int(shift["id"]), int(other["id"]))))
                if pair in seen_pairs or not shift_overlaps(shift, other):
                    continue
                seen_pairs.add(pair)
                issues.append(
                    {
                        "code": "double_booking",
                        "severity": "high",
                        "message": f'{volunteer["name"]} is booked on overlapping shifts.',
                        "shift_id": int(shift["id"]),
                        "volunteer_id": volunteer_id,
                    }
                )

    for (volunteer_id, week), hours in weekly_hours.items():
        volunteer = volunteers_by_id.get(volunteer_id)
        if not volunteer:
            continue
        limit = int(volunteer.get("max_hours_per_week", 12))
        if hours > limit:
            issues.append(
                {
                    "code": "weekly_cap_exceeded",
                    "severity": "high",
                    "message": (
                        f'{volunteer["name"]} is scheduled for {hours:.1f}h this week; '
                        f'the limit is {limit}h.'
                    ),
                    "shift_id": None,
                    "volunteer_id": volunteer_id,
                }
            )

    issues.sort(
        key=lambda issue: (
            SEVERITY_ORDER.get(issue["severity"], 9),
            issue["code"],
            issue["message"],
        )
    )
    return issues


def coverage_summary(selected_shifts: list[dict], assignments: list[dict]) -> dict:
    selected_ids = {int(shift["id"]) for shift in selected_shifts}
    total_slots = sum(max(0, int(shift.get("needed", 1))) for shift in selected_shifts)
    filled_slots = sum(
        1
        for assignment in assignments
        if int(assignment["shift_id"]) in selected_ids
    )
    unique_volunteers = {
        int(assignment["volunteer_id"])
        for assignment in assignments
        if int(assignment["shift_id"]) in selected_ids
    }
    return {
        "total_slots": total_slots,
        "filled_slots": min(filled_slots, total_slots),
        "unfilled_slots": max(0, total_slots - filled_slots),
        "coverage_percent": round((min(filled_slots, total_slots) / total_slots) * 100)
        if total_slots
        else 0,
        "volunteers_assigned": len(unique_volunteers),
    }
