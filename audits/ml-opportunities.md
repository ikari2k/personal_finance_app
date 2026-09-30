# Machine Learning Opportunities — finance_app

Scope: a proposal, not a code audit — what predictive/ML work would genuinely earn its
keep on this app's actual data, given its actual constraints. Grounded in the real
models (`app/models/transaction.py`, `app/models/category.py`, `app/models/rule.py`),
the real aggregation layer (`app/services/aggregation.py`), and CLAUDE.md's own stated
scale (~7,000–9,000 ledger rows at steady state: 5 years × ~100–150 tx/month,
single-user). Every "already exists" claim below was checked against the code, not
assumed.

---

## 1. The case, in one paragraph

This app already computes rolling averages (`rolling_average_daily_expense`/
`_monthly_expense`), YoY/MoM category deltas (`category_movers`, `category_mom_deltas`),
and surfaces "Top uncategorized descriptions" as rule-writing candidates
(`top_uncategorized_descriptions`) — all *descriptive* statistics computed after the
fact. None of it *predicts* anything: no "you're on track to spend $X this month," no
"this looks like it should be categorized Y," no "this transaction is unusual for this
category." Every one of those is a small, well-scoped, **local, dependency-light**
addition that slots into the existing `services/` layer's own "pure function in,
in-memory data out" convention — no architecture change, no new I/O layer, and (for
most of it) no new dependency at all. The honest constraint is data volume: ~150
transactions/month means most *time-series* forecasting has only 12–60 monthly data
points per category, which rules out anything fancier than exponential smoothing — see
§4.

---

## 2. What data actually exists to learn from

From `Transaction` (`app/models/transaction.py`): `date`, `account_id`, `category`,
`subcategory`, `description` (free text, bank-supplied or user-typed), `amount`
(signed), `type` (income/expense/transfer), `notes`, `counterparty_account`. From
`config/categories.toml`: a two-level category→subcategory tree with optional budgets.
From `config/rules.toml`: existing regex→category mappings, i.e. **existing labeled
training signal for free** — every transaction a rule already matched is a labeled
example without the user doing anything extra.

Three qualities of this data matter for what's feasible:

- **It's small.** ~7,000–9,000 rows total, over 5 years. That's plenty for counting-
  based methods (word frequencies, per-category statistics) and small classifiers, but
  thin for anything that wants thousands of examples per class (deep learning is a
  non-starter — see §5).
- **It's personal and non-stationary.** One user's spending habits, merchants, and
  category vocabulary — a model trained on it will never generalize to anyone else's
  ledger, and re-training after every import is cheap and expected, not a batch job to
  schedule.
- **It's already partially labeled.** `category`/`subcategory` on every non-transfer
  row is a label a supervised model can learn from immediately; `Rule.pattern` is a
  second, independent labeling signal (what a human already decided a description
  *should* map to).

---

## 3. Constraints that rule things in and out

- **No network access beyond localhost** (CLAUDE.md, "What this app is"). This is
  absolute for this proposal: no OpenAI/Anthropic API calls, no cloud ML services, no
  "send my bank transactions to a third party to classify them." Everything below runs
  in-process, offline, on the user's own machine.
- **Zero data-science dependencies today** (`pyproject.toml`'s `dependencies` list is
  `fastapi`, `jinja2`, `pydantic`, `python-multipart`, `tomli-w`, `uvicorn` — no numpy,
  pandas, or scikit-learn). Adding one is a real cost against this project's stated
  minimalism (CLAUDE.md: "Don't add features... beyond what the task requires"), so
  §4 is explicitly tiered by whether it needs a new dependency at all.
- **Human-in-the-loop is already the house style.** Bulk reclassification
  (`services.categorizer.plan_reclassification`/`apply_reclassification`) and
  transfer-detection (`services.transactions.find_transfer_matches`/
  `apply_transfer_matches`) both **always preview before writing anything** — a
  user-confirmed checkbox per suggestion, never a silent auto-apply. Any ML feature
  here should be a *suggestion* slotted into that same preview-then-apply pattern, not
  a new "trust the model" code path.
- **No database, no background jobs.** Storage is flat CSV/TOML, read fully into
  memory per request (CLAUDE.md, "Scale assumptions"). A model here means "a small
  Python object rebuilt from the in-memory transaction list on each relevant request,"
  not a persisted, versioned model artifact — training is cheap and fast enough (a few
  thousand rows) to redo on the fly, which sidesteps a whole category of MLOps concerns
  (model versioning, drift monitoring, retraining schedules) that would otherwise be
  the biggest chunk of the work.

---

## 4. Opportunities, ranked

Each entry: what it predicts, why it's worth it, what it needs, and where it would
live. Tier 0 = pure Python, no new dependency. Tier 1 = one small, well-established,
fully-local library (scikit-learn). Nothing here is Tier 2 (cloud/LLM) — see §5 for why.

### 4.1 Auto-categorization suggestions (Tier 0 → Tier 1) — highest value, best fit

**What**: for a transaction with no matching rule (today defaults to `"Uncategorized"`,
per `services.categorizer`), predict a likely category/subcategory *and a confidence*,
instead of leaving it blank.

**Why it's the strongest candidate**: the app already has a UI surface built for
exactly this — `/categories`' "Top uncategorized descriptions" table
(`top_uncategorized_descriptions`) exists specifically to point the user at rule-writing
candidates. A classifier doesn't replace that workflow, it **automates the next step**:
instead of the user reading "STARBUCKS #4521 — 6 times, still uncategorized" and typing
a regex rule by hand, the app suggests "these 6 look like Dining → Coffee, 92%
confident, based on 40 similar past transactions" and the user confirms once. It also
directly improves `find_transfer_matches`' neighbor problem: an uncategorized row is
also a row transfer-detection has less to go on.

**How**: description text is the training signal. A **Naive Bayes bag-of-words
classifier over already-categorized description text is genuinely Tier 0** — word
counts per category, Laplace-smoothed, is ~40 lines of pure Python with no dependency,
and is a well-known strong baseline for exactly this kind of short, merchant-name-like
text. `Rule.pattern` matches double as bonus labels. A Tier 1 upgrade (scikit-learn's
`TfidfVectorizer` + `MultinomialNB` or `LinearSVC`) buys marginally better accuracy and
calibrated confidence scores, at the cost of the first new dependency — worth it only
if the pure-Python version's accuracy genuinely disappoints in practice.

**Where it fits**: a new `services/categorization_ml.py` (pure functions, same
contract as every other `services/*.py` module — train from `list[Transaction]` in,
return predictions out, no I/O), called from `app.routers.categories` or a new
`/import` step, reusing the *exact* preview-then-apply dialog pattern
`plan_reclassification` already established. Never auto-applies; a confidence below
some threshold shows nothing rather than a bad guess.

### 4.2 Anomaly flagging within a category (Tier 0)

**What**: flag a transaction that's unusual *for its own category* — not "large" in
absolute terms, but large or small relative to that category's own history. A $40
"Dining" charge is normal; a $600 one probably deserves a second look (fraud, a
mis-categorized big-ticket item, or a genuine one-off worth remembering).

**Why**: this is a natural companion to the budget-ring system that already exists
(`_ring_geometry`) — budgets catch "did I overspend the category this month," this
catches "is this *one transaction* the reason," at the moment it's entered rather than
discovered a month later on `/reports`.

**How**: fully Tier 0 — per category, compute a robust center/spread (median and
median-absolute-deviation, more outlier-resistant than mean/stddev on skewed spending
data) from `abs(amount)` history, flag anything past a threshold (e.g. modified z-score
> 3.5, a standard robust-statistics rule of thumb). No model to train, no library.

**Where it fits**: a small `services/anomalies.py` function, surfaced as a badge on the
transaction row or a dashboard "needs attention" widget (that panel already exists —
see `app.routers.dashboard`'s uncategorized-count / transfer-candidate-count entries;
an anomaly count is a natural third).

### 4.3 Month-end spend forecast (Tier 0)

**What**: "at this rate, you'll spend ~$X on Groceries this month" — partway through a
month, project the full-month total from the days-elapsed-so-far pace, instead of only
showing the partial actual (which is what `/reports/{year}/{month}` shows today).

**Why**: this is the most requested *kind* of feature in consumer finance tools
generically, and the app already computes the one building block it needs
(`rolling_average_daily_expense`) — this is closer to "finish a half-built feature"
than "build a new one."

**How**: `(days_elapsed_this_month_spend / days_elapsed) × days_in_month`, optionally
blended with the category's own historical day-of-month spending *shape* (some
categories front-load — rent on the 1st — others are flat) rather than assuming linear
pacing. Both are Tier 0 arithmetic over data `category_monthly_series` already
provides.

**Honesty check on data size**: this is a within-month projection from *daily* data
(30ish points), not a cross-month time-series forecast — that distinction matters,
because a naive "fit a model to 24 months of category totals" approach (§4.4) is
exactly where the small-data ceiling bites.

### 4.4 Cross-month category/net-worth trend forecast (Tier 0, with an honesty caveat)

**What**: project next month's (or next few months') total for a category, or net
worth, from the historical monthly series `category_monthly_series`/
`net_worth_by_month` already build.

**Why**: extends the existing YoY/MoM delta widgets (§2) from "here's what changed"
to "here's where this is headed" — e.g. a savings-rate trendline projected forward.

**The honest limit**: a typical category has 12–60 monthly data points. That's enough
for a **seasonal-naive baseline or simple exponential smoothing** (both Tier 0, a
handful of lines) but is thin for anything that wants to *learn* seasonality
statistically (a model with a "December is 40% higher" parameter needs several
Decembers to trust) — recommend implementing the simple baseline first, and explicitly
NOT reaching for `statsmodels`/`Prophet`-class tooling, which is built for
hundreds-to-thousands of points and would be a much heavier dependency for a
worse-justified accuracy gain here.

### 4.5 Recurring-transaction / subscription detection (Tier 0)

**What**: detect transactions that repeat on a roughly-monthly cadence with a similar
description and amount (subscriptions, rent, utilities) and surface them as a distinct
list — "your recurring charges," with drift detection ("Netflix went from $15.49 to
$17.49 last month").

**Why**: this is a real, commonly-wanted view that's currently invisible — the ledger
has no concept of "recurring" at all today, and it's a natural companion to
`services.transactions.find_orphan_transfer_candidates`'s own "detect a pattern the
user didn't explicitly tell us about" spirit, just applied to *expense* patterns
instead of transfer patterns.

**How**: fully Tier 0 — group by (`category`, normalized description), check for
roughly-30-day gaps between occurrences and low amount variance. No model, just
grouping + simple statistics, structurally similar to `find_transfer_matches`'
date/amount-proximity matching that already exists.

### 4.6 Budget recommendation (Tier 0)

**What**: when a category has no budget set, suggest one from its own trailing
3–6-month average (already computable from `category_recent_monthly_totals`), shown as
a pre-filled (not auto-saved) suggestion on the category edit form.

**Why**: budgets are currently 100% manually typed (`services.categories`); most users
don't know their own trailing average off the top of their head, and the app already
has that number computed for the dashboard sparkline.

**How**: pure arithmetic over an already-existing aggregation function. The cheapest
item on this list by far — worth doing regardless of appetite for the rest.

---

## 5. What NOT to do, and why

- **No cloud/LLM calls of any kind.** Violates "no network access beyond localhost"
  outright — categorization, anomaly detection, and forecasting are all solvable
  locally at this data size; there's no accuracy gap large enough to justify breaking
  that invariant.
- **No deep learning.** A neural classifier wants thousands of examples per class to
  beat a well-tuned Naive Bayes/linear model; this ledger has, at best, a few hundred
  transactions in even its biggest category. It would underperform the Tier 0/1
  options above while adding a genuinely heavy dependency (torch/tensorflow) to an app
  whose entire philosophy is the opposite of that.
- **No persisted/versioned model artifacts, no training pipeline.** At this data
  volume, retraining from scratch on each relevant request (or once per app start) is
  fast enough that a saved model file, a training job, and a "is the model stale"
  check are pure overhead with nothing bought for it.
- **No fully-automatic categorization.** Every existing bulk-write feature in this app
  previews before applying; an ML suggestion that silently rewrites a category the
  first time it's wrong would be a worse experience than today's plain
  `"Uncategorized"` default, and would break the one invariant CLAUDE.md is most
  insistent about across every category-touching feature in the app.

---

## 6. If pursued: suggested order

1. **§4.6 (budget recommendation)** — near-zero cost, reuses an existing aggregation
   function verbatim, good proof that "suggest, don't auto-apply" fits the UI cleanly.
2. **§4.1 Tier 0 (Naive Bayes categorization suggestions)** — highest user-facing
   value, directly extends an already-shipped UI surface
   (`top_uncategorized_descriptions`), still zero new dependencies.
3. **§4.3 (month-end forecast)** — second-highest visibility (a dashboard/report
   widget), builds on `rolling_average_daily_expense` which already exists.
4. **§4.2 (anomaly flagging)** and **§4.5 (recurring detection)** — smaller, standalone
   wins, no dependency on the others, can land in either order or in parallel.
5. **§4.4 (cross-month forecast)** — lowest priority: real value, but the smallest
   accuracy ceiling given the data volume, and the easiest one to over-build if not
   deliberately kept to the Tier 0 baseline recommended above.
6. **Tier 1 (scikit-learn) upgrade to §4.1** — only after the Tier 0 version has been
   used for real and its accuracy is the actual bottleneck, not a guess.
