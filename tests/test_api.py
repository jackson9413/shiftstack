"""End-to-end API tests for ShiftStack."""

import tempfile
import unittest
from pathlib import Path

from app import create_app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database = Path(self.temp_dir.name) / "test.sqlite3"
        self.app = create_app(database, testing=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_event(self, name="Food Drive"):
        response = self.client.post(
            "/api/events",
            json={
                "name": name,
                "event_date": "2026-11-21",
                "start_time": "08:00",
                "end_time": "15:00",
                "location": "Community Hall",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["id"]

    def create_volunteer(self, name="Maya Chen", availability="Sat 08:00-15:00", skills="Registration, Setup"):
        response = self.client.post(
            "/api/volunteers",
            json={
                "name": name,
                "email": name.split()[0].lower() + "@example.org",
                "skills": skills,
                "availability": availability,
                "max_hours_per_week": 8,
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["id"]

    def create_shift(self, event_id, role="Registration", start_time="09:00", end_time="12:00", needed=1):
        response = self.client.post(
            "/api/shifts",
            json={
                "event_id": event_id,
                "role": role,
                "start_time": start_time,
                "end_time": end_time,
                "needed": needed,
                "notes": "Test shift",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["id"]

    def test_health_and_index(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["status"], "ok")
        index = self.client.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertIn(b"ShiftStack", index.data)

    def test_validation_returns_actionable_errors(self):
        bad_date = self.client.post(
            "/api/events",
            json={"name": "Bad", "event_date": "tomorrow", "start_time": "09:00", "end_time": "12:00"},
        )
        self.assertEqual(bad_date.status_code, 400)
        self.assertIn("YYYY-MM-DD", bad_date.get_json()["error"])

        event_id = self.create_event()
        bad_availability = self.client.post(
            "/api/volunteers",
            json={"name": "Maya", "availability": "whenever"},
        )
        self.assertEqual(bad_availability.status_code, 400)
        self.assertIn("availability", bad_availability.get_json()["error"])

        outside = self.client.post(
            "/api/shifts",
            json={
                "event_id": event_id,
                "role": "Late shift",
                "start_time": "16:00",
                "end_time": "17:00",
                "needed": 1,
            },
        )
        self.assertEqual(outside.status_code, 400)
        self.assertIn("event time window", outside.get_json()["error"])

    def test_full_auto_assignment_workflow(self):
        event_id = self.create_event()
        volunteer_id = self.create_volunteer()
        shift_id = self.create_shift(event_id)

        response = self.client.post("/api/auto-assign", json={"event_id": event_id})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["created"], 1)
        self.assertEqual(payload["state"]["summary"]["coverage_percent"], 100)
        self.assertEqual(payload["state"]["shifts"][0]["assignments"][0]["volunteer_id"], volunteer_id)

        duplicate = self.client.post(
            "/api/assignments",
            json={"shift_id": shift_id, "volunteer_id": volunteer_id},
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("fully staffed", duplicate.get_json()["error"])

    def test_manual_overlap_is_allowed_and_flagged(self):
        event_id = self.create_event()
        volunteer_id = self.create_volunteer()
        first_shift = self.create_shift(event_id, "Setup", "09:00", "11:00")
        second_shift = self.create_shift(event_id, "Registration", "10:00", "12:00")
        for shift_id in (first_shift, second_shift):
            response = self.client.post(
                "/api/assignments",
                json={"shift_id": shift_id, "volunteer_id": volunteer_id},
            )
            self.assertEqual(response.status_code, 201)
        state = self.client.get("/api/state").get_json()
        self.assertIn("double_booking", {issue["code"] for issue in state["issues"]})

    def test_demo_auto_fill_and_exports(self):
        demo = self.client.post("/api/demo")
        self.assertEqual(demo.status_code, 201)
        event_id = demo.get_json()["event_id"]
        auto = self.client.post("/api/auto-assign", json={"event_id": event_id})
        self.assertEqual(auto.status_code, 200)
        self.assertGreater(auto.get_json()["created"], 0)

        csv_response = self.client.get(f"/api/export.csv?event_id={event_id}")
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("Volunteer", csv_response.get_data(as_text=True))
        self.assertIn("Maya Chen", csv_response.get_data(as_text=True))

        ics_response = self.client.get(f"/api/export.ics?event_id={event_id}")
        self.assertEqual(ics_response.status_code, 200)
        ics = ics_response.get_data(as_text=True)
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertIn("BEGIN:VEVENT", ics)
        self.assertIn("SUMMARY:", ics)

    def test_clear_delete_and_sqlite_cascade(self):
        event_id = self.create_event()
        volunteer_id = self.create_volunteer()
        shift_id = self.create_shift(event_id)
        self.client.post(
            "/api/assignments",
            json={"shift_id": shift_id, "volunteer_id": volunteer_id},
        )
        cleared = self.client.post(f"/api/events/{event_id}/clear-assignments")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.get_json()["state"]["summary"]["filled_slots"], 0)

        deleted = self.client.delete(f"/api/events/{event_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertIsNone(deleted.get_json()["state"]["selected_event"])

    def test_missing_resources_return_404(self):
        missing_event = self.client.get("/api/state?event_id=999")
        self.assertEqual(missing_event.status_code, 404)
        missing_assignment = self.client.delete("/api/assignments/999")
        self.assertEqual(missing_assignment.status_code, 404)


if __name__ == "__main__":
    unittest.main()
