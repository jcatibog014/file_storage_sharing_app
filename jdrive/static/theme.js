// Runs before the page draws: the saved theme is on <html> from the first paint.
// The "js" class lets the CSS hide things that only make sense with JavaScript off.
document.documentElement.classList.add("js");
try {
  const saved = localStorage.getItem("jdrive-theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
} catch (error) {
  // Storage can be blocked. The system setting still works.
}
