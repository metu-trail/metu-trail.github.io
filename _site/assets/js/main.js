// Mobile menu + publication filters. The site works without JavaScript;
// this only adds convenience.
(function () {
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  var filters = document.querySelector("[data-filters]");
  if (!filters) return;
  filters.hidden = false;
  var theme = "", pi = "";
  var pubs = Array.prototype.slice.call(document.querySelectorAll(".year-group .pub"));
  var groups = Array.prototype.slice.call(document.querySelectorAll(".year-group"));
  var count = filters.querySelector("[data-count]");
  var empty = document.querySelector(".filter-empty");

  function apply() {
    var n = 0;
    pubs.forEach(function (li) {
      var themes = (li.getAttribute("data-themes") || "").split(/\s+/);
      var pis = (li.getAttribute("data-pis") || "").split(/\s+/);
      var show = (!theme || themes.indexOf(theme) >= 0) && (!pi || pis.indexOf(pi) >= 0);
      li.hidden = !show;
      if (show) n++;
    });
    groups.forEach(function (g) {
      g.hidden = !g.querySelector(".pub:not([hidden])");
    });
    count.textContent = n;
    if (empty) empty.hidden = n > 0;
  }

  function wire(attr, set) {
    var buttons = filters.querySelectorAll("[" + attr + "]");
    Array.prototype.forEach.call(buttons, function (b) {
      b.addEventListener("click", function () {
        Array.prototype.forEach.call(buttons, function (x) { x.classList.remove("is-on"); });
        b.classList.add("is-on");
        set(b.getAttribute(attr));
        apply();
      });
    });
  }
  wire("data-filter-theme", function (v) { theme = v; });
  wire("data-filter-pi", function (v) { pi = v; });

  // Allow links like /publications/#theme=fairness
  var m = location.hash.match(/theme=(\w+)/);
  if (m) {
    var b = filters.querySelector('[data-filter-theme="' + m[1] + '"]');
    if (b) b.click();
  }
})();
