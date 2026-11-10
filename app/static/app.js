/*
 * App-specific interaction JS (the one custom component beyond htmx's own
 * declarative attributes/expressions). Currently just the inline
 * category/subcategory combobox on the transactions list
 * (transactions/_table.html's txn_row, `.cat-combo`) — a type-to-filter
 * substitute for a native <select> so recategorizing several rows in a
 * row can narrow the option list live instead of relying on a select's
 * jump-to-first-match type-ahead.
 *
 * Every listener is delegated on `document` rather than bound per
 * `.cat-combo` element: rows are re-rendered wholesale by htmx swaps
 * (a category change re-renders its own <tr>; the whole table re-renders
 * on filter/grouping changes), so per-element listeners would silently
 * stop working on any row htmx has ever swapped. Delegation needs no
 * re-init after a swap at all.
 *
 * A commit (click or Enter on an option) is the only way this ever
 * changes a row's category — free-typed text is never sent to the
 * server; it only narrows which of the real `.cat-combo-option`s are
 * shown. Escape, or clicking/tabbing away without picking one, reverts
 * the input to its last committed value (`data-display`).
 */
(function () {
  "use strict";

  function comboOf(el) {
    return el.closest(".cat-combo");
  }

  function input(combo) {
    return combo.querySelector(".cat-combo-input");
  }

  function list(combo) {
    var el = combo.querySelector(".cat-combo-list");
    // Rows ship an empty list (the options are one <template> per type on
    // the page, not repeated per row); fill it on first use.
    if (!el.firstChild) {
      var tpl = document.getElementById("cat-options-" + el.dataset.type);
      if (tpl) el.appendChild(tpl.content.cloneNode(true));
    }
    return el;
  }

  function allOptions(combo) {
    return combo.querySelectorAll(".cat-combo-option");
  }

  function visibleOptions(combo) {
    return Array.prototype.filter.call(allOptions(combo), function (o) {
      return !o.hidden;
    });
  }

  function clearActive(combo) {
    Array.prototype.forEach.call(allOptions(combo), function (o) {
      o.classList.remove("active");
    });
  }

  function openList(combo) {
    list(combo).hidden = false;
  }

  function closeList(combo) {
    list(combo).hidden = true;
    clearActive(combo);
  }

  function filterList(combo) {
    var query = input(combo).value.trim().toLowerCase();
    Array.prototype.forEach.call(allOptions(combo), function (o) {
      o.hidden = query !== "" && o.textContent.toLowerCase().indexOf(query) === -1;
    });
    // A group label is hidden once none of its following options (up to
    // the next group label) are visible.
    Array.prototype.forEach.call(
      combo.querySelectorAll(".cat-combo-group-label"),
      function (label) {
        var sibling = label.nextElementSibling;
        var anyVisible = false;
        while (sibling && !sibling.classList.contains("cat-combo-group-label")) {
          if (!sibling.hidden) anyVisible = true;
          sibling = sibling.nextElementSibling;
        }
        label.hidden = !anyVisible;
      }
    );
    clearActive(combo);
    openList(combo);
  }

  function moveActive(combo, delta) {
    var opts = visibleOptions(combo);
    if (!opts.length) return;
    var activeIndex = opts.findIndex(function (o) {
      return o.classList.contains("active");
    });
    var nextIndex =
      activeIndex === -1
        ? delta > 0
          ? 0
          : opts.length - 1
        : Math.min(Math.max(activeIndex + delta, 0), opts.length - 1);
    clearActive(combo);
    opts[nextIndex].classList.add("active");
    opts[nextIndex].scrollIntoView({ block: "nearest" });
  }

  function revert(combo) {
    var el = input(combo);
    el.value = el.dataset.display;
  }

  function commit(combo, option) {
    var el = input(combo);
    var category = option.dataset.category;
    var subcategory = option.dataset.subcategory;
    var text = option.textContent;
    var changed =
      category !== el.dataset.category || subcategory !== el.dataset.subcategory;
    el.value = text;
    el.dataset.category = category;
    el.dataset.subcategory = subcategory;
    el.dataset.display = text;
    closeList(combo);
    if (combo.hasAttribute("data-local")) {
      // Import preview: nothing to save yet, just record the pick for confirm.
      combo.querySelector("input[type=hidden]").value = JSON.stringify([category, subcategory]);
      return;
    }
    if (!changed) return;
    window.htmx.ajax("POST", "/transactions/" + combo.dataset.txnId + "/category", {
      target: combo.closest("tr"),
      swap: "outerHTML",
      values: { category: category, subcategory: subcategory },
    });
  }

  document.addEventListener("focusin", function (e) {
    if (!e.target.matches || !e.target.matches(".cat-combo-input")) return;
    e.target.select();
    filterList(comboOf(e.target));
  });

  document.addEventListener("input", function (e) {
    if (!e.target.matches || !e.target.matches(".cat-combo-input")) return;
    filterList(comboOf(e.target));
  });

  document.addEventListener("keydown", function (e) {
    if (!e.target.matches || !e.target.matches(".cat-combo-input")) return;
    var combo = comboOf(e.target);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      openList(combo);
      moveActive(combo, 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      openList(combo);
      moveActive(combo, -1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      var opts = visibleOptions(combo);
      var active = opts.find(function (o) {
        return o.classList.contains("active");
      });
      var target = active || (opts.length === 1 ? opts[0] : null);
      if (target) commit(combo, target);
    } else if (e.key === "Escape") {
      revert(combo);
      closeList(combo);
      e.target.blur();
    }
  });

  // A mousedown on an option (not click) fires before the input's own
  // blur, so preventDefault here keeps focus on the input instead of
  // racing the focusout handler below into reverting first.
  document.addEventListener("mousedown", function (e) {
    var option = e.target.closest && e.target.closest(".cat-combo-option");
    if (!option) return;
    e.preventDefault();
    commit(comboOf(option), option);
  });

  document.addEventListener("focusout", function (e) {
    if (!e.target.matches || !e.target.matches(".cat-combo-input")) return;
    var combo = comboOf(e.target);
    // Deferred one tick so a same-combo mousedown-then-commit (which
    // moves focus nowhere, since preventDefault above kept it on the
    // input) isn't immediately undone by this handler.
    setTimeout(function () {
      if (combo.contains(document.activeElement)) return;
      revert(combo);
      closeList(combo);
    }, 0);
  });
})();

/*
 * Net worth line chart hover (reports/_net_worth_line.html, `.nw-chart`):
 * a crosshair, dot and tooltip snapped to the nearest daily point. The
 * points ship as JSON in `data-points` ([x, y, date, value] in SVG
 * viewBox units). Delegated on `document` since htmx swaps the chart
 * when a range tab is clicked.
 */
(function () {
  "use strict";

  function pointsOf(chart) {
    if (!chart._pts) chart._pts = JSON.parse(chart.dataset.points);
    return chart._pts;
  }

  function hide(chart) {
    chart.querySelectorAll(".nw-cross, .nw-dot, .nw-tip").forEach(function (el) {
      el.hidden = true;
    });
  }

  document.addEventListener("mousemove", function (e) {
    var chart = e.target.closest && e.target.closest(".nw-chart");
    if (!chart) return;
    var svg = chart.querySelector(".nw-svg");
    var rect = svg.getBoundingClientRect();
    var vb = svg.viewBox.baseVal;
    var scale = vb.width / rect.width;
    var x = (e.clientX - rect.left) * scale;
    var pts = pointsOf(chart);
    if (!pts.length) return;

    // Binary search for the point whose x is closest.
    var lo = 0, hi = pts.length - 1;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (pts[mid][0] < x) lo = mid + 1; else hi = mid;
    }
    if (lo > 0 && Math.abs(pts[lo - 1][0] - x) < Math.abs(pts[lo][0] - x)) lo--;
    var p = pts[lo];

    var cross = chart.querySelector(".nw-cross");
    var dot = chart.querySelector(".nw-dot");
    var tip = chart.querySelector(".nw-tip");
    cross.setAttribute("x1", p[0]);
    cross.setAttribute("x2", p[0]);
    dot.setAttribute("cx", p[0]);
    dot.setAttribute("cy", p[1]);
    tip.textContent = "";
    var value = document.createElement("strong");
    value.textContent = p[3];
    var day = document.createElement("span");
    day.className = "muted";
    day.textContent = p[2];
    tip.append(value, day);
    cross.hidden = dot.hidden = tip.hidden = false;

    var px = p[0] / scale;
    var left = px + 12;
    if (left + tip.offsetWidth > rect.width) left = px - 12 - tip.offsetWidth;
    tip.style.left = Math.max(0, left) + "px";
  });

  document.addEventListener("mouseout", function (e) {
    var chart = e.target.closest && e.target.closest(".nw-chart");
    if (chart && !chart.contains(e.relatedTarget)) hide(chart);
  });
})();
