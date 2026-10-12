/* Admin helpers: confirmations, select-all checkboxes, distribution bars. */
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-confirm]");
    if (el && !window.confirm(el.getAttribute("data-confirm"))) e.preventDefault();
  });
  document.addEventListener("change", function (e) {
    var all = e.target.closest("[data-check-all]");
    if (!all) return;
    var name = all.getAttribute("data-check-all");
    document.querySelectorAll('input[type=checkbox][name="' + name + '"]').forEach(function (c) { c.checked = all.checked; });
  });
  // Colour pickers: the native picker (no name) mirrors a typed #RRGGBB field (the submitted value),
  // so a colour can always be typed even where the phone's picker is limited or out of sync.
  function hex6(v) {
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec((v || "").trim());
    if (!m) return null;
    var h = m[1].length === 3 ? m[1].replace(/./g, "$&$&") : m[1];
    return "#" + h.toLowerCase();
  }
  document.querySelectorAll("input[type=color][data-color-for]").forEach(function (picker) {
    var field = document.getElementById(picker.getAttribute("data-color-for"));
    if (!field) return;
    var v = hex6(field.value);
    if (v) picker.value = v;  // the server-rendered value wins over any restored picker state
    picker.addEventListener("input", function () { field.value = picker.value.toUpperCase(); });
    field.addEventListener("input", function () { var h = hex6(field.value); if (h) picker.value = h; });
  });
  document.querySelectorAll("[data-pct]").forEach(function (el) {
    el.style.width = Math.max(0, Math.min(100, parseFloat(el.getAttribute("data-pct")) || 0)) + "%";
  });
})();
