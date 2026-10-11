// Applies the saved theme before first paint so there is no flash. "system" = no attribute.
(function () {
  try {
    var t = localStorage.getItem("oc-theme");
    if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage blocked: follow system */ }
})();
