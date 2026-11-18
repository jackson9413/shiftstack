# ShiftStack

**A practical volunteer shift planner for community teams.** ShiftStack turns a list of volunteers, availability windows, and event shifts into a balanced roster — then shows what still needs attention before the roster is sent.

ShiftStack is local-first: the UI, scheduling engine, and SQLite database all run on your machine. There are no accounts, API keys, analytics, or external calls.

## What it does

- Creates reusable events with a date, operating window, location, and notes.
- Records volunteer skills, weekly availability, contact details, and a weekly hour limit.
- Builds role-based shifts with the number of people needed and shift notes.
- **Auto-fills open slots** using deterministic matching:
  1. declared availability must cover the full shift;
  2. stated skills should match the role;
  3. overlapping assignments are rejected;
  4. weekly hour limits are enforced;
  5. remaining candidates are ordered by workload and name.
- Supports deliberate manual assignments and immediately flags risky choices.
- Runs a planning check for:
  - unfilled shifts;
  - assignments outside stated availability;
  - skill/role mismatches;
  - double-booked volunteers;
  - weekly hour-limit overruns.
- Shows live coverage, open slots, people assigned, issue counts, and per-volunteer workload.
- Exports the roster as CSV or an .ics calendar file.
- Includes a one-click demo with a realistic food-drive roster.

## Why it matters

Community organizers usually copy volunteer details into a spreadsheet, then discover gaps, double bookings, and unfair workloads after people have already been contacted. ShiftStack keeps the matching rules and the safety check in one place, so a coordinator can move from “I think we have enough people” to a shareable roster with an explicit list of remaining risks.

It is deliberately focused on **volunteer operations**, not payroll, donor management, or a general-purpose CRM.

## Tech stack

- **Backend:** Python 3.10+ and Flask
- **Storage:** SQLite via Python's standard-library sqlite3 module
- **Frontend:** semantic HTML, modern vanilla JavaScript, and hand-written CSS
- **Scheduling engine:** dependency-free Python
- **Tests:** Python unittest
- **External services:** none

## Quick start

~~~bash
cd ~/Desktop/OpenClaw_Daily/2026-11-17/shiftstack

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 app.py
~~~

Open [http://127.0.0.1:5000](http://127.0.0.1:5000), then click **Load demo** or create an event.

To choose another port:

~~~bash
PORT=5050 python3 app.py
~~~

The SQLite database is created at instance/shiftstack.sqlite3. Set SHIFTSTACK_DB=/path/to/database.sqlite3 to place it elsewhere.

## Run the tests

~~~bash
source .venv/bin/activate
python3 -m unittest discover -s tests -v
~~~

Expected result from this build:

~~~text
Ran 16 tests in 0.049s

OK
~~~

The suite covers availability parsing, role matching, workload balancing, overlap prevention, weekly limits, risk detection, validation errors, the full auto-assignment workflow, SQLite cascades, and CSV/ICS exports.

## How to use it

1. **Create an event.** Set the date and the window during which shifts may occur.
2. **Add volunteers.** Availability uses weekly windows such as "Sat 09:00-17:00"; separate multiple windows with semicolons. A volunteer with no skills listed can be matched to any role.
3. **Add shifts.** Each shift needs a role, start/end time, and number of people needed.
4. **Auto-fill open slots.** Existing assignments are preserved. The engine only fills remaining capacity.
5. **Review the planning check.** Manual assignments are allowed, but availability, skill, overlap, and weekly-limit risks stay visible.
6. **Export.** Download a CSV roster for coordinators or an .ics file for calendars.

### Availability syntax

~~~text
Sat 09:00-17:00
Sun 10:00-14:00
~~~

Accepted separators include semicolons, commas, and newlines. "daily 08:00-12:00" and a bare "08:00-12:00" mean every day. Overnight windows are not supported because event shifts must fit inside one daytime event window.

## API overview

All write endpoints accept JSON.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | /api/health | Liveness check |
| GET | /api/state?event_id=... | Current roster, issues, coverage, and workload |
| POST | /api/events | Create an event |
| POST | /api/volunteers | Create a volunteer |
| POST | /api/shifts | Create a shift |
| POST | /api/assignments | Add a manual assignment |
| POST | /api/auto-assign | Fill open slots automatically |
| DELETE | /api/assignments/<id> | Remove an assignment |
| DELETE | /api/shifts/<id> | Remove a shift |
| DELETE | /api/events/<id>/clear-assignments | Clear one event's roster |
| GET | /api/export.csv?event_id=... | Download a CSV roster |
| GET | /api/export.ics?event_id=... | Download calendar events |
| POST | /api/demo | Seed a realistic demo roster |

Example:

~~~bash
curl -s http://127.0.0.1:5000/api/health
~~~

~~~json
{"service":"shiftstack","status":"ok"}
~~~

## Project structure

~~~text
shiftstack/
├── app.py                 # Flask routes, validation, exports, demo seed
├── scheduler.py           # Pure matching and risk-detection engine
├── storage.py             # SQLite schema and connection helpers
├── static/
│   ├── app.js             # Dashboard rendering and API interactions
│   └── styles.css         # Responsive application styling
├── templates/
│   └── index.html         # Semantic application shell
├── tests/
│   ├── test_api.py        # API and persistence tests
│   └── test_scheduler.py  # Scheduling-engine tests
├── sample/
│   └── demo_scenario.json # Portable description of the demo scenario
├── requirements.txt
└── README.md
~~~

## Data and deployment notes

- The app binds to 127.0.0.1 by default; it is not exposed to the network.
- There is no authentication. Do not publish it to the public internet without adding an access-control layer.
- All SQL uses parameterized statements. The UI avoids injecting server-provided text as HTML.
- Data stays in the local SQLite file unless you export it.
- The scheduler is deterministic and explainable, not an opaque optimization solver. It is designed to produce a sensible roster quickly, not mathematically maximize every possible combination.

## Domain rotation

**Domain served:** community operations / volunteer scheduling (niche B2B).

A future daily build should rotate away from volunteer operations and choose a different underrepresented domain.
