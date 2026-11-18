"use strict";

let appState = {
  events: [],
  volunteers: [],
  selected_event: null,
  shifts: [],
  issues: [],
  summary: {},
  workload: [],
};
let activeShiftId = null;
let toastTimer = null;

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

async function api(path, options = {}) {
  const config = Object.assign({}, options);
  config.headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  const response = await fetch(path, config);
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.error || "The request could not be completed");
  }
  return payload;
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("show"), 3200);
}

function formatDate(value, options = {}) {
  if (!value) return "Date not set";
  const parsed = new Date(value + "T12:00:00");
  return parsed.toLocaleDateString(undefined, Object.assign({
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  }, options));
}

function formatTime(value) {
  if (!value) return "";
  const parts = value.split(":");
  const hour = Number(parts[0]);
  const minute = parts[1];
  const suffix = hour >= 12 ? "PM" : "AM";
  const displayHour = hour % 12 || 12;
  return displayHour + ":" + minute + " " + suffix;
}

function shiftDuration(shift) {
  const start = Number(shift.start_time.split(":")[0]) * 60 + Number(shift.start_time.split(":")[1]);
  const end = Number(shift.end_time.split(":")[0]) * 60 + Number(shift.end_time.split(":")[1]);
  const hours = (end - start) / 60;
  return Number.isInteger(hours) ? hours + "h" : hours.toFixed(1) + "h";
}

function setState(payload) {
  appState = payload;
  render();
}

async function refreshState(eventId) {
  const query = eventId ? "?event_id=" + encodeURIComponent(eventId) : "";
  const payload = await api("/api/state" + query);
  setState(payload);
}

function render() {
  renderEventSelector();
  renderDashboard();
  renderShifts();
  renderIssues();
  renderWorkload();
  syncShiftFormBounds();
}

function renderEventSelector() {
  const select = $("#eventSelect");
  select.replaceChildren();
  $("#eventCount").textContent = String(appState.events.length);

  if (!appState.events.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No events yet";
    select.appendChild(option);
    select.disabled = true;
    return;
  }

  select.disabled = false;
  appState.events.forEach((event) => {
    const option = document.createElement("option");
    option.value = String(event.id);
    const date = new Date(event.event_date + "T12:00:00").toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
    option.textContent = event.name + " · " + date + " · " + event.filled_count + "/" + event.slot_count;
    select.appendChild(option);
  });
  if (appState.selected_event) {
    select.value = String(appState.selected_event.id);
  }
}

function renderDashboard() {
  const event = appState.selected_event;
  $("#emptyState").hidden = Boolean(event);
  $("#dashboard").hidden = !event;
  if (!event) return;

  $("#eventTitle").textContent = event.name;
  $("#eventDateLabel").textContent = formatDate(event.event_date);
  const location = event.location || "Location not set";
  $("#eventMeta").textContent = location + " · " + formatTime(event.start_time) + "–" + formatTime(event.end_time);

  const summary = appState.summary || {};
  const coverage = Number(summary.coverage_percent || 0);
  $("#coverageValue").textContent = coverage + "%";
  $("#coverageBar").style.width = coverage + "%";
  $("#coverageCaption").textContent = (summary.filled_slots || 0) + " of " + (summary.total_slots || 0) + " slots filled";
  $("#openSlotsValue").textContent = String(summary.unfilled_slots || 0);
  $("#openSlotsCaption").textContent = summary.unfilled_slots ? "Needs a volunteer or a plan" : "Everything is covered";
  $("#assignedValue").textContent = String(summary.volunteers_assigned || 0);
  $("#assignedCaption").textContent = "Across " + appState.shifts.length + " shift" + (appState.shifts.length === 1 ? "" : "s");
  $("#riskValue").textContent = String(summary.issue_count || 0);
  $("#riskCaption").textContent = summary.high_issue_count
    ? summary.high_issue_count + " high-priority risk" + (summary.high_issue_count === 1 ? "" : "s")
    : "No conflicts detected";
  $("#shiftCount").textContent = appState.shifts.length + (appState.shifts.length === 1 ? " shift" : " shifts");
}

function renderShifts() {
  const list = $("#shiftList");
  list.replaceChildren();
  if (!appState.shifts.length) {
    const empty = document.createElement("div");
    empty.className = "all-clear";
    empty.innerHTML = "<strong>No shifts yet</strong>Add the first role and ShiftStack will build the roster.";
    list.appendChild(empty);
    return;
  }

  appState.shifts.forEach((shift) => {
    const card = document.createElement("article");
    card.className = "shift-card";

    const time = document.createElement("div");
    time.className = "shift-time";
    const start = document.createElement("strong");
    start.textContent = formatTime(shift.start_time);
    const duration = document.createElement("small");
    duration.textContent = shiftDuration(shift);
    time.append(start, duration);

    const main = document.createElement("div");
    main.className = "shift-main";

    const titleRow = document.createElement("div");
    titleRow.className = "shift-title-row";
    const titleWrap = document.createElement("div");
    const role = document.createElement("h3");
    role.textContent = shift.role;
    const notes = document.createElement("p");
    notes.textContent = shift.notes || "No shift notes";
    titleWrap.append(role, notes);
    const status = document.createElement("span");
    status.className = "shift-status" + (shift.unfilled ? " open" : "");
    status.textContent = shift.unfilled
      ? shift.unfilled + " open slot" + (shift.unfilled === 1 ? "" : "s")
      : "Fully staffed";
    titleRow.append(titleWrap, status);

    const assignments = document.createElement("div");
    assignments.className = "assignment-list";
    shift.assignments.forEach((assignment) => {
      const chip = document.createElement("span");
      chip.className = "assignment-chip";
      chip.textContent = assignment.volunteer_name;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "Remove " + assignment.volunteer_name);
      remove.addEventListener("click", () => removeAssignment(assignment.id));
      chip.appendChild(remove);
      assignments.appendChild(chip);
    });
    if (shift.unfilled > 0) {
      const add = document.createElement("button");
      add.type = "button";
      add.className = "assign-button";
      add.textContent = "+ Assign volunteer";
      add.addEventListener("click", () => openAssignDialog(shift.id));
      assignments.appendChild(add);
    }

    const actions = document.createElement("div");
    actions.className = "shift-actions";
    const removeShift = document.createElement("button");
    removeShift.type = "button";
    removeShift.className = "link-button";
    removeShift.textContent = "Remove shift";
    removeShift.addEventListener("click", () => deleteShift(shift.id));
    actions.appendChild(removeShift);

    main.append(titleRow, assignments, actions);
    card.append(time, main);
    list.appendChild(card);
  });
}

function renderIssues() {
  const list = $("#issueList");
  list.replaceChildren();
  $("#issueCount").textContent = String(appState.issues.length);
  if (!appState.issues.length) {
    const clear = document.createElement("div");
    clear.className = "all-clear";
    clear.innerHTML = "<strong>Ready to share</strong>Every shift is covered with no conflicts detected.";
    list.appendChild(clear);
    return;
  }

  appState.issues.forEach((issue) => {
    const item = document.createElement("div");
    item.className = "issue-item " + issue.severity;
    const dot = document.createElement("span");
    dot.className = "issue-dot";
    const copy = document.createElement("div");
    const message = document.createElement("p");
    message.textContent = issue.message;
    const label = document.createElement("small");
    label.textContent = issue.severity + " · " + issue.code.replaceAll("_", " ");
    copy.append(message, label);
    item.append(dot, copy);
    list.appendChild(item);
  });
}

function renderWorkload() {
  const list = $("#workloadList");
  list.replaceChildren();
  if (!appState.workload.length) {
    const empty = document.createElement("div");
    empty.className = "all-clear";
    empty.textContent = "Add volunteers to see their planned load.";
    list.appendChild(empty);
    return;
  }

  appState.workload.forEach((entry) => {
    const item = document.createElement("div");
    const percent = entry.limit ? Math.min(100, Math.round((entry.hours / entry.limit) * 100)) : 0;
    const over = entry.hours > entry.limit;
    item.className = "workload-item" + (over ? " over" : "");
    const top = document.createElement("div");
    top.className = "workload-top";
    const name = document.createElement("span");
    name.textContent = entry.name;
    const hours = document.createElement("small");
    hours.textContent = entry.hours + "h / " + entry.limit + "h";
    top.append(name, hours);
    const track = document.createElement("div");
    track.className = "workload-track";
    const fill = document.createElement("i");
    fill.style.width = percent + "%";
    track.appendChild(fill);
    item.append(top, track);
    list.appendChild(item);
  });
}

function syncShiftFormBounds() {
  const event = appState.selected_event;
  const start = $("#shiftForm [name='start_time']");
  const end = $("#shiftForm [name='end_time']");
  if (!event) {
    start.value = "";
    end.value = "";
    return;
  }
  if (!start.value) start.value = event.start_time;
  if (!end.value) end.value = event.end_time;
}

function openAssignDialog(shiftId) {
  const shift = appState.shifts.find((item) => item.id === shiftId);
  if (!shift) return;
  const assignedIds = new Set(shift.assignments.map((item) => item.volunteer_id));
  const available = appState.volunteers.filter((volunteer) => !assignedIds.has(volunteer.id));
  const select = $("#assignmentVolunteer");
  select.replaceChildren();
  activeShiftId = shiftId;
  $("#assignDialogTitle").textContent = "Assign to " + shift.role;

  if (!available.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = appState.volunteers.length ? "No unassigned volunteers" : "Add a volunteer first";
    select.appendChild(option);
    select.disabled = true;
  } else {
    select.disabled = false;
    available.forEach((volunteer) => {
      const option = document.createElement("option");
      option.value = String(volunteer.id);
      option.textContent = volunteer.name + (volunteer.skills ? " · " + volunteer.skills : "");
      select.appendChild(option);
    });
  }
  $("#assignDialog").showModal();
}

async function removeAssignment(assignmentId) {
  try {
    const payload = await api("/api/assignments/" + assignmentId, { method: "DELETE" });
    setState(payload.state);
    showToast("Assignment removed");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function deleteShift(shiftId) {
  if (!window.confirm("Remove this shift and its assignments?")) return;
  try {
    const payload = await api("/api/shifts/" + shiftId, { method: "DELETE" });
    setState(payload.state);
    showToast("Shift removed");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function submitForm(form, path, extra = {}) {
  const button = form.querySelector("button[type='submit']");
  if (button) button.disabled = true;
  try {
    const data = Object.assign({}, Object.fromEntries(new FormData(form)), extra);
    const payload = await api(path, {
      method: "POST",
      body: JSON.stringify(data),
    });
    setState(payload.state);
    return payload;
  } catch (error) {
    showToast(error.message, true);
    throw error;
  } finally {
    if (button) button.disabled = false;
  }
}

$("#eventForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await submitForm(event.currentTarget, "/api/events");
    event.currentTarget.reset();
    showToast("Event created");
  } catch (_error) {
    return;
  }
});

$("#volunteerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await submitForm(event.currentTarget, "/api/volunteers");
    event.currentTarget.reset();
    showToast("Volunteer added");
  } catch (_error) {
    return;
  }
});

$("#shiftForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!appState.selected_event) {
    showToast("Create or select an event first", true);
    return;
  }
  try {
    await submitForm(event.currentTarget, "/api/shifts", { event_id: appState.selected_event.id });
    event.currentTarget.reset();
    syncShiftFormBounds();
    showToast("Shift added");
  } catch (_error) {
    return;
  }
});

$("#assignForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitter = event.submitter;
  if (submitter && submitter.value === "cancel") {
    $("#assignDialog").close();
    return;
  }
  const volunteerId = Number($("#assignmentVolunteer").value);
  if (!volunteerId || !activeShiftId) return;
  try {
    const payload = await api("/api/assignments", {
      method: "POST",
      body: JSON.stringify({ shift_id: activeShiftId, volunteer_id: volunteerId }),
    });
    $("#assignDialog").close();
    setState(payload.state);
    showToast("Volunteer assigned");
  } catch (error) {
    showToast(error.message, true);
  }
});

$("#eventSelect").addEventListener("change", async (event) => {
  try {
    await refreshState(event.target.value);
  } catch (error) {
    showToast(error.message, true);
  }
});

$("#autoAssignButton").addEventListener("click", async () => {
  if (!appState.selected_event) return;
  const button = $("#autoAssignButton");
  button.disabled = true;
  try {
    const payload = await api("/api/auto-assign", {
      method: "POST",
      body: JSON.stringify({ event_id: appState.selected_event.id }),
    });
    setState(payload.state);
    const remaining = payload.state.summary.unfilled_slots;
    showToast("Auto-fill added " + payload.created + " assignment" + (payload.created === 1 ? "" : "s") + (remaining ? "; " + remaining + " slot" + (remaining === 1 ? "" : "s") + " remain open." : "."));
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#clearButton").addEventListener("click", async () => {
  if (!appState.selected_event) return;
  if (!window.confirm("Clear every assignment for this event?")) return;
  try {
    const payload = await api("/api/events/" + appState.selected_event.id + "/clear-assignments", { method: "POST" });
    setState(payload.state);
    showToast("Roster cleared");
  } catch (error) {
    showToast(error.message, true);
  }
});

$("#deleteEventButton").addEventListener("click", async () => {
  if (!appState.selected_event) return;
  if (!window.confirm("Delete this event, its shifts, and its assignments?")) return;
  try {
    const payload = await api("/api/events/" + appState.selected_event.id, { method: "DELETE" });
    setState(payload.state);
    showToast("Event deleted");
  } catch (error) {
    showToast(error.message, true);
  }
});

async function loadDemo() {
  try {
    const payload = await api("/api/demo", { method: "POST" });
    setState(payload.state);
    showToast("Demo roster loaded — try Auto-fill");
  } catch (error) {
    showToast(error.message, true);
  }
}
$("#demoButton").addEventListener("click", loadDemo);
$("#emptyDemoButton").addEventListener("click", loadDemo);

$("#exportCsvButton").addEventListener("click", () => {
  if (appState.selected_event) {
    window.location.href = "/api/export.csv?event_id=" + appState.selected_event.id;
  }
});
$("#exportIcsButton").addEventListener("click", () => {
  if (appState.selected_event) {
    window.location.href = "/api/export.ics?event_id=" + appState.selected_event.id;
  }
});

$("#jumpToEventButton").addEventListener("click", () => {
  const eventSection = $(".panel-event").parentElement.querySelector("details");
  eventSection.open = true;
  eventSection.scrollIntoView({ behavior: "smooth", block: "start" });
  const input = $("#eventForm [name='name']");
  input.focus();
});

window.addEventListener("DOMContentLoaded", async () => {
  try {
    await refreshState();
  } catch (error) {
    showToast(error.message, true);
  }
});
