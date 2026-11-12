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

document.addEventListener("DOMContentLoaded", () => {
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
