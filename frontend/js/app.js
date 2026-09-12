/* ================================================================
   CENTRAL FORM STATE
   ================================================================ */
const userData = {
  name: "",
  interests: {
    categories: [],
    concepts: [],
    audience: [],
    levels: [],
    languages: []
  },
  courses: []
};

/* Field metadata: visible label + state key. Also defines the display
   order of Step 2. `options` starts empty and is filled in by
   loadFieldData() below, once data/*.json has been fetched — this file
   no longer embeds any data inline, so the same app.js works whether the
   dataset behind a field has 10 items or 15,000. */
const FIELD_META = [
  { key: "categories",  label: "Categories",       options: [] },
  { key: "concepts",    label: "Concepts",         options: [] },
  { key: "audience",    label: "Profession",       options: [] },
  { key: "levels",      label: "Difficulty levels",       options: [] },
  { key: "languages",   label: "Languages",               options: [] }
];

let COURSE_OPTIONS = [];

/* Per-course characteristics for the Step 4 recommendation cards: a dict
   keyed by course name, e.g. { "Learn Social Psychology": { categories: [...],
   concepts: [...], ... } } — built by the notebook from education.kg. This
   is now the single source of truth for course names too: COURSE_OPTIONS
   (Step 3) is derived from this dict's keys, not from a separate file. */
let COURSE_FEATURES = {};
/* Fetches every data/*.json file in parallel and fills in FIELD_META[i].options,
   COURSE_FEATURES and COURSE_OPTIONS. To use a different dataset, replace the
   files in data/ — same schema, no other code changes needed. See
   data/README.md for the exact format of each file.

   course_features.json now doubles as the source for Step 3's course list:
   COURSE_OPTIONS is just its keys, sorted (the dict's own key order follows
   however the file was built, not alphabetically, so we sort explicitly to
   keep Step 3's browsing order predictable). Because of that, a failed
   course_features.json load is now treated as fatal, same as the other
   fields below — Step 3 has nothing to show without it, so silently
   degrading would leave the wizard in a broken-looking state instead of
   showing the "couldn't load the catalog" error init() already handles. */
async function loadFieldData(){
  const keys = FIELD_META.map(f => f.key);
  const [fieldResults, courseFeatures] = await Promise.all([
    Promise.all(keys.map(key =>
      fetch(`data/${key}.json`).then(r => {
        if(!r.ok) throw new Error(`Failed to load data/${key}.json (${r.status})`);
        return r.json();
      })
    )),
    fetch("data/course_features.json").then(r => {
      if(!r.ok) throw new Error(`Failed to load data/course_features.json (${r.status})`);
      return r.json();
    })
  ]);

  const byKey = Object.fromEntries(keys.map((key, i) => [key, fieldResults[i]]));
  FIELD_META.forEach(field => { field.options = byKey[field.key]; });

  COURSE_FEATURES = courseFeatures;
  COURSE_OPTIONS = Object.keys(COURSE_FEATURES).sort();
}
const MAX_VISIBLE_OPTIONS = 60; // cap on rendered rows to keep the DOM light even on lists of 13,000+ items
const MAX_SELECTIONS_PER_FIELD = 5; // max number of values a person can pick within a single interest field
const MAX_COURSE_SELECTIONS = 5; // max number of individual courses a person can shortlist in Step 3
const MAX_VISIBLE_TAGS = 10; // per characteristic group in a recommendation card's expanded details
const MAX_VISIBLE_OBJECTIVES = 5; // "What you'll learn" bullets shown before "+N more"
const COURSE_CARDS_PER_PAGE = 12; // Step 3: both the random-sample size and the cap on search-result cards shown
const DEBUG_DISABLE_RANDOM_SAMPLE = false; // TEMP: stessi 12 corsi ad ogni reload, utile per il debug. Rimetti a false per il comportamento normale.

/* ================================================================
   RENDER STEP 2 — an accordion of "filter-block" panels, one per field
   ================================================================ */
function renderFilters(){
  const container = document.getElementById("filters");
  container.innerHTML = FIELD_META.map(field => `
    <div class="filter-block" data-field="${field.key}">
      <button type="button" class="filter-head" data-toggle="${field.key}" aria-expanded="false">
        <span class="filter-head-left">
          <span class="filter-label">${field.label}</span>
          <span class="filter-preview" data-preview-for="${field.key}"></span>
        </span>
        <span class="filter-head-right">
          <span class="filter-total">${field.options.length.toLocaleString("en-US")} options</span>
          <span class="filter-count" data-count-for="${field.key}">0</span>
          <span class="chevron">⌄</span>
        </span>
      </button>
      <div class="filter-body">
        <div class="selected-chips" data-selected-for="${field.key}"></div>
        <div class="search-wrap">
          <input type="text" class="search-input" placeholder="Search in ${field.label.toLowerCase()}…" data-search-for="${field.key}" autocomplete="off">
        </div>
        <div class="option-list" data-options-for="${field.key}"></div>
      </div>
    </div>
  `).join("");

  FIELD_META.forEach(field => {
    renderOptionList(field.key, "");
    renderSelectedChips(field.key);
    updateFilterCount(field.key);
  });

  document.getElementById("fieldSelectionLimit").textContent = MAX_SELECTIONS_PER_FIELD;

  // A single delegated listener for the whole accordion
  container.addEventListener("click", handleFiltersClick);
  container.addEventListener("input", handleFiltersInput);
}

function fieldByKey(key){
  return FIELD_META.find(f => f.key === key);
}

/* Opens/closes a panel and handles chip removal. */
function handleFiltersClick(e){
  const toggleBtn = e.target.closest("[data-toggle]");
  if(toggleBtn){
    const key = toggleBtn.dataset.toggle;
    const block = document.querySelector(`.filter-block[data-field="${key}"]`);
    const willOpen = !block.classList.contains("is-open");

    document.querySelectorAll(".filter-block.is-open").forEach(b => {
      b.classList.remove("is-open");
      b.querySelector(".filter-head").setAttribute("aria-expanded", "false");
    });

    if(willOpen){
      block.classList.add("is-open");
      toggleBtn.setAttribute("aria-expanded", "true");
      const input = block.querySelector(".search-input");
      requestAnimationFrame(() => input.focus());
    }
    return;
  }

  const optionRow = e.target.closest(".option-row");
  if(optionRow && optionRow.dataset.value !== undefined){
    selectValue(optionRow.dataset.field, optionRow.dataset.value);
    return;
  }

  const removeBtn = e.target.closest("[data-remove]");
  if(removeBtn){
    deselectValue(removeBtn.dataset.field, removeBtn.dataset.remove);
  }
}

function handleFiltersInput(e){
  const input = e.target.closest(".search-input");
  if(!input) return;
  const key = input.dataset.searchFor;
  renderOptionList(key, input.value);
}

/* Fuzzy match score for search: null if `query` doesn't match `text` at
   all, otherwise a number where higher = better. Exact substring matches
   always outrank subsequence-only ("fuzzy") matches — fuzzy is a fallback
   for typos/abbreviations, not a replacement for precise substring search,
   so results don't get reshuffled away from what people expect when they
   type something that matches exactly.

   Subsequence matching (every character of the query appears in the text,
   in order, not necessarily contiguous — e.g. "jvscrpt" matches
   "JavaScript") rather than full edit-distance/Levenshtein: it's O(text
   length) per comparison instead of O(text length × query length), which
   matters here since search runs on every keystroke, undebounced, across
   lists with thousands of items. */
function fuzzyScore(text, query){
  const t = text.toLowerCase();
  const q = query.toLowerCase();
  if(q === "") return 0;

  const idx = t.indexOf(q);
  if(idx !== -1){
    // Exact substring match. A flat +10000 keeps every substring match
    // above every fuzzy-only match regardless of position; within that,
    // matches at the very start (idx 0) get a further bonus, then earlier
    // matches beat later ones.
    return 10000 + (idx === 0 ? 500 : 0) - idx;
  }

  let qi = 0, firstMatch = -1, lastMatch = -1;
  for(let ti = 0; ti < t.length && qi < q.length; ti++){
    if(t[ti] === q[qi]){
      if(firstMatch === -1) firstMatch = ti;
      lastMatch = ti;
      qi++;
    }
  }
  if(qi < q.length) return null; // not every query character was found, in order

  // Tighter matches (query letters close together in the text) score
  // higher than the same letters scattered far apart.
  const span = lastMatch - firstMatch + 1;
  return Math.max(0, 1000 - span * 10) - firstMatch;
}

/* Ranks a list of strings against a query using fuzzyScore, dropping
   non-matches and sorting best-first. Shared by Step 2's field search and
   Step 3's course search so both behave identically. */
function fuzzySearch(items, query){
  return items
    .map(item => ({ item, score: fuzzyScore(item, query) }))
    .filter(m => m.score !== null)
    .sort((a, b) => b.score - a.score)
    .map(m => m.item);
}

/* Filters and renders the option list for a field, excludes already
   selected values, highlights the search term, and caps the number
   of rendered rows to stay lightweight even on huge lists. */
function renderOptionList(key, query){
  const field = fieldByKey(key);
  const listEl = document.querySelector(`[data-options-for="${key}"]`);
  const selected = new Set(userData.interests[key]);
  const q = query.trim().toLowerCase();

  if(selected.size >= MAX_SELECTIONS_PER_FIELD){
    listEl.innerHTML = `<div class="option-empty">You've reached the limit of ${MAX_SELECTIONS_PER_FIELD} selections for ${field.label.toLowerCase()}. Remove one to pick another.</div>`;
    return;
  }

  let matches = field.options.filter(opt => !selected.has(opt));
  if(q){
    matches = fuzzySearch(matches, q);
  }

  if(matches.length === 0){
    listEl.innerHTML = `<div class="option-empty">No results${q ? ` for “${escapeHtml(query)}”` : ""}.</div>`;
    return;
  }

  const visible = matches.slice(0, MAX_VISIBLE_OPTIONS);
  const rows = visible.map(opt => `
    <button type="button" class="option-row" data-field="${key}" data-value="${escapeHtml(opt)}">
      ${highlight(opt, q)}
    </button>
  `).join("");

  const more = matches.length > MAX_VISIBLE_OPTIONS
    ? `<div class="option-more">+${(matches.length - MAX_VISIBLE_OPTIONS).toLocaleString("en-US")} more results — refine your search</div>`
    : "";

  listEl.innerHTML = rows + more;
}

function highlight(text, q){
  const safe = escapeHtml(text);
  if(!q) return safe;
  const idx = safe.toLowerCase().indexOf(q.toLowerCase());
  if(idx === -1) return safe;
  return safe.slice(0, idx) + "<mark>" + safe.slice(idx, idx + q.length) + "</mark>" + safe.slice(idx + q.length);
}

function escapeHtml(str){
  return str.replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}

function selectValue(key, value){
  const list = userData.interests[key];
  if(list.length >= MAX_SELECTIONS_PER_FIELD) return; // limit reached, ignore extra picks
  if(!list.includes(value)) list.push(value);
  refreshField(key);
}

function deselectValue(key, value){
  const list = userData.interests[key];
  const idx = list.indexOf(value);
  if(idx !== -1) list.splice(idx, 1);
  refreshField(key);
}

function refreshField(key){
  const block = document.querySelector(`.filter-block[data-field="${key}"]`);
  const searchInput = block.querySelector(".search-input");
  renderSelectedChips(key);
  renderOptionList(key, searchInput.value);
  updateFilterCount(key);
  updatePreview(key);
}

function renderSelectedChips(key){
  const wrap = document.querySelector(`[data-selected-for="${key}"]`);
  const list = userData.interests[key];
  wrap.innerHTML = list.map(v => `
    <span class="chip-selected">
      ${escapeHtml(v)}
      <button type="button" data-field="${key}" data-remove="${escapeHtml(v)}" aria-label="Remove ${escapeHtml(v)}">✕</button>
    </span>
  `).join("");
}

function updateFilterCount(key){
  const el = document.querySelector(`[data-count-for="${key}"]`);
  const n = userData.interests[key].length;
  el.textContent = `${n}/${MAX_SELECTIONS_PER_FIELD}`;
  el.classList.toggle("has-selection", n > 0);
  el.classList.toggle("at-limit", n >= MAX_SELECTIONS_PER_FIELD);
}

function updatePreview(key){
  const el = document.querySelector(`[data-preview-for="${key}"]`);
  const list = userData.interests[key];
  el.textContent = list.length ? list.join(", ") : "";
}

/* ================================================================
   STEP 3 — COURSE PICKER
   Cards, not a flat list — same .reco-card/.reco-card-trigger/.reco-details
   classes as Step 4 (not a copy: literally the same CSS rules), so any
   visual change to Page 4's cards applies here automatically. The two
   differences from a Page 4 card: a Select button (a course here can be
   picked, a recommendation can't) sitting beside the trigger rather than
   inside it (a <button> can't nest inside another <button>), and the
   detail panel only has the six course_features.json characteristics —
   no "what you'll learn"/price/enrolments, since those come from a live
   recommendation response, which doesn't exist yet at this step.
   ================================================================ */
let COURSE_RANDOM_SAMPLE = [];      // fixed sample shown when the search box is empty
const expandedCourseCards = new Set(); // course names currently expanded, preserved across re-renders

/* Unbiased random sample via Fisher–Yates — NOT `array.sort(() => Math.random() - 0.5)`,
   which is a well-known biased shuffle (some orderings come out more likely
   than others depending on the sort algorithm's comparison pattern). */
function pickRandomSample(pool, n){
    if(DEBUG_DISABLE_RANDOM_SAMPLE) return pool.slice(0, n); // sempre gli stessi N, in ordine alfabetico

  const copy = pool.slice();
  for(let i = copy.length - 1; i > 0; i--){
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy.slice(0, n);
}

function initCoursePicker(){
  document.getElementById("courseTotalCount").textContent = `${COURSE_OPTIONS.length.toLocaleString("en-US")} courses`;
  COURSE_RANDOM_SAMPLE = pickRandomSample(COURSE_OPTIONS, COURSE_CARDS_PER_PAGE);

  document.getElementById("courseSearchInput").addEventListener("input", (e) => {
    renderCourseOptions(e.target.value);
  });

  // One delegated listener covers three separate concerns now, instead of
  // one: selecting/deselecting a course, expanding/collapsing a card's
  // details, and a "+N more" toggle inside an expanded characteristic
  // group (same mechanism as Step 4's initRecommendationCards).
  document.getElementById("courseOptionList").addEventListener("click", (e) => {
    const selectBtn = e.target.closest(".course-select-btn");
    if(selectBtn){
      const name = selectBtn.dataset.course;
      if(userData.courses.includes(name)) deselectCourse(name);
      else selectCourse(name);
      return;
    }

    const moreBtn = e.target.closest(".reco-show-more");
    if(moreBtn){
      moreBtn.closest(".reco-feature-group").classList.add("is-expanded");
      return;
    }

    const trigger = e.target.closest(".reco-card-trigger");
    if(trigger){
      const card = trigger.closest(".reco-card");
      const name = card.dataset.course;
      const isOpen = card.classList.toggle("is-open");
      trigger.setAttribute("aria-expanded", String(isOpen));
      if(isOpen) expandedCourseCards.add(name);
      else expandedCourseCards.delete(name);
    }
  });

  document.getElementById("courseSelectedChips").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-remove]");
    if(btn) deselectCourse(btn.dataset.remove);
  });

  renderCourseOptions("");
  renderCourseChips();
  updateCourseCount();
}

/* Empty search box → the fixed random sample (so cards don't reshuffle
   every time userData.courses changes and the list re-renders). Non-empty
   → fuzzy search across the full catalog (see fuzzySearch above), capped
   to COURSE_CARDS_PER_PAGE with a "+N more — refine your search" hint,
   same pattern as Step 2's option lists.

   Selected courses stay visible (marked, not removed) instead of
   vanishing from the list on pick — for a card grid that avoids the grid
   reflowing/jumping every time something is selected, which was a bigger
   problem here than in the old compact row list this replaces. */
function renderCourseOptions(query){
  const listEl = document.getElementById("courseOptionList");
  const selected = new Set(userData.courses);
  const atLimit = selected.size >= MAX_COURSE_SELECTIONS;
  const q = query.trim().toLowerCase();

  let pool, sampleHint = "", truncated = 0;
  if(q){
    const allMatches = fuzzySearch(COURSE_OPTIONS, q);
    pool = allMatches.slice(0, COURSE_CARDS_PER_PAGE);
    truncated = Math.max(0, allMatches.length - COURSE_CARDS_PER_PAGE);
  } else {
    pool = COURSE_RANDOM_SAMPLE;
    sampleHint = `<p class="course-sample-hint">Showing a random sample of ${pool.length} out of ${COURSE_OPTIONS.length.toLocaleString("en-US")} courses — search to find something specific.</p>`;
  }

  if(pool.length === 0){
    listEl.innerHTML = sampleHint + `<div class="option-empty">No results${q ? ` for “${escapeHtml(query)}”` : ""}.</div>`;
    return;
  }

  const cardsHtml = pool.map(name => renderCourseCard(name, selected.has(name), atLimit)).join("");

  const more = truncated > 0
    ? `<div class="option-more">+${truncated.toLocaleString("en-US")} more results — refine your search</div>`
    : "";
  const limitHint = atLimit
    ? `<p class="course-sample-hint">You've reached the limit of ${MAX_COURSE_SELECTIONS} courses — remove one to select another.</p>`
    : "";

  listEl.innerHTML = sampleHint + limitHint + cardsHtml + more;
}

/* One course card. Reuses renderTagGroup() verbatim from the Step 4
   section below (same truncate-then-"+N more" behavior for courses with
   many categories/concepts) — genuinely the same function, not a
   duplicate, so the two stay in sync automatically. Expand state is
   looked up from expandedCourseCards so a re-render (e.g. after picking
   a different course) doesn't slam an open card shut. */
function renderCourseCard(name, isSelected, atLimit){
  const disableSelect = atLimit && !isSelected;
  const isOpen = expandedCourseCards.has(name);
  const features = COURSE_FEATURES[name];
  const featureGroupsHtml = features
    ? FIELD_META.map(field => renderTagGroup(field.label, features[field.key] || [])).join("")
    : "";
  const detailsHtml = featureGroupsHtml || `<p class="reco-no-details">No additional details available for this course.</p>`;

  return `
    <div class="reco-card${isSelected ? " is-selected" : ""}${isOpen ? " is-open" : ""}" data-course="${escapeHtml(name)}">
      <div class="course-card-top">
        <button type="button" class="reco-card-trigger" aria-expanded="${isOpen}">
          <div class="reco-body">
            <h3 class="reco-title">${escapeHtml(name)}</h3>
          </div>
          <span class="reco-chevron">⌄</span>
        </button>
        <button type="button" class="course-select-btn${isSelected ? " is-selected" : ""}" data-course="${escapeHtml(name)}" ${disableSelect ? "disabled" : ""} aria-pressed="${isSelected}">${isSelected ? "✓ Selected" : "Select"}</button>
      </div>
      <div class="reco-details">${detailsHtml}</div>
    </div>
  `;
}

function renderCourseChips(){
  const wrap = document.getElementById("courseSelectedChips");
  wrap.innerHTML = userData.courses.map(c => `
    <span class="chip-selected">
      ${escapeHtml(c)}
      <button type="button" data-remove="${escapeHtml(c)}" aria-label="Remove ${escapeHtml(c)}">✕</button>
    </span>
  `).join("");
}

function updateCourseCount(){
  const el = document.getElementById("courseCountBadge");
  const n = userData.courses.length;
  el.textContent = `${n}/${MAX_COURSE_SELECTIONS}`;
  el.classList.toggle("has-selection", n > 0);
  el.classList.toggle("at-limit", n >= MAX_COURSE_SELECTIONS);
  // At least one course is required before moving on to recommendations.
  document.getElementById("toStep4").disabled = n === 0;
}

function selectCourse(title){
  if(userData.courses.length >= MAX_COURSE_SELECTIONS) return;
  if(!userData.courses.includes(title)) userData.courses.push(title);
  refreshCoursePicker();
}

function deselectCourse(title){
  const idx = userData.courses.indexOf(title);
  if(idx !== -1) userData.courses.splice(idx, 1);
  refreshCoursePicker();
}

function refreshCoursePicker(){
  const searchInput = document.getElementById("courseSearchInput");
  renderCourseChips();
  renderCourseOptions(searchInput.value);
  updateCourseCount();
}

/* ================================================================
   STEP NAVIGATION
   ================================================================ */
let currentStep = 1;
const FILL_WIDTH = { 1: "0%", 2: "29%", 3: "59%", 4: "88%" };

function goToStep(step){
  currentStep = step;

  document.querySelectorAll("[data-step-panel]").forEach(panel => {
    panel.classList.toggle("hidden", Number(panel.dataset.stepPanel) !== step);
  });
  document.querySelectorAll(".chapter").forEach(ch => {
    const chStep = Number(ch.dataset.step);
    ch.classList.toggle("is-active", chStep === step);
    ch.classList.toggle("is-done", chStep < step);
  });
  document.getElementById("syllabusFill").style.width = FILL_WIDTH[step];

  const activePanel = document.querySelector(`[data-step-panel="${step}"]`);
  activePanel.style.animation = "none";
  void activePanel.offsetWidth;
  activePanel.style.animation = "";
}

const nameInput = document.getElementById("nameInput");
const toStep2Btn = document.getElementById("toStep2");
nameInput.addEventListener("input", () => {
  userData.name = nameInput.value.trim();
  toStep2Btn.disabled = userData.name.length === 0;
});
toStep2Btn.addEventListener("click", () => goToStep(2));
document.getElementById("toStep1").addEventListener("click", () => goToStep(1));

// Step 2 → Step 3 (courses): just moves forward, no fetch yet.
document.getElementById("toStep3").addEventListener("click", () => goToStep(3));
document.getElementById("toStep2From3").addEventListener("click", () => goToStep(2));

// Step 3 (courses) → Step 4 (recommendations): this is where we actually
// call the backend, now that both interests and course picks are set.
document.getElementById("toStep4").addEventListener("click", () => {
  goToStep(4);
  document.getElementById("step4Title").textContent = `Here's what we recommend for ${userData.name || "you"}`;
  fetchRecommendations();
});

// "Edit choices" takes the person back to the previous step (courses),
// which is the most likely thing they'd want to tweak before re-running.
document.getElementById("editChoices").addEventListener("click", () => goToStep(3));

document.getElementById("restart").addEventListener("click", () => {
  userData.name = "";
  Object.keys(userData.interests).forEach(k => userData.interests[k] = []);
  userData.courses = [];
  nameInput.value = "";
  toStep2Btn.disabled = true;
  FIELD_META.forEach(f => {
    document.querySelector(`.filter-block[data-field="${f.key}"]`).classList.remove("is-open");
    refreshField(f.key);
  });
  document.getElementById("courseSearchInput").value = "";
  expandedCourseCards.clear();
  COURSE_RANDOM_SAMPLE = pickRandomSample(COURSE_OPTIONS, COURSE_CARDS_PER_PAGE);
  renderCourseChips();
  renderCourseOptions("");
  updateCourseCount();
  document.getElementById("recoList").classList.add("hidden");
  document.getElementById("step4Actions").classList.add("hidden");
  goToStep(1);
});

/* ================================================================
   BACKEND INTEGRATION — hoploy "recommend" endpoint
   ------------------------------------------------------------------
   Request payload mirrors RecommendationRequest (schemas.py):

     class RecommendationRequest(BaseModel):
         user_id: str                                   # required
         preferences: list[str]                         # required — course titles from Step 3
         previous_recommendations: list[str] | None
         recommendation_count: int | None = 5
         diversity_factor: float | None = 0.5
         disable_collaborative: bool | None = False
         concept_preferences: list[str] | None
         category_preferences: list[str] | None
         audience_preferences: list[str] | None

   NOTE: language_preferences / level_preferences / instructor_preferences
   are commented out in the current RecommendationRequest, even though
   lp.py's EducationLogitsProcessor already reads language_preferences,
   level_preferences and professor_preferences via getattr(...). Until
   those fields are reinstated in the Pydantic model, this wizard keeps
   collecting Languages / Difficulty levels / Instructors in Step 2 (so
   nothing is lost) but does NOT send them here, since posting fields
   the schema doesn't declare could be silently ignored or rejected
   depending on the model's config. Add them to the payload below the
   moment they're uncommented server-side.

   Response payload mirrors RecommendationResponse:

     class RecommendationItem(BaseModel):
         course: str
         score: float                    # 0.0–1.0
         explanation: str | None
         metadata: InfoResponse | None   # course/language/enrolments/price/objectives

     class RecommendationResponse(BaseModel):
         user_id: str
         recommendations: list[RecommendationItem]

   CONNECTION: the frontend calls a same-origin relative path, "/api/...".
   nginx (see frontend/nginx.conf.template) reverse-proxies everything
   under /api/ to the hoploy backend container over the internal Docker
   network. That means: no CORS (same origin), no backend URL baked into
   this file or loaded from a separate runtime config, and the exact same
   code runs in every environment — only frontend/nginx.conf.template's
   BACKEND_HOST/BACKEND_PORT env vars change between setups.
   ================================================================ */
const RECOMMEND_ENDPOINT = "/api/recommend";
// One id per browser session, standing in for real auth until it's wired up.
const SESSION_USER_ID = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : `guest-${Date.now()}`;

async function fetchRecommendations(){
  const loadingEl = document.getElementById("loadingState");
  const listEl = document.getElementById("recoList");
  const errorEl = document.getElementById("recoError");
  const actionsEl = document.getElementById("step4Actions");

  loadingEl.classList.remove("hidden");
  listEl.classList.add("hidden");
  actionsEl.classList.add("hidden");
  errorEl.textContent = "";

  try{
    const response = await requestRecommendations(buildRecommendationRequest(userData));
    renderRecommendations(response.recommendations);
  } catch(err){
    errorEl.textContent = "We couldn't retrieve recommendations. Please try again.";
    console.error("fetchRecommendations error:", err);
  } finally{
    loadingEl.classList.add("hidden");
    listEl.classList.remove("hidden");
    actionsEl.classList.remove("hidden");
  }
}

/* Maps the wizard's internal state to a RecommendationRequest payload.
   Optional fields are omitted entirely (rather than sent as empty lists)
   when unused, so the backend's own Pydantic defaults apply. */
function buildRecommendationRequest(state){
  const payload = {
    user_id: SESSION_USER_ID,
    preferences: state.courses // required — the Step 3 shortlist
  };

  if(state.interests.concepts.length)   payload.concept_preferences  = state.interests.concepts;
  if(state.interests.categories.length) payload.category_preferences = state.interests.categories;
  if(state.interests.audience.length)   payload.audience_preferences = state.interests.audience;

  if(state.interests.languages.length)  payload.language_preferences = state.interests.languages;
  if(state.interests.levels.length)     payload.level_preferences    = state.interests.levels;
  return payload;
}

/* No fallback here on purpose: a failed request is a real failure now that
   nginx connects frontend and backend directly (no CORS to route around,
   no separate host to lose track of). fetchRecommendations() above shows
   the person a clear error instead of quietly swapping in fake results. */
async function requestRecommendations(payload){
  const response = await fetch(RECOMMEND_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if(!response.ok) throw new Error(`Server returned ${response.status}`);
  return await response.json(); // RecommendationResponse: { user_id, recommendations }
}

/* Renders RecommendationItem objects: course title, match score, the
   model's explanation (the reasoning path it followed, if provided),
   whatever InfoResponse metadata is available (language / price /
   enrolments), and a collapsed details panel — click a card to expand it.
   Event delegation on #recoList (set up once in initRecommendationCards,
   not here) handles the expand/collapse, since this function re-renders
   the whole list's innerHTML on every fetch. */
function renderRecommendations(recommendations){
  const listEl = document.getElementById("recoList");
  if(!recommendations || recommendations.length === 0){
    listEl.innerHTML = `<p class="field-hint">No recommendations found: try widening your selections.</p>`;
    return;
  }

  listEl.innerHTML = recommendations.map(rec => {
    const meta = rec.metadata || {};

    const metaBits = [];
    if(meta.language) metaBits.push(meta.language);
    if(meta.price != null) metaBits.push(`$${Number(meta.price).toFixed(2)}`);
    if(meta.enrolments != null) metaBits.push(`${Math.round(meta.enrolments).toLocaleString("en-US")} enrolled`);

    return `
      <div class="reco-card">
        <button type="button" class="reco-card-trigger" aria-expanded="false">
          <div class="reco-body">
            <h3 class="reco-title">${escapeHtml(rec.course)}</h3>
            ${metaBits.length ? `<p class="reco-meta">${metaBits.map(escapeHtml).join(" · ")}</p>` : ""}
            ${rec.explanation ? `<p class="reco-explain">${escapeHtml(rec.explanation)}</p>` : ""}
          </div>
          <div class="reco-right">
            <span class="reco-chevron">⌄</span>
          </div>
        </button>
        <div class="reco-details">${renderCourseDetails(rec)}</div>
      </div>
    `;
  }).join("");
}

/* Builds the expandable panel for one recommended course: "what you'll
   learn" (from the API response's own metadata.objectives — already
   available, no extra fetch) plus the six characteristics from
   course_features.json (categories/concepts/languages/instructors/
   audience/levels — reuses FIELD_META's keys and labels so this stays in
   sync with Step 2 automatically). Falls back to a plain message if a
   course has neither -- shouldn't normally happen, since course_features
   is scoped to the same KG-linked courses the model recommends from, but
   an honest empty state beats a silently broken expand.

   Both objectives and concepts can run long for some courses (30+
   objectives, 50+ concepts have both shown up in practice) — every group
   here goes through the same truncate-then-"+N more" mechanism rather
   than a bespoke fix per section, so any field could blow up like that
   without needing special-casing. */
function renderCourseDetails(rec){
  const objectives = (rec.metadata && rec.metadata.objectives) || [];
  const objectivesHtml = renderObjectivesGroup(objectives);

  const courseFeatures = COURSE_FEATURES[rec.course];
  const featureGroupsHtml = courseFeatures
    ? FIELD_META.map(field => renderTagGroup(field.label, courseFeatures[field.key] || [])).join("")
    : "";

  const combined = objectivesHtml + featureGroupsHtml;
  return combined || `<p class="reco-no-details">No additional details available for this course.</p>`;
}

/* A labeled group of chips, truncated to MAX_VISIBLE_TAGS with the rest
   present in the DOM but hidden (class reco-tag-hidden) until a "+N more"
   toggle reveals them — see initRecommendationCards() for the click
   handling. Returns "" for an empty list so callers can just concatenate
   without checking length themselves. */
function renderTagGroup(label, values){
  if(values.length === 0) return "";
  const visible = values.slice(0, MAX_VISIBLE_TAGS);
  const hidden = values.slice(MAX_VISIBLE_TAGS);
  const visibleHtml = visible.map(v => `<span class="tag">${escapeHtml(v)}</span>`).join("");
  const hiddenHtml = hidden.map(v => `<span class="tag reco-tag-hidden">${escapeHtml(v)}</span>`).join("");
  const moreBtn = hidden.length
    ? `<button type="button" class="reco-show-more">+${hidden.length} more</button>`
    : "";
  return `
    <div class="reco-feature-group">
      <p class="reco-feature-label">${escapeHtml(label)}</p>
      <div class="reco-feature-tags">${visibleHtml}${hiddenHtml}${moreBtn}</div>
    </div>
  `;
}

/* Same truncate-then-expand mechanism as renderTagGroup, adapted to a
   bullet list instead of a chip cloud. */
function renderObjectivesGroup(objectives){
  if(objectives.length === 0) return "";
  const visible = objectives.slice(0, MAX_VISIBLE_OBJECTIVES);
  const hidden = objectives.slice(MAX_VISIBLE_OBJECTIVES);
  const visibleHtml = visible.map(o => `<li>${escapeHtml(o)}</li>`).join("");
  const hiddenHtml = hidden.map(o => `<li class="reco-objective-hidden">${escapeHtml(o)}</li>`).join("");
  const moreBtn = hidden.length
    ? `<button type="button" class="reco-show-more">+${hidden.length} more</button>`
    : "";
  return `
    <div class="reco-feature-group">
      <p class="reco-feature-label">What you'll learn</p>
      <ul class="reco-objectives">${visibleHtml}${hiddenHtml}</ul>
      ${moreBtn}
    </div>
  `;
}

/* One delegated listener for the whole recommendation list, set up once
   at init — handles every card and every "+N more" toggle regardless of
   how many times renderRecommendations() replaces #recoList's innerHTML. */
function initRecommendationCards(){
  document.getElementById("recoList").addEventListener("click", (e) => {
    const moreBtn = e.target.closest(".reco-show-more");
    if(moreBtn){
      moreBtn.closest(".reco-feature-group").classList.add("is-expanded");
      return;
    }

    const trigger = e.target.closest(".reco-card-trigger");
    if(!trigger) return;
    const card = trigger.closest(".reco-card");
    const isOpen = card.classList.toggle("is-open");
    trigger.setAttribute("aria-expanded", String(isOpen));
  });
}

/* ================================================================
   INIT
   Loads data/*.json before rendering Step 2 and Step 3, so the app
   never shows an accordion or course list with nothing in it.
   ================================================================ */
async function init(){
  try{
    await loadFieldData();
  } catch(err){
    console.error("Could not load catalog data from data/*.json:", err);
    document.getElementById("filters").innerHTML =
      `<p class="field-hint">Couldn't load the catalog. Make sure you're serving this page over http(s) (not opening the file directly) and that the data/ folder is present.</p>`;
  }
  renderFilters();
  initCoursePicker();
  initRecommendationCards();
  goToStep(1);
}

init();