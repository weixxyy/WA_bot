"use strict";

// CSRF-токен встроен в страницу при отдаче: сторонний сайт его не прочитает.
const CSRF = document.querySelector('meta[name="csrf-token"]').content;

// Версия статики, вместе с которой отдана эта страница. Если сервер отдаёт
// другую — в браузере остался старый app.js (часть кнопок в нём не работает).
const PANEL_VERSION =
  (document.querySelector('meta[name="panel-version"]') || {}).content || "";

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
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    // Код нужен вызывающим: 409 от «Остановить панель» — это не ошибка, а
    // «уже останавливается», а 403 — старая страница с чужим CSRF-токеном.
    error.status = response.status;
    throw error;
  }
  return data;
}

let toastTimer = null;
// Про устаревшую страницу предупреждаем один раз за загрузку, чтобы не спамить.
let stalePageWarned = false;
// Опрос состояния и последний его снимок: по нему диалог остановки понимает,
// идёт ли прогон (панель дождётся его до 15 секунд).
let statusTimer = null;
let lastStatus = null;
// Панель выключается: тосты про ошибки запросов уже не новость.
let stopping = false;

function toast(message, kind = "ok") {
  if (stopping) {
    return;
  }
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

/** Показывает/прячет элемент по id, не падая, если его нет в этой странице. */
function setHidden(id, hidden) {
  const element = document.getElementById(id);
  if (element) {
    element.hidden = hidden;
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
      const kind = status.kind === "join" ? "вступление" : "рассылка";
      runChip.textContent = `идёт ${kind} (${trigger})`;
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

    const nextJoinChip = document.getElementById("chip-next-join");
    if (!status.schedule.join_enabled) {
      nextJoinChip.textContent = "вступление: выкл";
      nextJoinChip.className = "chip";
    } else if (status.next_run_join) {
      nextJoinChip.textContent = `вступление: ${formatDateTime(status.next_run_join)}`;
      nextJoinChip.className = "chip ok";
    } else {
      nextJoinChip.textContent = "вступление: пусто";
      nextJoinChip.className = "chip bad";
    }

    const profileChip = document.getElementById("chip-profiles");
    profileChip.textContent = `профилей: ${status.profiles_count}`;
    profileChip.className = "chip";

    // Страница могла остаться от старой версии панели: тогда часть кнопок в ней
    // просто не обрабатывается — говорим об этом прямо, а не молчим.
    const stalePage =
      Boolean(status.panel_version) && status.panel_version !== PANEL_VERSION;
    setHidden("chip-stale", !stalePage);
    if (stalePage && !stalePageWarned) {
      stalePageWarned = true;
      toast("Страница открыта от старой версии панели — обновите её (Ctrl+Shift+R)", "error");
    }

    // Правки кода подхватываются только при перезапуске панели.
    setHidden("chip-code-stale", !status.code_stale);
    lastStatus = status;
    return status;
  } catch (error) {
    runChip.textContent = "панель недоступна";
    runChip.className = "chip bad";
    return null;
  }
}

async function runScript(buttonId, path, okMessage) {
  const button = document.getElementById(buttonId);
  button.disabled = true;
  try {
    // Причину «ничего не произошло» лучше назвать заранее: ответ 409 от панели
    // объясняет её куда хуже, чем чип с текущим прогоном.
    const status = await api("/api/status");
    if (status.running) {
      const kind = status.kind === "join" ? "вступление" : "рассылка";
      const trigger = status.trigger === "scheduled" ? "по расписанию" : "вручную";
      toast(`Уже идёт ${kind} (${trigger}) — дождитесь завершения`, "error");
      return;
    }
    await api(path, { method: "POST", body: {} });
    toast(okMessage, "ok");
    await refreshStatus();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function runNow() {
  return runScript("run-now", "/api/run", "Прогон рассылки запущен — смотрите журнал");
}

function runJoin() {
  return runScript(
    "run-join",
    "/api/run/join",
    "Скрипт вступления запущен — смотрите журнал",
  );
}

/* ------------------------------------------------------- остановка панели */
/**
 * Выключает панель: то же, что Ctrl+C в её окне или ./stop.sh.
 *
 * Панель — это и есть процесс бота, поэтому вместе с ней останавливается
 * планировщик и освобождается блокировка logs/wa_bot.lock. Если в этот момент
 * идёт прогон, панель дождётся его завершения (до 15 секунд) — об этом честно
 * сказано в подтверждении.
 */
async function stopPanel() {
  const button = document.getElementById("stop-panel");
  const running = Boolean(lastStatus && lastStatus.running);
  const question = running
    ? "Остановить панель? Идёт прогон — панель дождётся его завершения (до 15 секунд), "
      + "затем выключится вместе с планировщиком и снимет блокировку бота."
    : "Остановить панель? Планировщик выключится, блокировка бота снимется. "
      + "Запустить снова — ./web.sh (Windows: web.bat).";
  if (!window.confirm(question)) {
    return;
  }

  if (button) button.disabled = true;
  let answer = null;
  try {
    answer = await api("/api/stop", { method: "POST", body: {} });
  } catch (error) {
    // 409 — панель уже останавливается (нажали в двух вкладках): это не ошибка.
    // Если панель отвечает на другие запросы, остановка не заказана: 403 значит
    // старую страницу с чужим CSRF-токеном, остальное — отказ по делу.
    const alreadyStopping = error.status === 409;
    const alive = alreadyStopping ? true : Boolean(await refreshStatus());
    if (!alreadyStopping && alive) {
      if (button) button.disabled = false;
      const hint = error.status === 403 ? " Обновите страницу (Ctrl+Shift+R)." : "";
      toast(`${error.message}.${hint}`, "error");
      return;
    }
  }
  enterShutdown(answer);
}

/** Показывает, что панель выключается, и ждёт, когда она перестанет отвечать. */
function enterShutdown(answer) {
  stopping = true;
  clearInterval(statusTimer);
  statusTimer = null;
  // Запросы к гасящейся панели смысла не имеют: кнопки больше не работают.
  document.querySelectorAll("button").forEach((item) => {
    item.disabled = true;
  });

  const title = document.getElementById("shutdown-title");
  const text = document.getElementById("shutdown-text");
  if (title) title.textContent = "Панель останавливается…";
  if (text) {
    text.textContent = answer && answer.running
      ? "Ждём завершения текущего прогона (до 15 секунд), затем панель остановит "
        + "планировщик и снимет блокировку бота."
      : "Останавливаю планировщик и снимаю блокировку бота.";
  }
  setHidden("shutdown", false);
  watchShutdown(0);
}

/**
 * Раз в секунду проверяет, жива ли панель: первый сетевой провал означает, что
 * процесс завершился и блокировка снята. Счётчик секунд показываем затем, чтобы
 * ожидание прогона не выглядело зависанием.
 */
function watchShutdown(attempt) {
  const title = document.getElementById("shutdown-title");
  const text = document.getElementById("shutdown-text");
  setTimeout(async () => {
    let status = null;
    try {
      status = await api("/api/status");
    } catch (error) {
      status = null;
    }
    if (!status) {
      if (title) title.textContent = "Панель остановлена";
      if (text) {
        text.textContent =
          "Планировщик выключен, блокировка бота снята — окно можно закрывать.";
      }
      return;
    }
    if (attempt >= 60) {
      if (text) {
        text.textContent =
          "Панель всё ещё на связи. Завершается длинный прогон? Смотрите журнал; "
          + "если ждать больше не нужно — ./stop.sh. Если панель уже запускали "
          + "заново, обновите страницу (Ctrl+Shift+R).";
      }
      return;
    }
    if (text) text.textContent = `Панель ещё завершается… (${attempt + 1} с)`;
    watchShutdown(attempt + 1);
  }, 1000);
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

/* ---------------------------------------------- отсортированные по типу группы */
async function loadGroups() {
  try {
    const data = await api("/api/groups");
    document.getElementById("groups-only-admins").value = data.only_admins.join("\n");
    document.getElementById("groups-closed").value = data.closed.join("\n");
    document.getElementById("groups-open").value = data.open.join("\n");
    document.getElementById("groups-info").textContent =
      `админы: ${data.only_admins.length}, заявки: ${data.closed.length}, остальные: ${data.open.length}`;
  } catch (error) {
    toast(`Не удалось загрузить отсортированные ссылки: ${error.message}`, "error");
  }
}

/* --------------------------------------------------------------- расписание */
let scheduleTimes = [];
let joinScheduleTimes = [];

/** Рисует чипы времён в контейнере; onRemove вызывается по клику на «×». */
function renderTimeChips(containerId, times, onRemove) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  if (!times.length) {
    const empty = document.createElement("span");
    empty.className = "time-chip empty";
    empty.textContent = "времени нет";
    container.appendChild(empty);
    return;
  }
  times.forEach((time) => {
    const chip = document.createElement("span");
    chip.className = "time-chip";
    const label = document.createElement("span");
    label.textContent = time;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "Убрать время";
    remove.addEventListener("click", () => onRemove(time));
    chip.append(label, remove);
    container.appendChild(chip);
  });
}

function renderTimes() {
  renderTimeChips("times", scheduleTimes, (time) => {
    scheduleTimes = scheduleTimes.filter((value) => value !== time);
    renderTimes();
  });
}

function renderJoinTimes() {
  renderTimeChips("join-times", joinScheduleTimes, (time) => {
    joinScheduleTimes = joinScheduleTimes.filter((value) => value !== time);
    renderJoinTimes();
  });
}

function scheduleInfoText(data) {
  return data.next_run
    ? `следующий запуск: ${formatDateTime(data.next_run)}`
    : "автозапуск не запланирован";
}

function joinScheduleInfoText(data) {
  return data.next_run_join
    ? `следующий запуск вступления: ${formatDateTime(data.next_run_join)}`
    : "автозапуск вступления не запланирован";
}

async function loadSchedule() {
  try {
    const data = await api("/api/schedule");
    scheduleTimes = [...data.times];
    document.getElementById("schedule-enabled").checked = data.enabled;
    renderTimes();
    document.getElementById("schedule-info").textContent = scheduleInfoText(data);

    joinScheduleTimes = [...(data.join_times || [])];
    document.getElementById("schedule-join-enabled").checked = data.join_enabled;
    renderJoinTimes();
    document.getElementById("join-schedule-info").textContent = joinScheduleInfoText(data);
  } catch (error) {
    toast(`Не удалось загрузить расписание: ${error.message}`, "error");
  }
}

function addTimeTo(inputId, times, render) {
  const input = document.getElementById(inputId);
  const value = input.value;
  if (!value) {
    toast("Сначала выберите время", "error");
    return;
  }
  if (!times.includes(value)) {
    times.push(value);
    times.sort();
    render();
  }
  input.value = "";
}

function addTime() {
  addTimeTo("time-input", scheduleTimes, renderTimes);
}

function addJoinTime() {
  addTimeTo("join-time-input", joinScheduleTimes, renderJoinTimes);
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

async function saveJoinSchedule() {
  try {
    const data = await api("/api/schedule/join", {
      method: "PUT",
      body: {
        enabled: document.getElementById("schedule-join-enabled").checked,
        times: joinScheduleTimes,
      },
    });
    joinScheduleTimes = [...(data.join_times || [])];
    renderJoinTimes();
    document.getElementById("join-schedule-info").textContent = joinScheduleInfoText(data);
    toast("Расписание вступления сохранено", "ok");
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
  const button = document.getElementById("refresh");
  if (button) button.disabled = true;
  try {
    await refreshStatus();
    await Promise.all([
      loadMessage(),
      loadUrls(),
      loadGroups(),
      loadSchedule(),
      loadProfiles(),
    ]);
    // Без явного подтверждения кнопка «Обновить» выглядит неработающей: данные
    // могли не измениться, и на экране не происходит ровно ничего.
    toast(`Обновлено в ${new Date().toLocaleTimeString("ru-RU")}`, "ok");
  } finally {
    if (button) button.disabled = false;
  }
}

function init() {
  initTabs();
  on("refresh", refreshAll);
  on("run-now", runNow);
  on("run-join", runJoin);
  on("message-save", saveMessage);
  on("urls-save", saveUrls);
  on("import-run", importUrls);
  on("groups-refresh", loadGroups);
  on("time-add", addTime);
  on("schedule-save", saveSchedule);
  on("join-time-add", addJoinTime);
  on("join-schedule-save", saveJoinSchedule);
  on("profile-add", addProfile);
  on("history-refresh", loadHistory);
  on("stop-panel", stopPanel);

  refreshAll();
  // Гасится панель или нет — состояние всё равно нужно: опрос выключает
  // enterShutdown, и по нему видно, что процесс действительно завершился.
  statusTimer = setInterval(refreshStatus, 5000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}



