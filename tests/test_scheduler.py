"""Unit tests for ShiftStack's scheduling and risk engine."""

import unittest

from scheduler import (
    auto_assign,
    availability_covers,
    build_issues,
    coverage_summary,
    parse_availability,
    role_matches,
    shift_overlaps,
)


SATURDAY = "2026-11-21"


def volunteer(volunteer_id, name, skills="", availability="Sat 09:00-13:00", limit=8):
    return {
        "id": volunteer_id,
        "name": name,
        "skills": skills,
        "availability": availability,
        "max_hours_per_week": limit,
    }


def shift(shift_id, role, start_time, end_time, needed=1, event_id=1):
    return {
        "id": shift_id,
        "event_id": event_id,
        "role": role,
        "start_time": start_time,
        "end_time": end_time,
        "needed": needed,
    }


class SchedulerTests(unittest.TestCase):
    def test_parses_named_and_daily_availability_windows(self):
        entries = parse_availability("Sat 09:00-17:00; Sun 10:00-14:00; daily 06:00-08:00")
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["days"], [5])
        self.assertEqual(entries[1]["days"], [6])
        self.assertEqual(entries[2]["days"], list(range(7)))

    def test_availability_can_be_known_or_unknown(self):
        current = volunteer(1, "Ava", availability="Sat 09:00-12:00")
        missing = volunteer(2, "Ben", availability="")
        target = shift(10, "Setup", "10:00", "11:00")
        self.assertTrue(availability_covers(current, target, SATURDAY))
        self.assertIsNone(availability_covers(missing, target, SATURDAY))
        self.assertFalse(
            availability_covers(
                volunteer(3, "Cam", availability="Sat 11:00-12:00"),
                target,
                SATURDAY,
            )
        )

    def test_role_match_accepts_empty_skill_lists_and_token_overlap(self):
        self.assertTrue(role_matches(volunteer(1, "Ava", skills=""), "Registration"))
        self.assertTrue(role_matches(volunteer(2, "Ben", skills="Registration"), "Registration desk"))
        self.assertTrue(role_matches(volunteer(3, "Cam", skills="food service"), "Food service"))
        self.assertFalse(role_matches(volunteer(4, "Dee", skills="Logistics"), "Registration"))

    def test_auto_assign_prioritizes_roles_then_lowest_load(self):
        people = [
            volunteer(1, "Ava", skills="Registration"),
            volunteer(2, "Ben", skills="Setup"),
            volunteer(3, "Cam", skills="Registration"),
        ]
        shifts = [
            shift(1, "Setup", "09:00", "11:00", needed=1),
            shift(2, "Registration", "09:00", "11:00", needed=1),
        ]
        dates = {1: SATURDAY, 2: SATURDAY}
        result = auto_assign(people, shifts, [], dates, 1)
        pairs = {(item["shift_id"], item["volunteer_id"]) for item in result["new_assignments"]}
        self.assertEqual(pairs, {(1, 2), (2, 1)})

    def test_auto_assign_preserves_existing_assignments_and_fills_only_open_slots(self):
        people = [volunteer(1, "Ava"), volunteer(2, "Ben")]
        shifts = [shift(1, "Setup", "09:00", "11:00", needed=2)]
        existing = [{"id": 9, "shift_id": 1, "volunteer_id": 1}]
        result = auto_assign(people, shifts, existing, {1: SATURDAY}, 1)
        self.assertEqual(len(result["new_assignments"]), 1)
        self.assertEqual(result["new_assignments"][0]["volunteer_id"], 2)

    def test_auto_assign_rejects_overlap_and_weekly_cap(self):
        people = [volunteer(1, "Ava", limit=2)]
        shifts = [
            shift(1, "Setup", "09:00", "11:00", needed=1),
            shift(2, "Setup", "10:00", "12:00", needed=1),
        ]
        dates = {1: SATURDAY, 2: SATURDAY}
        result = auto_assign(people, shifts, [], dates, 1)
        self.assertEqual(len(result["new_assignments"]), 1)
        reasons = " ".join(
            reason
            for rejection in result["rejections"]
            for reason in rejection["reasons"]
        )
        self.assertIn("overlapping shift", reasons)
        self.assertIn("weekly hour limit", reasons)

    def test_build_issues_reports_gaps_and_manual_assignment_risks(self):
        people = [
            volunteer(
                1,
                "Ava",
                skills="Food service",
                availability="Sat 12:00-14:00",
                limit=1,
            )
        ]
        shifts = [shift(1, "Registration", "09:00", "11:00", needed=2)]
        assignments = [{"id": 1, "shift_id": 1, "volunteer_id": 1}]
        issues = build_issues(people, shifts, shifts, assignments, {1: SATURDAY})
        codes = {issue["code"] for issue in issues}
        self.assertIn("unfilled_shift", codes)
        self.assertIn("availability_conflict", codes)
        self.assertIn("role_mismatch", codes)
        self.assertIn("weekly_cap_exceeded", codes)

    def test_build_issues_detects_double_booking(self):
        people = [volunteer(1, "Ava", availability="Sat 09:00-13:00", limit=8)]
        shifts = [
            shift(1, "Setup", "09:00", "11:00", needed=1),
            shift(2, "Registration", "10:00", "12:00", needed=1),
        ]
        assignments = [
            {"id": 1, "shift_id": 1, "volunteer_id": 1},
            {"id": 2, "shift_id": 2, "volunteer_id": 1},
        ]
        issues = build_issues(people, shifts, shifts, assignments, {1: SATURDAY, 2: SATURDAY})
        self.assertIn("double_booking", {issue["code"] for issue in issues})

    def test_overlap_and_coverage_math(self):
        first = shift(1, "Setup", "09:00", "11:00", needed=1)
        second = shift(2, "Registration", "10:30", "12:00", needed=2)
        self.assertTrue(shift_overlaps(first, second))
        summary = coverage_summary([first, second], [{"shift_id": 1, "volunteer_id": 1}])
        self.assertEqual(summary["total_slots"], 3)
        self.assertEqual(summary["filled_slots"], 1)
        self.assertEqual(summary["unfilled_slots"], 2)
        self.assertEqual(summary["coverage_percent"], 33)


if __name__ == "__main__":
    unittest.main()
