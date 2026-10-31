// Set the theme before first paint to avoid a flash of the wrong palette.
// Kept as an EXTERNAL same-origin classic script (not inline) so the Content-
// Security-Policy can use a strict `script-src 'self'` — no inline hash/nonce,
// no 'unsafe-inline'. Loaded parser-blocking in <head>, so it still runs before
// the body paints. See ui/nginx/default.conf.template.
(function () {
  try {
    var t = localStorage.getItem("macrorag-theme");
    if (t !== "light" && t !== "dark") {
      t = window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
    }
    document.documentElement.setAttribute("data-theme", t);
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "light");
  }
})();
