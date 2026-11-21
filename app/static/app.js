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
 * Payee dropdown in the transactions list's Payee cell (`.payee-combo`,
 * transactions/_table.html): the Category combobox's feel, for free text.
 * Focusing opens the list (existing payees, with this row's own bank text
 * first); typing narrows it; arrows/Enter or a click pick an option. Unlike
 * a category, typed text that matches nothing is a valid value, so Enter or
 * moving focus away saves it, and Escape reverts. Saving POSTs only the
 * payee to /transactions/{id}/payee and swaps the row. Delegated on
 * `document`, like the combobox above, since htmx replaces rows.
 */
(function () {
  "use strict";

  function comboOf(el) {
    return el.closest(".payee-combo");
  }

  function input(combo) {
    return combo.querySelector(".payee-combo-input");
  }

  function list(combo) {
    var el = combo.querySelector(".payee-combo-list");
    // Rows ship an empty list; the shared options come from one <template>.
    if (!el.firstChild) {
      var tpl = document.getElementById("payee-options-list");
      if (tpl) el.appendChild(tpl.content.cloneNode(true));
    }
    return el;
  }

  function allOptions(combo) {
    return combo.querySelectorAll(".payee-option");
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

  // This row's own bank text, offered first unless it is already the value.
  function syncBankOption(combo) {
    var el = list(combo);
    Array.prototype.forEach.call(el.querySelectorAll(".payee-bank"), function (n) {
      n.remove();
    });
    var field = input(combo);
    var bank = field.dataset.bank;
    if (!bank || bank === field.dataset.value) return;
    var label = document.createElement("div");
    label.className = "payee-group-label payee-bank";
    label.textContent = "From the bank";
    var option = document.createElement("div");
    option.className = "payee-option payee-bank";
    option.setAttribute("role", "option");
    option.tabIndex = -1;
    option.textContent = bank;
    option.title = bank;
    el.prepend(label, option);
  }

  function openList(combo) {
    list(combo).hidden = false;
  }

  function closeList(combo) {
    list(combo).hidden = true;
    clearActive(combo);
  }

  // `showAll` lists everything (on focus, before anything is typed);
  // otherwise the typed text narrows the options by substring.
  function filterList(combo, showAll) {
    var query = showAll ? "" : input(combo).value.trim().toLowerCase();
    Array.prototype.forEach.call(allOptions(combo), function (o) {
      o.hidden = query !== "" && o.textContent.toLowerCase().indexOf(query) === -1;
    });
    // A group label is hidden once none of the options after it are visible.
    Array.prototype.forEach.call(
      combo.querySelectorAll(".payee-group-label"),
      function (label) {
        var sibling = label.nextElementSibling;
        var anyVisible = false;
        while (sibling && !sibling.classList.contains("payee-group-label")) {
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
    el.value = el.dataset.value;
  }

  function commit(combo, text) {
    var el = input(combo);
    text = text.trim();
    var changed = text !== el.dataset.value;
    el.value = text;
    el.dataset.value = text;
    closeList(combo);
    if (!changed) return;
    window.htmx.ajax("POST", "/transactions/" + combo.dataset.txnId + "/payee", {
      target: combo.closest("tr"),
      swap: "outerHTML",
      values: { payee: text },
    });
  }

  document.addEventListener("focusin", function (e) {
    if (!e.target.matches || !e.target.matches(".payee-combo-input")) return;
    var combo = comboOf(e.target);
    e.target.select();
    syncBankOption(combo);
    filterList(combo, true);
  });

  document.addEventListener("input", function (e) {
    if (!e.target.matches || !e.target.matches(".payee-combo-input")) return;
    filterList(comboOf(e.target), false);
  });

  document.addEventListener("keydown", function (e) {
    if (!e.target.matches || !e.target.matches(".payee-combo-input")) return;
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
      var active = visibleOptions(combo).find(function (o) {
        return o.classList.contains("active");
      });
      commit(combo, active ? active.textContent : e.target.value);
      e.target.blur();
    } else if (e.key === "Escape") {
      revert(combo);
      closeList(combo);
      e.target.blur();
    }
  });

  // mousedown (not click) fires before the input's blur; preventDefault keeps
  // focus on the input so the focusout handler below doesn't save the old text.
  document.addEventListener("mousedown", function (e) {
    var option = e.target.closest && e.target.closest(".payee-option");
    if (!option) return;
    e.preventDefault();
    var combo = comboOf(option);
    commit(combo, option.textContent);
    input(combo).blur();
  });

  document.addEventListener("focusout", function (e) {
    if (!e.target.matches || !e.target.matches(".payee-combo-input")) return;
    var combo = comboOf(e.target);
    // Deferred one tick so a click on an option (which keeps focus here) is
    // not undone by this handler.
    setTimeout(function () {
      if (combo.contains(document.activeElement)) return;
      var field = input(combo);
      if (field.value.trim() !== field.dataset.value) commit(combo, field.value);
      else closeList(combo);
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

/*
 * Year/month chooser (`[data-month-picker]` in _scope_bar.html): a button
 * opens a popover with a year stepper and a 3x4 month grid; picking a month
 * sets the form's hidden input to YYYY-MM and submits. Months after
 * `data-max` are disabled. Delegated like everything above, since htmx swaps
 * the scope bar out-of-band.
 */
(function () {
  "use strict";

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function parse(key) {
    var m = /^(\d{4})-(\d{2})$/.exec(key || "");
    return m ? { y: +m[1], m: +m[2] } : null;
  }

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function button(text, className, attrs) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = className;
    b.textContent = text;
    Object.keys(attrs || {}).forEach(function (k) {
      b.setAttribute(k, attrs[k]);
    });
    return b;
  }

  function render(picker, year) {
    var pop = picker.querySelector(".month-pop");
    var max = parse(picker.dataset.max);
    var sel = parse(picker.querySelector("input[type=hidden]").value);
    picker.dataset.year = year;

    var head = document.createElement("div");
    head.className = "month-pop-head";
    var prev = button("\u2039", "month-pop-step", { "data-step": "-1", "aria-label": "Previous year" });
    var next = button("\u203a", "month-pop-step", { "data-step": "1", "aria-label": "Next year" });
    if (max && year >= max.y) next.disabled = true;
    var label = document.createElement("span");
    label.textContent = year;
    head.append(prev, label, next);

    var grid = document.createElement("div");
    grid.className = "month-pop-grid";
    MONTHS.forEach(function (name, i) {
      var key = year + "-" + pad(i + 1);
      var b = button(name, "month-pop-month", { "data-key": key });
      b.setAttribute("aria-pressed", sel && sel.y === year && sel.m === i + 1 ? "true" : "false");
      if (max && (year > max.y || (year === max.y && i + 1 > max.m))) b.disabled = true;
      grid.append(b);
    });
    pop.textContent = "";
    pop.append(head, grid);
  }

  function close(picker, refocus) {
    var pop = picker.querySelector(".month-pop");
    var trigger = picker.querySelector(".month-pick");
    pop.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (refocus) trigger.focus();
  }

  function closeAll(except) {
    document.querySelectorAll("[data-month-picker]").forEach(function (p) {
      if (p !== except) close(p, false);
    });
  }

  document.addEventListener("click", function (e) {
    var picker = e.target.closest && e.target.closest("[data-month-picker]");
    if (!picker) {
      closeAll(null);
      return;
    }
    closeAll(picker);
    var pop = picker.querySelector(".month-pop");

    if (e.target.closest(".month-pick")) {
      if (!pop.hidden) return close(picker, false);
      var sel = parse(picker.querySelector("input[type=hidden]").value);
      var max = parse(picker.dataset.max);
      render(picker, sel ? sel.y : max ? max.y : new Date().getFullYear());
      pop.hidden = false;
      picker.querySelector(".month-pick").setAttribute("aria-expanded", "true");
      var active = pop.querySelector("[aria-pressed=true]") || pop.querySelector(".month-pop-month:not(:disabled)");
      if (active) active.focus();
      return;
    }

    var step = e.target.closest(".month-pop-step");
    if (step) {
      render(picker, +picker.dataset.year + +step.dataset.step);
      var again = pop.querySelector('[data-step="' + step.dataset.step + '"]');
      if (again && !again.disabled) again.focus();
      return;
    }

    var month = e.target.closest(".month-pop-month");
    if (month && !month.disabled) {
      picker.querySelector("input[type=hidden]").value = month.dataset.key;
      picker.submit();
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var open = document.querySelector("[data-month-picker] .month-pop:not([hidden])");
    if (open) close(open.closest("[data-month-picker]"), true);
  });
})();
