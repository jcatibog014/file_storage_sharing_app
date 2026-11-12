// Extras only: every page works without this file.

// The Appearance choice on the Settings page. theme.js applies it on every page.
function setUpTheme() {
  const section = document.querySelector("[data-theme-settings]");
  if (!section) return;
  const root = document.documentElement;
  section.hidden = false;
  section.querySelectorAll('input[name="theme"]').forEach((choice) => {
    choice.checked = choice.value === (root.dataset.theme || "system");
    choice.addEventListener("change", () => {
      try {
        if (choice.value === "system") {
          delete root.dataset.theme;
          localStorage.removeItem("jdrive-theme");
        } else {
          root.dataset.theme = choice.value;
          localStorage.setItem("jdrive-theme", choice.value);
        }
      } catch (error) {
        // Storage can be blocked. The theme still changes for this page.
      }
    });
  });
}

// The ☰ button on phones.
function setUpMenu() {
  const button = document.querySelector("[data-menu-button]");
  const menu = document.getElementById(button && button.getAttribute("aria-controls"));
  if (!button || !menu) return;
  button.hidden = false;
  const setOpen = (open) => {
    button.setAttribute("aria-expanded", open);
    menu.classList.toggle("is-open", open);
  };
  button.addEventListener("click", () => setOpen(button.getAttribute("aria-expanded") !== "true"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
}

// <details> menus (the account menu) close on an outside click or Escape.
function setUpPopovers() {
  const menus = () => document.querySelectorAll("details[data-popover][open]");
  document.addEventListener("click", (event) => {
    menus().forEach((menu) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    menus().forEach((menu) => {
      menu.open = false;
      menu.querySelector("summary").focus();
    });
  });
}

function setUpConfirm() {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  // One button of a form that needs asking first (Delete, beside Save).
  document.querySelectorAll("button[data-confirm-button]").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (!confirm(button.dataset.confirmButton)) event.preventDefault();
    });
  });
}

// A select that saves itself (a role, a filter): no Save button needed.
function setUpAutoSubmit() {
  document.querySelectorAll("[data-auto-submit]").forEach((control) => {
    const button = control.form.querySelector("[data-auto-submit-button]");
    if (button) button.hidden = true;
    control.addEventListener("change", () => control.form.requestSubmit());
  });
}

// The Copy button beside a link.
function setUpCopy() {
  document.querySelectorAll("[data-copy]").forEach((button) => {
    const field = document.getElementById(button.dataset.copy);
    button.hidden = false;
    button.addEventListener("click", async () => {
      field.select();
      try {
        await navigator.clipboard.writeText(field.value);
      } catch (error) {
        document.execCommand("copy");
      }
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = "Copy"), 1500);
    });
  });
}

// The service worker (installable app, offline page) and the "Install the app" button in Settings.
// Browsers that can install it fire "beforeinstallprompt"; only then does the button show.
function setUpInstall() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Not a problem: the site works the same without it.
    });
  }
  const button = document.querySelector("[data-install]");
  if (!button) return;
  let offer = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault(); // our own button instead of the browser's banner
    offer = event;
    button.hidden = false;
  });
  button.addEventListener("click", async () => {
    if (!offer) return;
    offer.prompt();
    await offer.userChoice;
    offer = null;
    button.hidden = true;
  });
  window.addEventListener("appinstalled", () => (button.hidden = true));
}

// Messages fade away after a few seconds (errors stay; data-toast-wait sets a longer time);
// the clock starts again while the mouse or keyboard is on a message. Every message can be closed.
function setUpToasts() {
  const leave = (toast) => {
    toast.classList.add("is-leaving");
    setTimeout(() => toast.remove(), 400);
  };
  document.querySelectorAll(".toast").forEach((toast) => {
    const close = toast.querySelector("[data-toast-close]");
    close.hidden = false;
    close.addEventListener("click", () => leave(toast));
    if (!toast.hasAttribute("data-toast-fade")) return;
    const wait = Number(toast.dataset.toastWait) || 6000;
    let timer = setTimeout(() => leave(toast), wait);
    const hold = () => clearTimeout(timer);
    const resume = () => {
      if (toast.matches(":hover") || toast.contains(document.activeElement)) return;
      clearTimeout(timer);
      timer = setTimeout(() => leave(toast), 4000);
    };
    toast.addEventListener("mouseenter", hold);
    toast.addEventListener("focusin", hold);
    toast.addEventListener("mouseleave", resume);
    toast.addEventListener("focusout", () => setTimeout(resume));
  });
}

// A form's Save button sleeps until something in the form changes.
function setUpWatchChanges() {
  document.querySelectorAll("form[data-watch-changes]").forEach((form) => {
    const button = form.querySelector('button[type="submit"]:not([name])');
    if (!button) return;
    button.disabled = true;
    const wake = () => (button.disabled = false);
    form.addEventListener("input", wake);
    form.addEventListener("change", wake);
  });
}

// The code boxes: only digits, and the form goes as soon as all 6 are typed or pasted.
function setUpCodeBoxes() {
  document.querySelectorAll("[data-code-input]").forEach((input) => {
    input.addEventListener("input", () => {
      input.value = input.value.replace(/\D/g, "").slice(0, 6);
      if (input.value.length === 6 && input.form) input.form.requestSubmit();
    });
  });
}

// "Send a new code" waits a minute after the last one: count down on the button.
function setUpCountdowns() {
  document.querySelectorAll("[data-wait]").forEach((button) => {
    let left = Number(button.dataset.wait) || 0;
    if (!left) return;
    const label = button.textContent;
    const tick = () => {
      if (left <= 0) {
        button.disabled = false;
        button.textContent = label;
        return;
      }
      button.disabled = true;
      button.textContent = `${label} (${left} s)`;
      left -= 1;
      setTimeout(tick, 1000);
    };
    tick();
  });
}

// Uploads, the object-storage way. For each file:
//   1. ask our server for a signed link (POST /api/uploads): it checks the folder, size and space;
//   2. PUT the file straight to the bucket with that link (with a progress bar);
//   3. tell our server it's done (POST /api/uploads/<id>/done): it checks the bucket really has it.
// The file never passes through our server. Without JavaScript, the same form posts to /upload.
function setUpUploads() {
  const form = document.querySelector("form[data-upload]");
  if (!form) return;
  const input = form.querySelector("[data-upload-input]");
  const csrf = form.querySelector('[name="csrf_token"]').value;
  const zone = document.querySelector("[data-drop-zone]");
  const api = form.dataset.api || "/api"; // a "Can upload" link has its own: /s/<token>/api
  let panel = null;
  let rows = null;

  const makePanel = () => {
    if (panel) return;
    panel = document.createElement("section");
    panel.className = "upload-panel";
    panel.setAttribute("aria-live", "polite");
    const head = document.createElement("div");
    head.className = "upload-panel-head";
    head.textContent = "Uploading";
    panel.append(head);
    rows = document.createElement("div");
    rows.className = "upload-rows";
    panel.append(rows);
    document.body.append(panel);
  };

  const makeRow = (file) => {
    const row = document.createElement("div");
    row.className = "upload-row";
    const top = document.createElement("div");
    top.className = "upload-row-top";
    const name = document.createElement("span");
    name.className = "upload-row-name";
    name.textContent = file.name; // text, never HTML
    const state = document.createElement("span");
    state.className = "upload-row-state";
    state.textContent = "Waiting";
    top.append(name, state);
    const bar = document.createElement("div");
    bar.className = "upload-bar";
    const fill = document.createElement("span");
    bar.append(fill);
    row.append(top, bar);
    rows.append(row);
    return {
      progress(part) {
        fill.style.width = `${Math.round(part * 100)}%`;
        state.textContent = `${Math.round(part * 100)}%`;
      },
      done() {
        row.classList.add("is-done");
        fill.style.width = "100%";
        state.textContent = "Done";
      },
      failed(message) {
        row.classList.add("is-failed");
        fill.style.width = "100%";
        state.textContent = message;
      },
    };
  };

  const askServer = async (url, body) => {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json" },
      body: JSON.stringify(body),
      credentials: "same-origin",
    });
    let answer = {};
    try {
      answer = await response.json();
    } catch (error) {
      answer = {};
    }
    if (!response.ok || !answer.ok) throw new Error(answer.error || "Something went wrong. Please try again.");
    return answer;
  };

  // XMLHttpRequest, not fetch: it reports how much has been sent.
  const putToBucket = (link, file, row) =>
    new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open(link.method || "PUT", link.url);
      Object.entries(link.headers || {}).forEach(([key, value]) => request.setRequestHeader(key, value));
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) row.progress(event.loaded / event.total);
      });
      request.addEventListener("load", () =>
        request.status >= 200 && request.status < 300
          ? resolve()
          : reject(new Error("The storage refused the file. Please try again.")));
      request.addEventListener("error", () => reject(new Error("The connection dropped. Please try again.")));
      request.send(file);
    });

  const uploadOne = async (file) => {
    const row = makeRow(file);
    try {
      const link = await askServer(`${api}/uploads`, {
        parent_id: form.dataset.parent || "",
        target_id: form.dataset.target || "",
        name: file.name,
        size: file.size,
        type: file.type,
      });
      row.progress(0);
      await putToBucket(link, file, row);
      await askServer(`${api}/uploads/${link.upload_id}/done`, {});
      row.done();
      return true;
    } catch (error) {
      row.failed(error.message);
      return false;
    }
  };

  const send = async (fileList) => {
    const chosen = [...fileList];
    if (!chosen.length) return;
    makePanel();
    // Three at a time: quick, without choking a slow connection.
    const queue = chosen.slice();
    const results = [];
    const worker = async () => {
      while (queue.length) results.push(await uploadOne(queue.shift()));
    };
    await Promise.all([worker(), worker(), worker()]);
    if (results.every(Boolean)) {
      window.location.reload();
    } else {
      const again = document.createElement("a");
      again.href = window.location.href;
      again.className = "link-button";
      again.textContent = "Refresh";
      panel.querySelector(".upload-panel-head").append(again);
    }
  };

  input.addEventListener("change", () => {
    send(input.files);
    input.value = "";
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    send(input.files);
  });

  if (zone) {
    let depth = 0;
    zone.addEventListener("dragenter", (event) => {
      if (![...event.dataTransfer.types].includes("Files")) return;
      depth += 1;
      zone.classList.add("is-dragging");
    });
    zone.addEventListener("dragleave", () => {
      depth = Math.max(0, depth - 1);
      if (!depth) zone.classList.remove("is-dragging");
    });
    zone.addEventListener("dragover", (event) => event.preventDefault());
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      depth = 0;
      zone.classList.remove("is-dragging");
      send(event.dataTransfer.files);
    });
  }
  // A file dropped anywhere else must not open in the tab (and leave the page).
  document.addEventListener("dragover", (event) => event.preventDefault());
  document.addEventListener("drop", (event) => event.preventDefault());
}

// "New folder": the name box is ready to type over as soon as it opens.
function setUpSelectOnOpen() {
  document.querySelectorAll("details").forEach((panel) => {
    const box = panel.querySelector("[data-select-on-open]");
    if (!box) return;
    panel.addEventListener("toggle", () => {
      if (panel.open) box.select();
    });
  });
  // Rename: select the name without its ending (.pdf), like a computer does.
  const name = document.querySelector("[data-select-name]");
  if (name) {
    const dot = name.value.lastIndexOf(".");
    name.focus();
    name.setSelectionRange(0, dot > 0 ? dot : name.value.length);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  setUpUploads();
  setUpSelectOnOpen();
  setUpToasts();
  setUpWatchChanges();
  setUpInstall();
  setUpTheme();
  setUpMenu();
  setUpPopovers();
  setUpConfirm();
  setUpAutoSubmit();
  setUpCopy();
  setUpCodeBoxes();
  setUpCountdowns();
});
