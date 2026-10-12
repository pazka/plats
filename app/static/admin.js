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
  document.querySelectorAll("[data-pct]").forEach(function (el) {
    el.style.width = Math.max(0, Math.min(100, parseFloat(el.getAttribute("data-pct")) || 0)) + "%";
  });
})();
