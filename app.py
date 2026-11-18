"""ShiftStack: a practical volunteer shift planner for community teams."""

from __future__ import annotations

import csv
import io
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, Response, g, jsonify, render_template, request

from scheduler import (
    availability_covers,
    build_issues,
    coverage_summary,
    parse_availability,
    parse_clock,
    parse_iso_date,
)
from storage import init_database, rows_as_dicts


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def create_app(database_path: str | Path | None = None, testing: bool = False) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    if database_path is None:
        database_path = os.environ.get(
            "SHIFTSTACK_DB", str(Path(app.instance_path) / "shiftstack.sqlite3")
        )
    app.config.update(
        DATABASE=str(database_path),
        TESTING=testing,
        JSON_SORT_KEYS=False,
    )
    init_database(app.config["DATABASE"])

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            connection = sqlite3.connect(app.config["DATABASE"])
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            g.db = connection
        return g.db

    @app.teardown_appcontext
    def close_db(_error: BaseException | None) -> None:
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    def body() -> dict:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ApiError("request body must be a JSON object")
        return payload

    def text_value(payload: dict, key: str, *, required: bool = False, limit: int = 500) -> str:
        value = str(payload.get(key, "") or "").strip()
        if required and not value:
            raise ApiError(f"{key.replace('_', ' ')} is required")
        if len(value) > limit:
            raise ApiError(f"{key.replace('_', ' ')} is too long")
        return value

    def integer_value(
        payload: dict,
        key: str,
        *,
        default: int | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        raw = payload.get(key, default)
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ApiError(f"{key.replace('_', ' ')} must be a whole number") from exc
        if minimum is not None and value < minimum:
            raise ApiError(f"{key.replace('_', ' ')} must be at least {minimum}")
        if maximum is not None and value > maximum:
            raise ApiError(f"{key.replace('_', ' ')} must be at most {maximum}")
        return value

    def validate_date(value: str) -> str:
        try:
            parse_iso_date(value)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc
        return value

    def validate_times(start_time: str, end_time: str) -> tuple[str, str]:
        try:
            start = parse_clock(start_time)
            end = parse_clock(end_time)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc
        if end <= start:
            raise ApiError("end time must be after start time")
        return start_time, end_time

    def validate_availability(value: str) -> str:
        if value and not parse_availability(value):
            raise ApiError(
                "availability should use weekly windows such as 'Sat 09:00-17:00'",
            )
        return value

    def validate_email(value: str) -> str:
        if value and ("@" not in value or any(char.isspace() for char in value)):
            raise ApiError("email must be a valid address")
        return value

    def event_rows() -> list[dict]:
        return rows_as_dicts(
            get_db()
            .execute("SELECT * FROM events ORDER BY event_date DESC, start_time, id")
            .fetchall()
        )

    def volunteer_rows() -> list[dict]:
        return rows_as_dicts(
            get_db()
            .execute("SELECT * FROM volunteers ORDER BY name COLLATE NOCASE, id")
            .fetchall()
        )

    def all_shift_rows() -> list[dict]:
        return rows_as_dicts(
            get_db()
            .execute(
                """
                SELECT s.*, e.event_date
                FROM shifts s
                JOIN events e ON e.id = s.event_id
                ORDER BY e.event_date, s.start_time, s.id
                """
            )
            .fetchall()
        )

    def all_assignment_rows() -> list[dict]:
        return rows_as_dicts(
            get_db()
            .execute(
                """
                SELECT a.id, a.shift_id, a.volunteer_id, a.created_at
                FROM assignments a
                ORDER BY a.id
                """
            )
            .fetchall()
        )

    def shift_dates() -> dict[int, str]:
        return {int(row["id"]): row["event_date"] for row in all_shift_rows()}

    def selected_event(event_id: int | None) -> dict | None:
        events = event_rows()
        if event_id is not None and not events:
            raise ApiError("event not found", 404)
        if not events:
            return None
        if event_id is None:
            return events[0]
        for event in events:
            if int(event["id"]) == event_id:
                return event
        raise ApiError("event not found", 404)

    def state_payload(event_id: int | None = None) -> dict:
        connection = get_db()
        events = event_rows()
        chosen = selected_event(event_id)
        volunteers = volunteer_rows()
        all_shifts = all_shift_rows()
        assignments = all_assignment_rows()
        dates = {int(row["id"]): row["event_date"] for row in all_shifts}

        selected_shifts = (
            [row for row in all_shifts if int(row["event_id"]) == int(chosen["id"])]
            if chosen
            else []
        )
        assignments_by_shift: dict[int, list[dict]] = {}
        volunteer_by_id = {int(volunteer["id"]): volunteer for volunteer in volunteers}
        for assignment in assignments:
            assignments_by_shift.setdefault(int(assignment["shift_id"]), []).append(assignment)

        enriched_shifts: list[dict] = []
        workload: dict[int, float] = {}
        for shift in selected_shifts:
            shift_assignments: list[dict] = []
            for assignment in assignments_by_shift.get(int(shift["id"]), []):
                volunteer = volunteer_by_id.get(int(assignment["volunteer_id"]), {})
                shift_assignments.append(
                    {
                        "id": int(assignment["id"]),
                        "volunteer_id": int(assignment["volunteer_id"]),
                        "volunteer_name": volunteer.get("name", "Unknown volunteer"),
                        "volunteer_email": volunteer.get("email", ""),
                        "volunteer_skills": volunteer.get("skills", ""),
                    }
                )
                hours = (
                    parse_clock(shift["end_time"]) - parse_clock(shift["start_time"])
                ) / 60
                workload[int(assignment["volunteer_id"])] = (
                    workload.get(int(assignment["volunteer_id"]), 0) + hours
                )
            enriched = dict(shift)
            enriched["assignments"] = shift_assignments
            enriched["filled"] = len(shift_assignments)
            enriched["unfilled"] = max(0, int(shift["needed"]) - len(shift_assignments))
            enriched_shifts.append(enriched)

        event_stats: list[dict] = []
        for event in events:
            event_id_value = int(event["id"])
            event_shifts = [
                shift for shift in all_shifts if int(shift["event_id"]) == event_id_value
            ]
            event_shift_ids = {int(shift["id"]) for shift in event_shifts}
            slots = sum(int(shift["needed"]) for shift in event_shifts)
            filled = sum(
                1
                for assignment in assignments
                if int(assignment["shift_id"]) in event_shift_ids
            )
            stat = dict(event)
            stat["shift_count"] = len(event_shifts)
            stat["slot_count"] = slots
            stat["filled_count"] = min(filled, slots)
            event_stats.append(stat)

        issues = build_issues(
            volunteers,
            selected_shifts,
            all_shifts,
            assignments,
            dates,
        )
        summary = coverage_summary(selected_shifts, assignments)
        summary["issue_count"] = len(issues)
        summary["high_issue_count"] = sum(
            1 for issue in issues if issue["severity"] == "high"
        )

        volunteer_workload = []
        for volunteer in volunteers:
            volunteer_id = int(volunteer["id"])
            volunteer_workload.append(
                {
                    "volunteer_id": volunteer_id,
                    "name": volunteer["name"],
                    "hours": round(workload.get(volunteer_id, 0), 1),
                    "limit": int(volunteer["max_hours_per_week"]),
                }
            )

        return {
            "events": event_stats,
            "volunteers": volunteers,
            "selected_event": chosen,
            "shifts": enriched_shifts,
            "issues": issues,
            "summary": summary,
            "workload": volunteer_workload,
        }

    def parse_event_id(value: object) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ApiError("event_id must be a whole number") from exc

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return jsonify({"error": error.message}), error.status

    @app.errorhandler(404)
    def handle_not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "route not found"}), 404
        return render_template("index.html"), 404

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "shiftstack"})

    @app.get("/api/state")
    def get_state():
        event_id = parse_event_id(request.args.get("event_id"))
        return jsonify(state_payload(event_id))

    @app.post("/api/events")
    def create_event():
        payload = body()
        name = text_value(payload, "name", required=True, limit=120)
        event_date = validate_date(text_value(payload, "event_date", required=True, limit=10))
        start_time, end_time = validate_times(
            text_value(payload, "start_time", required=True, limit=5),
            text_value(payload, "end_time", required=True, limit=5),
        )
        location = text_value(payload, "location", limit=160)
        notes = text_value(payload, "notes", limit=1000)
        connection = get_db()
        cursor = connection.execute(
            """
            INSERT INTO events(name, event_date, start_time, end_time, location, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, event_date, start_time, end_time, location, notes),
        )
        connection.commit()
        return jsonify({"id": cursor.lastrowid, "state": state_payload(cursor.lastrowid)}), 201

    @app.delete("/api/events/<int:event_id>")
    def delete_event(event_id: int):
        connection = get_db()
        if not connection.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone():
            raise ApiError("event not found", 404)
        connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
        connection.commit()
        return jsonify({"deleted": event_id, "state": state_payload()})

    @app.post("/api/volunteers")
    def create_volunteer():
        payload = body()
        name = text_value(payload, "name", required=True, limit=120)
        email = validate_email(text_value(payload, "email", limit=254))
        phone = text_value(payload, "phone", limit=40)
        skills = text_value(payload, "skills", limit=500)
        availability = validate_availability(text_value(payload, "availability", limit=1000))
        max_hours = integer_value(
            payload,
            "max_hours_per_week",
            default=12,
            minimum=1,
            maximum=60,
        )
        connection = get_db()
        cursor = connection.execute(
            """
            INSERT INTO volunteers(
                name, email, phone, skills, availability, max_hours_per_week
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, email, phone, skills, availability, max_hours),
        )
        connection.commit()
        return jsonify({"id": cursor.lastrowid, "state": state_payload()}), 201

    @app.delete("/api/volunteers/<int:volunteer_id>")
    def delete_volunteer(volunteer_id: int):
        connection = get_db()
        if not connection.execute(
            "SELECT 1 FROM volunteers WHERE id = ?", (volunteer_id,)
        ).fetchone():
            raise ApiError("volunteer not found", 404)
        connection.execute("DELETE FROM volunteers WHERE id = ?", (volunteer_id,))
        connection.commit()
        return jsonify({"deleted": volunteer_id, "state": state_payload()})

    @app.post("/api/shifts")
    def create_shift():
        payload = body()
        event_id = integer_value(payload, "event_id", minimum=1)
        role = text_value(payload, "role", required=True, limit=100)
        start_time, end_time = validate_times(
            text_value(payload, "start_time", required=True, limit=5),
            text_value(payload, "end_time", required=True, limit=5),
        )
        needed = integer_value(payload, "needed", default=1, minimum=1, maximum=50)
        notes = text_value(payload, "notes", limit=500)
        connection = get_db()
        event = connection.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        if not event:
            raise ApiError("event not found", 404)
        if parse_clock(start_time) < parse_clock(event["start_time"]) or parse_clock(
            end_time
        ) > parse_clock(event["end_time"]):
            raise ApiError("shift must fit inside the event time window")
        cursor = connection.execute(
            """
            INSERT INTO shifts(event_id, role, start_time, end_time, needed, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, role, start_time, end_time, needed, notes),
        )
        connection.commit()
        return jsonify({"id": cursor.lastrowid, "state": state_payload(event_id)}), 201

    @app.delete("/api/shifts/<int:shift_id>")
    def delete_shift(shift_id: int):
        connection = get_db()
        row = connection.execute("SELECT event_id FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        if not row:
            raise ApiError("shift not found", 404)
        connection.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))
        connection.commit()
        return jsonify({"deleted": shift_id, "state": state_payload(int(row["event_id"]))})

    @app.post("/api/assignments")
    def create_assignment():
        payload = body()
        shift_id = integer_value(payload, "shift_id", minimum=1)
        volunteer_id = integer_value(payload, "volunteer_id", minimum=1)
        connection = get_db()
        shift = connection.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        volunteer = connection.execute(
            "SELECT * FROM volunteers WHERE id = ?", (volunteer_id,)
        ).fetchone()
        if not shift:
            raise ApiError("shift not found", 404)
        if not volunteer:
            raise ApiError("volunteer not found", 404)
        count = connection.execute(
            "SELECT COUNT(*) FROM assignments WHERE shift_id = ?", (shift_id,)
        ).fetchone()[0]
        if count >= int(shift["needed"]):
            raise ApiError("this shift is already fully staffed", 409)
        duplicate = connection.execute(
            "SELECT 1 FROM assignments WHERE shift_id = ? AND volunteer_id = ?",
            (shift_id, volunteer_id),
        ).fetchone()
        if duplicate:
            raise ApiError("volunteer is already assigned to this shift", 409)
        cursor = connection.execute(
            "INSERT INTO assignments(shift_id, volunteer_id) VALUES (?, ?)",
            (shift_id, volunteer_id),
        )
        connection.commit()
        return jsonify(
            {"id": cursor.lastrowid, "state": state_payload(int(shift["event_id"]))}
        ), 201

    @app.delete("/api/assignments/<int:assignment_id>")
    def delete_assignment(assignment_id: int):
        connection = get_db()
        row = connection.execute(
            """
            SELECT a.id, s.event_id
            FROM assignments a
            JOIN shifts s ON s.id = a.shift_id
            WHERE a.id = ?
            """,
            (assignment_id,),
        ).fetchone()
        if not row:
            raise ApiError("assignment not found", 404)
        connection.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
        connection.commit()
        return jsonify({"deleted": assignment_id, "state": state_payload(int(row["event_id"]))})

    @app.post("/api/auto-assign")
    def run_auto_assign():
        payload = request.get_json(silent=True) or {}
        event_id = parse_event_id(payload.get("event_id"))
        chosen = selected_event(event_id)
        if not chosen:
            raise ApiError("create an event and at least one shift first")
        volunteers = volunteer_rows()
        shifts = all_shift_rows()
        assignments = all_assignment_rows()
        dates = {int(row["id"]): row["event_date"] for row in shifts}
        result = auto_assign_engine(
            volunteers, shifts, assignments, dates, int(chosen["id"])
        )
        connection = get_db()
        created = 0
        for assignment in result["new_assignments"]:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO assignments(shift_id, volunteer_id)
                VALUES (?, ?)
                """,
                (assignment["shift_id"], assignment["volunteer_id"]),
            )
            created += cursor.rowcount
        connection.commit()
        return jsonify(
            {
                "created": created,
                "rejections": result["rejections"],
                "state": state_payload(int(chosen["id"])),
            }
        )

    @app.post("/api/events/<int:event_id>/clear-assignments")
    def clear_assignments(event_id: int):
        connection = get_db()
        if not connection.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone():
            raise ApiError("event not found", 404)
        connection.execute(
            """
            DELETE FROM assignments
            WHERE shift_id IN (SELECT id FROM shifts WHERE event_id = ?)
            """,
            (event_id,),
        )
        connection.commit()
        return jsonify({"cleared": True, "state": state_payload(event_id)})

    @app.post("/api/demo")
    def load_demo():
        connection = get_db()
        if connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]:
            raise ApiError("demo data can only be loaded into an empty workspace")
        today = date.today()
        days_until_saturday = (5 - today.weekday()) % 7
        if days_until_saturday == 0:
            days_until_saturday = 7
        event_date = (today + timedelta(days=days_until_saturday)).isoformat()
        cursor = connection.execute(
            """
            INSERT INTO events(name, event_date, start_time, end_time, location, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "Neighborhood Food Drive",
                event_date,
                "08:00",
                "15:00",
                "Eastside Community Hall",
                "Demo event with a realistic mix of available, limited, and open shifts.",
            ),
        )
        event_id = int(cursor.lastrowid)
        volunteer_seed = [
            (
                "Maya Chen",
                "maya@example.org",
                "Registration, Setup",
                "Sat 08:00-15:00",
                6,
            ),
            (
                "Luis Rivera",
                "luis@example.org",
                "Food service, Logistics",
                "Sat 09:00-15:00",
                6,
            ),
            (
                "Priya Shah",
                "priya@example.org",
                "Registration",
                "Sat 10:00-14:00",
                4,
            ),
            (
                "Jordan Brooks",
                "jordan@example.org",
                "Setup, Logistics",
                "Sat 08:00-12:00",
                4,
            ),
            (
                "Sam Wilson",
                "sam@example.org",
                "",
                "Sat 08:00-15:00",
                8,
            ),
        ]
        for volunteer in volunteer_seed:
            connection.execute(
                """
                INSERT INTO volunteers(
                    name, email, skills, availability, max_hours_per_week
                ) VALUES (?, ?, ?, ?, ?)
                """,
                volunteer,
            )
        shift_seed = [
            ("Setup", "08:00", "09:00", 2, "Tables, signs, and boxes"),
            ("Registration", "09:00", "12:00", 2, "Welcome and check-in desk"),
            ("Food service", "11:00", "14:00", 2, "Pack and hand out food boxes"),
            ("Logistics", "13:00", "15:00", 1, "Pickups and leftover inventory"),
        ]
        for shift in shift_seed:
            connection.execute(
                """
                INSERT INTO shifts(event_id, role, start_time, end_time, needed, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, *shift),
            )
        connection.commit()
        return jsonify({"event_id": event_id, "state": state_payload(event_id)}), 201

    @app.get("/api/export.csv")
    def export_csv():
        event_id = parse_event_id(request.args.get("event_id"))
        chosen = selected_event(event_id)
        if not chosen:
            raise ApiError("event not found", 404)
        connection = get_db()
        rows = connection.execute(
            """
            SELECT e.name AS event, e.event_date, e.location, s.role, s.start_time,
                   s.end_time, s.needed, v.name AS volunteer_name, v.email, v.skills
            FROM shifts s
            JOIN events e ON e.id = s.event_id
            LEFT JOIN assignments a ON a.shift_id = s.id
            LEFT JOIN volunteers v ON v.id = a.volunteer_id
            WHERE s.event_id = ?
            ORDER BY s.start_time, s.role, v.name
            """,
            (chosen["id"],),
        ).fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Event",
                "Date",
                "Location",
                "Role",
                "Start",
                "End",
                "Needed",
                "Volunteer",
                "Email",
                "Skills",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["event"],
                    row["event_date"],
                    row["location"],
                    row["role"],
                    row["start_time"],
                    row["end_time"],
                    row["needed"],
                    row["volunteer_name"] if "volunteer_name" in row.keys() else "",
                    row["email"] if "email" in row.keys() else "",
                    row["skills"] if "skills" in row.keys() else "",
                ]
            )
        filename = "".join(
            char if char.isalnum() or char in "-_" else "-"
            for char in chosen["name"].lower()
        ).strip("-")
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename or "shiftstack"}-roster.csv"'},
        )

    @app.get("/api/export.ics")
    def export_ics():
        event_id = parse_event_id(request.args.get("event_id"))
        chosen = selected_event(event_id)
        if not chosen:
            raise ApiError("event not found", 404)
        connection = get_db()
        rows = connection.execute(
            """
            SELECT s.*, v.id AS volunteer_id, v.name AS volunteer_name, v.email AS volunteer_email
            FROM shifts s
            LEFT JOIN assignments a ON a.shift_id = s.id
            LEFT JOIN volunteers v ON v.id = a.volunteer_id
            WHERE s.event_id = ?
            ORDER BY s.start_time, s.role, v.name
            """,
            (chosen["id"],),
        ).fetchall()
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//ShiftStack//Volunteer Roster//EN",
            "CALSCALE:GREGORIAN",
        ]

        def ics_escape(value: str) -> str:
            return (
                (value or "")
                .replace("\\", "\\\\")
                .replace(";", "\\;")
                .replace(",", "\\,")
                .replace("\n", "\\n")
            )

        def compact_date(value: str) -> str:
            return value.replace("-", "")

        def compact_time(value: str) -> str:
            return value.replace(":", "") + "00"

        for row in rows:
            attendee = ""
            if row["volunteer_name"]:
                attendee = (
                    f"ATTENDEE;CN={ics_escape(row['volunteer_name'])}:"
                    f"mailto:{row['volunteer_email'] or 'volunteer@unknown.invalid'}"
                )
            suffix = row["volunteer_name"] or "Open slot"
            uid = f"shift-{row['id']}-vol-{row['volunteer_id'] or 0}@shiftstack.local"
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{compact_date(chosen['event_date'])}T000000Z",
                    f"DTSTART:{compact_date(chosen['event_date'])}T{compact_time(row['start_time'])}",
                    f"DTEND:{compact_date(chosen['event_date'])}T{compact_time(row['end_time'])}",
                    f"SUMMARY:{ics_escape(row['role'] + ' — ' + suffix)}",
                    f"LOCATION:{ics_escape(chosen['location'])}",
                    f"DESCRIPTION:{ics_escape(row['notes'] or chosen['notes'])}",
                    attendee,
                    "END:VEVENT",
                ]
            )
        lines.append("END:VCALENDAR")
        payload = "\r\n".join(line for line in lines if line) + "\r\n"
        return Response(
            payload,
            mimetype="text/calendar",
            headers={"Content-Disposition": 'attachment; filename="shiftstack-roster.ics"'},
        )

    return app


def auto_assign_engine(
    volunteers: list[dict],
    shifts: list[dict],
    assignments: list[dict],
    dates: dict[int, str],
    event_id: int,
) -> dict:
    # Imported lazily to keep module import side effects minimal.
    from scheduler import auto_assign

    return auto_assign(volunteers, shifts, assignments, dates, event_id)


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
