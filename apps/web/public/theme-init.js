// Resolves the theme before first paint. Must stay an external classic script:
// the content security policy forbids inline scripts.
(function () {
  var theme = null;
  var source = "system";
  try {
    var stored = window.localStorage.getItem("pm.theme");
    if (stored === "light" || stored === "dark") {
      theme = stored;
      source = "user";
    }
  } catch (error) {
    // Storage may be unavailable (private mode); fall back to the system.
  }
  if (theme === null) {
    try {
      theme = window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
    } catch (error) {
      theme = "light";
    }
  }
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themeSource = source;
})();
