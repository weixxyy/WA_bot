"use strict";

// CSRF-токен встроен в страницу при отдаче: сторонний сайт его не прочитает.
const CSRF = document.querySelector('meta[name="csrf-token"]').content;

/**
 * Обёртка над fetch: JSON-тело, CSRF-заголовок для изменяющих запросов и
 * понятная ошибка из поля detail (его отдаёт FastAPI).
 */
async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  if (method !== "GET") {
    options.headers["X-CSRF-Token"] = CSRF;
  }
  const response = await fetch(path, options);
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : `Ошибка ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

let toastTimer = null;

function toast(message, kind = "ok") {
  const element = document.getElementById("toast");
  element.textContent = message;
  element.className = `toast ${kind}`;
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    element.hidden = true;
  }, 4200);
}

function on(id, handler) {
  const element = document.getElementById(id);
  if (element) {
    element.addEventListener("click", handler);
  }
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

/* ------------------------------------------------------------------ вкладки */
function activateView(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `view-${name}`);
  });
  if (name === "history") loadHistory();
}

function initTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => activateView(tab.dataset.view));
  });
}

/* ----------------------------------------------------------------- статус */
async function refreshStatus() {
  const runChip = document.getElementById("chip-run");
  try {
    const status = await api("/api/status");

    if (status.running) {
      const trigger = status.trigger === "scheduled" ? "по расписанию" : "вручную";
      runChip.textContent = `идёт прогон (${trigger})`;
      runChip.className = "chip busy";
    } else {
      runChip.textContent = "свободен";
      runChip.className = "chip ok";
    }

    const nextChip = document.getElementById("chip-next");
    if (!status.schedule.enabled) {
      nextChip.textContent = "автозапуск выключен";
      nextChip.className = "chip";
    } else if (status.next_run) {
      nextChip.textContent = `следующий: ${formatDateTime(status.next_run)}`;
      nextChip.className = "chip ok";
    } else {
      nextChip.textContent = "расписание пустое";
      nextChip.className = "chip bad";
    }

    const profileChip = document.getElementById("chip-profiles");
    profileChip.textContent = `профилей: ${status.profiles_count}`;
    profileChip.className = "chip";
    return status;
  } catch (error) {
    runChip.textContent = "панель недоступна";
    runChip.className = "chip bad";
    return null;
  }
}

async function runNow() {
  const button = document.getElementById("run-now");
  button.disabled = true;
  try {
    await api("/api/run", { method: "POST", body: {} });
    toast("Прогон запущен — смотрите журнал", "ok");
    await refreshStatus();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

/* --------------------------------------------------------------- сообщение */
async function loadMessage() {
  try {
    const data = await api("/api/message");
    document.getElementById("message-text").value = data.text;
    document.getElementById("message-info").textContent = `${data.text.length} символов`;
  } catch (error) {
    toast(`Не удалось загрузить сообщение: ${error.message}`, "error");
  }
}

async function saveMessage() {
  const text = document.getElementById("message-text").value;
  try {
    const data = await api("/api/message", { method: "PUT", body: { text } });
    document.getElementById("message-info").textContent = `сохранено, ${data.text.length} символов`;
    toast("Сообщение сохранено", "ok");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------- ссылки */
function updateUrlsInfo(count) {
  document.getElementById("urls-info").textContent = count
    ? `активных ссылок: ${count}`
    : "нет активных ссылок";
}

async function loadUrls() {
  try {
    const data = await api("/api/urls");
    document.getElementById("urls-text").value = data.text;
    updateUrlsInfo(data.urls.length);
  } catch (error) {
    toast(`Не удалось загрузить ссылки: ${error.message}`, "error");
  }
}

async function saveUrls() {
  const text = document.getElementById("urls-text").value;
  try {
    const data = await api("/api/urls", { method: "PUT", body: { text } });
    document.getElementById("urls-text").value = data.text;
    updateUrlsInfo(data.urls.length);
    toast(`Ссылки сохранены: ${data.urls.length}`, "ok");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function importUrls() {
  const text = document.getElementById("import-text").value;
  try {
    const data = await api("/api/urls/import", { method: "POST", body: { text } });
    document.getElementById("import-text").value = "";
    toast(`Добавлено ссылок: ${data.added.length} (дублей: ${data.duplicates.length})`, "ok");
    await loadUrls();
  } catch (error) {
    toast(error.message, "error");
  }
}

/* --------------------------------------------------------------- расписание */
let scheduleTimes = [];

function renderTimes() {
  const container = document.getElementById("times");
  container.innerHTML = "";
  if (!scheduleTimes.length) {
    const empty = document.createElement("span");
    empty.className = "time-chip empty";
    empty.textContent = "времени нет";
    container.appendChild(empty);
    return;
  }
  scheduleTimes.forEach((time) => {
    const chip = document.createElement("span");
    chip.className = "time-chip";
    const label = document.createElement("span");
    label.textContent = time;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "Убрать время";
    remove.addEventListener("click", () => {
      scheduleTimes = scheduleTimes.filter((value) => value !== time);
      renderTimes();
    });
    chip.append(label, remove);
    container.appendChild(chip);
  });
}

function scheduleInfoText(data) {
  return data.next_run
    ? `следующий запуск: ${formatDateTime(data.next_run)}`
    : "автозапуск не запланирован";
}

async function loadSchedule() {
  try {
    const data = await api("/api/schedule");
    scheduleTimes = [...data.times];
    document.getElementById("schedule-enabled").checked = data.enabled;
    renderTimes();
    document.getElementById("schedule-info").textContent = scheduleInfoText(data);
  } catch (error) {
    toast(`Не удалось загрузить расписание: ${error.message}`, "error");
  }
}

function addTime() {
  const input = document.getElementById("time-input");
  const value = input.value;
  if (!value) {
    toast("Сначала выберите время", "error");
    return;
  }
  if (!scheduleTimes.includes(value)) {
    scheduleTimes.push(value);
    scheduleTimes.sort();
    renderTimes();
  }
  input.value = "";
}

async function saveSchedule() {
  try {
    const data = await api("/api/schedule", {
      method: "PUT",
      body: {
        enabled: document.getElementById("schedule-enabled").checked,
        times: scheduleTimes,
      },
    });
    scheduleTimes = [...data.times];
    renderTimes();
    document.getElementById("schedule-info").textContent = scheduleInfoText(data);
    toast("Расписание сохранено", "ok");
    await refreshStatus();
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ профили */
const profileStatuses = {};
let loginJobId = null;
let loginPollTimer = null;

function statusLabel(status) {
  if (status === "logged") return '<span class="status-ok">вход выполнен</span>';
  if (status === "unlogged") return '<span class="status-bad">нужен вход</span>';
  if (status === "unknown") return '<span class="status-warn">не удалось проверить</span>';
  return "—";
}

function renderProfiles(profiles) {
  const body = document.getElementById("profiles-body");
  body.innerHTML = "";
  if (!profiles.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "muted";
    cell.textContent = "Профилей пока нет — добавьте номер.";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  profiles.forEach((profile) => {
    const row = document.createElement("tr");

    const nameCell = document.createElement("td");
    nameCell.textContent = profile.name;

    const kicksCell = document.createElement("td");
    kicksCell.textContent = profile.kicked_count === null || profile.kicked_count === undefined
      ? "—"
      : profile.kicked_count;

    const dateCell = document.createElement("td");
    dateCell.textContent = formatDateTime(profile.last_date_change);

    const statusCell = document.createElement("td");
    statusCell.innerHTML = statusLabel(profileStatuses[profile.name]);

    const actions = document.createElement("div");
    actions.className = "actions-cell";

    const checkButton = document.createElement("button");
    checkButton.type = "button";
    checkButton.className = "ghost small";
    checkButton.textContent = "Проверить";
    checkButton.addEventListener("click", () => checkProfile(profile.name, checkButton, statusCell));

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "danger small";
    deleteButton.textContent = "Удалить";
    deleteButton.addEventListener("click", () => removeProfile(profile.name));

    actions.append(checkButton, deleteButton);

    const actionsCell = document.createElement("td");
    actionsCell.appendChild(actions);

    row.append(nameCell, kicksCell, dateCell, statusCell, actionsCell);
    body.appendChild(row);
  });
}

async function loadProfiles() {
  try {
    const data = await api("/api/profiles");
    renderProfiles(data.profiles);
  } catch (error) {
    toast(`Не удалось загрузить профили: ${error.message}`, "error");
  }
}

async function checkProfile(name, button, statusCell) {
  button.disabled = true;
  statusCell.innerHTML = '<span class="spin">проверяю…</span>';
  try {
    const data = await api(`/api/profiles/${encodeURIComponent(name)}/check`, {
      method: "POST",
      body: {},
    });
    profileStatuses[name] = data.status;
    statusCell.innerHTML = statusLabel(data.status);
  } catch (error) {
    statusCell.textContent = "ошибка";
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function removeProfile(name) {
  if (!window.confirm(`Удалить профиль «${name}»? Сессию придётся привязывать заново.`)) {
    return;
  }
  try {
    await api(`/api/profiles/${encodeURIComponent(name)}`, { method: "DELETE" });
    delete profileStatuses[name];
    toast(`Профиль ${name} удалён`, "ok");
    await loadProfiles();
    await refreshStatus();
  } catch (error) {
    toast(error.message, "error");
  }
}

function setLoginInfo(text) {
  document.getElementById("login-info").textContent = text;
}

function stopLoginPolling() {
  if (loginPollTimer) {
    clearInterval(loginPollTimer);
    loginPollTimer = null;
  }
}

async function addProfile() {
  const input = document.getElementById("profile-name");
  const name = input.value.trim();
  if (!name) {
    toast("Введите имя профиля", "error");
    return;
  }
  const button = document.getElementById("profile-add");
  button.disabled = true;
  try {
    const data = await api("/api/profiles", { method: "POST", body: { name } });
    input.value = "";
    loginJobId = data.job;
    setLoginInfo("Окно Firefox открыто — отсканируйте QR-код телефоном.");
    stopLoginPolling();
    loginPollTimer = setInterval(pollLogin, 2000);
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
  }
}

async function pollLogin() {
  if (!loginJobId) return;
  const button = document.getElementById("profile-add");
  try {
    const job = await api(`/api/profiles/login/${encodeURIComponent(loginJobId)}`);
    if (job.status === "running") {
      setLoginInfo(`${job.name}: ${job.detail}`);
      return;
    }
    stopLoginPolling();
    loginJobId = null;
    button.disabled = false;
    setLoginInfo(`${job.name}: ${job.detail}`);
    if (job.status === "done") {
      toast(`Номер ${job.name} добавлен`, "ok");
    } else {
      toast(job.detail, "error");
    }
    await loadProfiles();
    await refreshStatus();
  } catch (error) {
    stopLoginPolling();
    loginJobId = null;
    button.disabled = false;
    toast(error.message, "error");
  }
}

/* --------------------------------------------------------- история и журнал */
function renderSummary(entries) {
  const container = document.getElementById("summary");
  container.innerHTML = "";
  if (!entries || !entries.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Прогонов ещё не было.";
    container.appendChild(empty);
    return;
  }
  [...entries].reverse().forEach((entry) => {
    const card = document.createElement("div");
    card.className = "card";

    const date = document.createElement("div");
    date.className = "date";
    date.textContent = formatDateTime(entry.date);
    card.appendChild(date);

    const total = document.createElement("div");
    total.className = "line";
    total.textContent = `Выгнан из групп: ${entry.total_kicked ?? 0}`;
    card.appendChild(total);

    Object.entries(entry.profiles || {}).forEach(([name, count]) => {
      const line = document.createElement("div");
      line.className = "line";
      line.textContent = `${name}: ${count === null ? "—" : count}`;
      card.appendChild(line);
    });

    container.appendChild(card);
  });
}

async function loadHistory() {
  try {
    const [stats, logs] = await Promise.all([api("/api/stats"), api("/api/logs")]);
    renderSummary(stats.summary);
    document.getElementById("logs").textContent = logs.text || "Лог пуст.";
  } catch (error) {
    toast(`Не удалось загрузить журнал: ${error.message}`, "error");
  }
}

/* -------------------------------------------------------------------- запуск */
async function refreshAll() {
  await refreshStatus();
  await Promise.all([loadMessage(), loadUrls(), loadSchedule(), loadProfiles()]);
}

function init() {
  initTabs();
  on("refresh", refreshAll);
  on("run-now", runNow);
  on("message-save", saveMessage);
  on("urls-save", saveUrls);
  on("import-run", importUrls);
  on("time-add", addTime);
  on("schedule-save", saveSchedule);
  on("profile-add", addProfile);
  on("history-refresh", loadHistory);

  refreshAll();
  setInterval(refreshStatus, 5000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}



