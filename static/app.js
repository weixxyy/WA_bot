document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const checkAll = document.querySelector("[data-check-all]");
if (checkAll) {
  checkAll.addEventListener("change", () => {
    document.querySelectorAll('input[name="group_ids"]').forEach((item) => {
      item.checked = checkAll.checked;
    });
  });
}

const statusPanel = document.querySelector("[data-status-panel]");
if (statusPanel) {
  window.setInterval(async () => {
    try {
      const response = await fetch("/api/status", { cache: "no-store" });
      if (!response.ok) return;
      const state = await response.json();
      const operation = document.querySelector("[data-operation]");
      const nextRun = document.querySelector("[data-next-run]");
      if (operation) operation.textContent = state.operation || "Ожидание";
      if (nextRun) nextRun.textContent = state.next_run || "не запланирован";
    } catch (_) {
      // The terminal may have been closed; the static page should remain readable.
    }
  }, 3000);
}
