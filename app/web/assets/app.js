const state = {
  sessions: [],
  activeSessionId: localStorage.getItem("taskloom.sessionId"),
  activeRun: null,
  events: [],
  eventSource: null,
  timer: null,
  startedAt: null,
};

const $ = (selector) => document.querySelector(selector);
const elements = {
  sessionList: $("#session-list"),
  sessionTitle: $("#session-title"),
  newSession: $("#new-session"),
  serviceDot: $("#service-dot"),
  serviceLabel: $("#service-label"),
  emptyState: $("#empty-state"),
  stream: $("#conversation-stream"),
  currentQuery: $("#current-query"),
  queryForm: $("#query-form"),
  queryInput: $("#query-input"),
  sendButton: $("#send-query"),
  pauseRun: $("#pause-run"),
  resumeRun: $("#resume-run"),
  cancelRun: $("#cancel-run"),
  runStatus: $("#run-status"),
  eventFeed: $("#event-feed"),
  elapsed: $("#elapsed"),
  resultCard: $("#result-card"),
  resultContent: $("#result-content"),
  copyResult: $("#copy-result"),
  tasksEmpty: $("#tasks-empty"),
  taskList: $("#task-list"),
  memoryCount: $("#memory-count"),
  runId: $("#run-id"),
  threadId: $("#thread-id"),
  toast: $("#toast"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  setTimeout(() => elements.toast.classList.remove("visible"), 2400);
}

function setService(online) {
  elements.serviceDot.className = `service-dot ${online ? "online" : "offline"}`;
  elements.serviceLabel.textContent = online ? "服务已连接" : "服务不可用";
}

async function ensureSession() {
  state.sessions = await api("/api/sessions");
  let active = state.sessions.find((item) => item.session_id === state.activeSessionId);
  if (!active) {
    active = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ title: "新会话" }),
    });
    state.sessions.unshift(active);
    state.activeSessionId = active.session_id;
    localStorage.setItem("taskloom.sessionId", active.session_id);
  }
  renderSessions();
  selectSession(active.session_id, true);
}

function renderSessions() {
  elements.sessionList.innerHTML = "";
  state.sessions.forEach((session) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `session-item ${session.session_id === state.activeSessionId ? "active" : ""}`;
    button.innerHTML = `<span>${escapeHtml(session.title)}</span>`;
    button.addEventListener("click", () => selectSession(session.session_id));
    elements.sessionList.appendChild(button);
  });
}

async function selectSession(sessionId, loadLatest = true) {
  state.activeSessionId = sessionId;
  localStorage.setItem("taskloom.sessionId", sessionId);
  const session = state.sessions.find((item) => item.session_id === sessionId);
  elements.sessionTitle.textContent = session?.title || "新会话";
  renderSessions();
  resetRunView();
  if (loadLatest && session?.latest_run_id) {
    try {
      const run = await api(`/api/sessions/${sessionId}/latest-run`);
      showRun(run);
      if (["queued", "running"].includes(run.status)) connectEvents(run.run_id);
    } catch (error) { showToast(error.message); }
  }
}

async function createSession() {
  const session = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ title: `会话 ${state.sessions.length + 1}` }),
  });
  state.sessions.unshift(session);
  await selectSession(session.session_id, false);
  elements.queryInput.focus();
}

async function submitQuery(event) {
  event.preventDefault();
  const query = elements.queryInput.value.trim();
  if (!query || !state.activeSessionId) return;
  elements.sendButton.disabled = true;
  try {
    const run = await api(`/api/sessions/${state.activeSessionId}/queries`, {
      method: "POST",
      body: JSON.stringify({ query }),
    });
    const session = state.sessions.find((item) => item.session_id === state.activeSessionId);
    if (session) {
      session.latest_run_id = run.run_id;
      if (session.title === "新会话" || /^会话 \d+$/.test(session.title)) {
        session.title = query.length > 24 ? `${query.slice(0, 24)}…` : query;
        elements.sessionTitle.textContent = session.title;
      }
    }
    renderSessions();
    elements.queryInput.value = "";
    resizeTextarea();
    state.events = [];
    showRun(run);
    connectEvents(run.run_id);
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.sendButton.disabled = false;
  }
}

function showRun(run) {
  state.activeRun = run;
  elements.emptyState.classList.add("hidden");
  elements.stream.classList.remove("hidden");
  elements.currentQuery.textContent = run.query;
  elements.runId.textContent = shortId(run.run_id);
  elements.runId.title = run.run_id;
  elements.threadId.textContent = shortId(run.thread_id);
  elements.threadId.title = run.thread_id;
  elements.memoryCount.textContent = String(run.memory_count || 0);
  setRunStatus(run.status, run.phase);
  updateRunControls(run.status);
  setPhase(run.phase, run.status);
  renderTasks(run.tasks || []);
  renderResult(run);
  if (run.started_at) startTimer(new Date(run.started_at));
}

function connectEvents(runId) {
  if (state.eventSource) state.eventSource.close();
  const after = state.events.at(-1)?.sequence || 0;
  const source = new EventSource(`/api/runs/${runId}/events?after=${after}`);
  state.eventSource = source;
  source.onmessage = receiveEvent;
  ["run.created", "run.started", "run.pause_requested", "run.pausing_after_tools", "run.paused", "run.resumed", "run.cancel_requested", "run.cancelled", "run.superseded", "run.completed", "run.failed"].forEach((type) => {
    source.addEventListener(type, receiveEvent);
  });
  ["progress.planner", "progress.supervisor", "progress.worker", "progress.aggregate", "progress.reviewer", "progress.memory", "progress.running"].forEach((type) => {
    source.addEventListener(type, receiveEvent);
  });
  source.onerror = async () => {
    source.close();
    await refreshRun(runId);
  };
}

async function receiveEvent(event) {
  const payload = JSON.parse(event.data);
  if (state.events.some((item) => item.sequence === payload.sequence)) return;
  state.events.push(payload);
  appendEvent(payload);
  const phase = payload.event_type.startsWith("progress.")
    ? payload.event_type.replace("progress.", "")
    : state.activeRun?.phase;
  if (phase) setPhase(phase, "running");
  if (["run.pause_requested", "run.pausing_after_tools", "run.paused", "run.resumed", "run.cancel_requested", "run.cancelled", "run.superseded", "run.completed", "run.failed"].includes(payload.event_type)) {
    await refreshRun(state.activeRun.run_id);
  }
  if (["run.cancelled", "run.superseded", "run.completed", "run.failed"].includes(payload.event_type)) {
    state.eventSource?.close();
  }
}

async function refreshRun(runId) {
  try {
    const run = await api(`/api/runs/${runId}`);
    showRun(run);
  } catch (error) { showToast(error.message); }
}

function appendEvent(event) {
  elements.eventFeed.querySelectorAll(".event-row.latest").forEach((node) => node.classList.remove("latest"));
  const row = document.createElement("div");
  row.className = "event-row latest";
  row.innerHTML = `<p>${escapeHtml(event.message)}</p><time>${formatTime(event.created_at)}</time>`;
  elements.eventFeed.appendChild(row);
  elements.eventFeed.scrollTop = elements.eventFeed.scrollHeight;
}

function renderTasks(tasks) {
  if (!tasks.length) {
    elements.tasksEmpty.classList.remove("hidden");
    elements.taskList.classList.add("hidden");
    return;
  }
  elements.tasksEmpty.classList.add("hidden");
  elements.taskList.classList.remove("hidden");
  elements.taskList.innerHTML = tasks.map((task) => `
    <article class="task-item">
      <header><strong>${escapeHtml(task.title || task.task_id)}</strong><span class="task-status ${task.status}">${escapeHtml(task.status)}</span></header>
      <p>${escapeHtml(task.description || "")}</p>
    </article>`).join("");
}

function renderResult(run) {
  const terminal = ["completed", "failed", "cancelled", "superseded"].includes(run.status);
  elements.resultCard.classList.toggle("hidden", !terminal);
  if (!terminal) return;
  if (run.status === "completed") {
    elements.resultContent.innerHTML = `<pre>${escapeHtml(run.final_output || formatWorkerOutput(run.worker_results))}</pre>`;
  } else {
    const fallback = run.status === "cancelled" ? "运行已取消" : run.status === "superseded" ? "已由同一会话中的新 Query 替换" : "运行失败";
    elements.resultContent.innerHTML = `<div class="error">${escapeHtml((run.errors || []).join("\n") || fallback)}</div>`;
  }
}

function formatWorkerOutput(results) {
  if (!results?.length) return "工作流已完成。";
  return JSON.stringify(results.map((item) => item.output).filter(Boolean), null, 2);
}

function setRunStatus(status, phase) {
  const labels = {
    queued: "等待执行", running: phaseLabel(phase), pause_requested: "正在取消 LLM", pausing_after_tools: "等待当前 Tool 后暂停", paused: "已暂停", resuming: "恢复中", cancelling: "取消中", completed: "已完成", failed: "执行失败", cancelled: "已取消", superseded: "已被新 Query 替换",
  };
  elements.runStatus.className = `status-pill ${status === "queued" ? "idle" : status}`;
  elements.runStatus.querySelector("span").textContent = labels[status] || status;
}

function updateRunControls(status) {
  elements.pauseRun.disabled = !["queued", "running", "resuming"].includes(status);
  elements.resumeRun.disabled = !["pause_requested", "pausing_after_tools", "paused"].includes(status);
  elements.cancelRun.disabled = !["queued", "running", "pause_requested", "pausing_after_tools", "paused", "resuming"].includes(status);
}

async function controlRun(action) {
  if (!state.activeRun) return;
  try {
    const run = await api(`/api/runs/${state.activeRun.run_id}/${action}`, { method: "POST" });
    showRun(run);
    if (["pause", "resume"].includes(action)) connectEvents(run.run_id);
  } catch (error) { showToast(error.message); }
}

function setPhase(phase, status) {
  const order = ["planner", "supervisor", "worker", "reviewer"];
  const normalized = phase === "aggregate" ? "worker" : phase === "memory" ? "reviewer" : phase;
  const activeIndex = order.indexOf(normalized);
  document.querySelectorAll(".phase").forEach((node, index) => {
    node.classList.toggle("active", status === "running" && index === activeIndex);
    node.classList.toggle("done", status === "completed" || (activeIndex >= 0 && index < activeIndex));
  });
}

function phaseLabel(phase) {
  return ({ planner: "Planner 规划中", supervisor: "Supervisor 调度中", worker: "Worker 执行中", aggregate: "结果聚合中", reviewer: "Reviewer 审核中", memory: "Memory 写入中" })[phase] || "运行中";
}

function startTimer(date) {
  state.startedAt = date;
  clearInterval(state.timer);
  const update = () => {
    const end = state.activeRun?.finished_at ? new Date(state.activeRun.finished_at) : new Date();
    const seconds = Math.max(0, Math.floor((end - state.startedAt) / 1000));
    elements.elapsed.textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    if (state.activeRun?.finished_at) clearInterval(state.timer);
  };
  update();
  state.timer = setInterval(update, 1000);
}

function resetRunView() {
  state.eventSource?.close();
  clearInterval(state.timer);
  state.activeRun = null;
  state.events = [];
  elements.emptyState.classList.remove("hidden");
  elements.stream.classList.add("hidden");
  elements.eventFeed.innerHTML = "";
  elements.resultCard.classList.add("hidden");
  elements.runId.textContent = "—";
  elements.threadId.textContent = "—";
  elements.memoryCount.textContent = "0";
  renderTasks([]);
  setRunStatus("queued", "queued");
  updateRunControls("queued");
  setPhase("queued", "queued");
  elements.elapsed.textContent = "00:00";
}

function resizeTextarea() {
  elements.queryInput.style.height = "auto";
  elements.queryInput.style.height = `${Math.min(elements.queryInput.scrollHeight, 135)}px`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}
function shortId(value) { return value ? `${value.slice(0, 8)}…${value.slice(-4)}` : "—"; }
function formatTime(value) { return new Date(value).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }); }

elements.newSession.addEventListener("click", () => createSession().catch((error) => showToast(error.message)));
elements.pauseRun.addEventListener("click", () => controlRun("pause"));
elements.resumeRun.addEventListener("click", () => controlRun("resume"));
elements.cancelRun.addEventListener("click", () => controlRun("cancel"));
elements.queryForm.addEventListener("submit", submitQuery);
elements.queryInput.addEventListener("input", resizeTextarea);
elements.queryInput.addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.key === "Enter") elements.queryForm.requestSubmit();
});
document.querySelectorAll("#suggestions button").forEach((button) => button.addEventListener("click", () => {
  elements.queryInput.value = button.textContent;
  resizeTextarea();
  elements.queryInput.focus();
}));
document.querySelectorAll(".inspector-tabs button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".inspector-tabs button").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  $("#tasks-panel").classList.toggle("hidden", button.dataset.tab !== "tasks");
  $("#memory-panel").classList.toggle("hidden", button.dataset.tab !== "memory");
}));
elements.copyResult.addEventListener("click", async () => {
  await navigator.clipboard.writeText(elements.resultContent.innerText);
  showToast("结果已复制");
});

(async function init() {
  try {
    await api("/api/health");
    setService(true);
    await ensureSession();
  } catch (error) {
    setService(false);
    showToast(error.message);
  }
})();
