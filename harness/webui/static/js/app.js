import {
  analyzePrompt,
  cancelJob,
  createReportShare,
  createJob,
  getJob,
  getJobs,
  getGithubRepos,
  getModels,
  getProviders,
  getSession,
  getWorkspaces,
  importHistory,
  probeAllProviders,
  probeProvider,
  redispatchJob,
  revokeReportShare,
  saveProfile,
  subscribeToJob,
  uploadFiles,
  finalizePrompt,
} from "./api.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const bootstrap = window.MOAX_BOOTSTRAP || {};
const PROVIDER_AVATARS = Object.freeze({
  codex: "/static/images/provider-codex.webp",
  claude: "/static/images/provider-claude.webp",
  opencode: "/static/images/provider-opencode.webp",
  cursor: "/static/images/provider-cursor.webp",
  agy: "/static/images/provider-agy.webp",
});
const REVIEW_PIXEL_ART = Object.freeze({
  codex: "/static/images/pixel-codex.webp",
  claude: "/static/images/pixel-claude.webp",
  opencode: "/static/images/pixel-opencode.webp",
  agy: "/static/images/pixel-agy.webp",
  cursor: "/static/images/provider-cursor.webp",
});
const DEPTH_PRESENTATION = Object.freeze({
  quick: {
    value: 0,
    image: "/static/images/context-brief.webp",
    caption: "Two proposers and one refiner for a focused first pass.",
  },
  balanced: {
    value: 1,
    image: "/static/images/context-effort.webp",
    caption: "Three proposers and two refiners with each route’s configured effort.",
  },
  thorough: {
    value: 2,
    image: "/static/images/context-roster.webp",
    caption: "Four proposers, three refiners, and high effort where supported.",
  },
});
const DEPTH_KEYS = ["quick", "balanced", "thorough"];

const state = {
  providers: [],
  models: [],
  jobs: [],
  workspaces: [],
  githubRepos: [],
  pendingFiles: [],
  uploadedFiles: [],
  sourceMode: "brief",
  depthPreset: "balanced",
  route: "overview",
  detailJob: null,
  events: [],
  eventStop: null,
  promptCoach: { original: "", analysis: null, answers: [], index: 0, result: null, undo: "" },
  detailRefreshAt: 0,
  step: 1,
  profile: loadProfile(),
  loading: {
    providers: true,
    models: true,
    jobs: true,
    workspaces: true,
    githubRepos: true,
  },
};

function loadProfile() {
  let profile;
  try { profile = JSON.parse(localStorage.getItem("moax.profile") || "{}"); } catch { profile = {}; }
  const id = profile.id || newProfileId();
  const next = { id, name: profile.name || "Local operator", compactEvents: profile.compactEvents ?? true };
  localStorage.setItem("moax.profile", JSON.stringify(next));
  return next;
}

function newProfileId() {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function applySessionProfile(saved) {
  state.profile.id = saved.id || state.profile.id;
  state.profile.name = saved.display_name || saved.name || state.profile.name;
  state.profile.compactEvents = saved.settings?.compact_events ?? state.profile.compactEvents;
  localStorage.setItem("moax.profile", JSON.stringify(state.profile));
  updateProfileChrome();
}

async function establishProfileSession() {
  const session = await getSession();
  if (session) {
    applySessionProfile(session);
    return;
  }
  try {
    applySessionProfile(await saveProfile(state.profile));
  } catch (error) {
    if (![403, 409].includes(error.status)) throw error;
    state.profile.id = newProfileId();
    localStorage.setItem("moax.profile", JSON.stringify(state.profile));
    applySessionProfile(await saveProfile(state.profile));
  }
}

function installBrandAssets() {
  let favicon = document.querySelector('link[rel~="icon"]');
  if (!favicon) {
    favicon = document.createElement("link");
    favicon.rel = "icon";
    document.head.append(favicon);
  }
  favicon.type = "image/png";
  favicon.href = "/static/images/favicon.png";
}

function updateProfileChrome() {
  $("#sidebar-profile-name").textContent = state.profile.name;
}

function hydrateOptionalAssets(root = document) {
  $$("img[data-optional-src]:not([data-probed])", root).forEach((image) => {
    image.dataset.probed = "true";
    const source = image.dataset.optionalSrc;
    const probe = new Image();
    probe.onload = () => {
      image.src = source;
      image.hidden = false;
      const fallback = image.parentElement?.querySelector(".asset-fallback");
      if (fallback) fallback.hidden = true;
    };
    probe.src = source;
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  const url = String(value || "");
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  return "#";
}

function titleCase(value) {
  return String(value || "").replaceAll("_", " ").replaceAll("-", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function shortPath(value) {
  const path = String(value || "—").replace(/\/+$/, "");
  const parts = path.split("/");
  return parts.length > 3 ? `…/${parts.slice(-2).join("/")}` : path;
}

function formatTime(value, includeDate = true) {
  if (!value) return "—";
  const normalized = typeof value === "number" && value < 10_000_000_000 ? value * 1000 : value;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, includeDate
    ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
    : { hour: "numeric", minute: "2-digit", second: "2-digit" }
  ).format(date);
}

function formatDuration(start, end = Date.now()) {
  if (!start) return "—";
  const startValue = typeof start === "number" && start < 10_000_000_000 ? start * 1000 : start;
  const endValue = typeof end === "number" && end < 10_000_000_000 ? end * 1000 : end;
  const milliseconds = Math.max(0, new Date(endValue).getTime() - new Date(startValue).getTime());
  if (!Number.isFinite(milliseconds)) return "—";
  const seconds = Math.floor(milliseconds / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  if (hours) return `${hours}h ${minutes % 60}m`;
  if (minutes) return `${minutes}m ${seconds % 60}s`;
  return `${seconds}s`;
}

function showToast(message, type = "info") {
  const toast = document.createElement("div");
  toast.className = `toast${type === "error" ? " is-error" : ""}`;
  toast.textContent = message;
  $("#toast-region").append(toast);
  window.setTimeout(() => toast.remove(), 4600);
}

function setButtonLoading(button, loading, label, idleLabel) {
  button.disabled = loading;
  button.dataset.loading = String(loading);
  button.setAttribute("aria-busy", String(loading));
  button.textContent = loading ? label : idleLabel;
}

function closeInfoPopover() {
  const popover = $("#info-popover");
  popover.hidden = true;
  $$(".info-button[aria-expanded='true']").forEach((button) => button.setAttribute("aria-expanded", "false"));
}

function toggleInfoPopover(button) {
  const popover = $("#info-popover");
  if (button.getAttribute("aria-expanded") === "true") {
    closeInfoPopover();
    return;
  }
  closeInfoPopover();
  $("#info-popover-title").textContent = button.dataset.infoTitle;
  $("#info-popover-body").textContent = button.dataset.infoBody;
  popover.hidden = false;
  button.setAttribute("aria-expanded", "true");
  const anchor = button.getBoundingClientRect();
  const box = popover.getBoundingClientRect();
  const left = Math.max(14, Math.min(anchor.left - box.width + anchor.width, window.innerWidth - box.width - 14));
  const below = anchor.bottom + 8;
  const top = below + box.height <= window.innerHeight - 12
    ? below
    : Math.max(12, anchor.top - box.height - 8);
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;
}

function routeFromPath(pathname = location.pathname) {
  const match = pathname.match(/^\/runs\/([^/]+)\/?$/);
  if (match) return { route: "run-detail", id: decodeURIComponent(match[1]) };
  if (pathname.startsWith("/providers")) return { route: "providers" };
  if (pathname.startsWith("/runs")) return { route: "runs" };
  if (pathname.startsWith("/new")) return { route: "new" };
  return { route: "overview" };
}

function pathForRoute(route, id) {
  return route === "overview" ? "/"
    : route === "run-detail" ? `/runs/${encodeURIComponent(id)}`
    : `/${route}`;
}

async function navigate(route, id, push = true) {
  if (state.eventStop) {
    state.eventStop();
    state.eventStop = null;
  }
  state.route = route;
  $$(".view").forEach((view) => view.classList.toggle("is-visible", view.dataset.view === route));
  $$(".nav-item").forEach((link) => link.classList.toggle("is-active", link.dataset.route === (route === "run-detail" ? "runs" : route)));
  $("#sidebar").classList.remove("is-open");
  $("#menu-button").setAttribute("aria-expanded", "false");
  if (push) history.pushState({ route, id }, "", pathForRoute(route, id));
  window.scrollTo({ top: 0, behavior: "auto" });
  if (route === "run-detail" && id) await loadRunDetail(id);
  if (route === "runs") renderArchive();
  if (route === "new") renderReview();
  document.title = `${route === "overview" ? "Control Room" : titleCase(route)} · MoA-X`;
}

function providerReady(provider) {
  return provider.available || provider.status === "ready" || provider.status === "authenticated";
}

function statusClass(provider) {
  return providerReady(provider) ? "is-ready"
    : provider.installed ? "is-warning"
    : "is-error";
}

function statusCopy(provider) {
  if (providerReady(provider)) return "Account ready";
  if (provider.installed) return "Needs attention";
  return "CLI missing";
}

function renderMachineHealth() {
  const ready = state.providers.filter(providerReady).length;
  const signal = $("#machine-signal");
  signal.className = `signal-dot ${ready ? "is-good" : "is-bad"}`;
  $("#machine-label").textContent = ready ? `${ready} provider${ready === 1 ? "" : "s"} ready` : "No providers ready";
  $("#machine-detail").textContent = ready ? "Local accounts available" : "Open provider workbench";
}

function renderHealthRibbon() {
  const target = $("#health-ribbon");
  target.setAttribute("aria-busy", "false");
  if (!state.providers.length) {
    target.innerHTML = `<div class="health-cell is-error"><i></i><div><strong>No provider data</strong><small>Check the local server</small></div></div>`;
    return;
  }
  target.innerHTML = state.providers.slice(0, 6).map((provider) => `
    <div class="health-cell ${statusClass(provider)}">
      <i></i>
      <div>
        <strong>${escapeHtml(provider.name)}</strong>
        <small>${escapeHtml(statusCopy(provider))}</small>
      </div>
    </div>
  `).join("");
}

function renderProviderSummary() {
  const ready = state.providers.filter(providerReady).length;
  const installed = state.providers.filter((provider) => provider.installed).length;
  const models = state.providers.reduce((sum, provider) => sum + provider.models.length, 0);
  $("#provider-summary").innerHTML = `
    <div class="summary-stat"><strong>${ready}</strong><span>Accounts ready for work</span></div>
    <div class="summary-stat"><strong>${installed}</strong><span>CLIs installed on this machine</span></div>
    <div class="summary-stat"><strong>${models || state.models.length}</strong><span>Discovered model choices</span></div>
  `;
  $("#provider-summary").setAttribute("aria-busy", "false");
}

function renderProviders() {
  if (state.loading.providers) return;
  renderProviderSummary();
  const target = $("#provider-grid");
  target.setAttribute("aria-busy", "false");
  if (!state.providers.length) {
    target.innerHTML = `<div class="empty-state"><span class="empty-number">—</span><div><h3>No provider probes returned</h3><p>Make sure the Flask server can access the same HOME, PATH, and environment as your terminal.</p></div></div>`;
    return;
  }
  target.innerHTML = state.providers.map((provider, index) => {
    const listedModels = provider.routes?.length ? provider.routes : provider.models;
    const modelNames = listedModels.map((model) => model.name || model.id).join(", ") || "Discovered at launch";
    const avatarPath = PROVIDER_AVATARS[provider.id];
    const initials = provider.name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
    return `
      <article class="provider-card" data-provider-id="${escapeHtml(provider.id)}">
        <aside class="provider-card-portrait">
          <div class="provider-avatar">
            ${avatarPath ? `<img data-optional-src="${avatarPath}" alt="${escapeHtml(provider.name)} provider portrait" hidden>` : ""}
            <span class="asset-fallback">${escapeHtml(initials)}</span>
          </div>
          <span>${String(index + 1).padStart(2, "0")} · ${escapeHtml(titleCase(provider.lab))}</span>
        </aside>
        <div class="provider-card-body">
          <div class="provider-card-head">
            <div>
              <p class="eyebrow">${escapeHtml(String(provider.harness).toUpperCase())} CLI</p>
              <h2>${escapeHtml(provider.name)}</h2>
            </div>
            <span class="status-tag ${providerReady(provider) ? "completed" : provider.installed ? "queued" : "failed"}">${escapeHtml(statusCopy(provider))}</span>
          </div>
          <p>${escapeHtml(provider.detail || (providerReady(provider)
            ? "This provider can use the account already authenticated on the host."
            : "Run a fresh capability check after installing or signing in with this CLI."))}</p>
          <dl class="provider-details">
            <div><dt>Version</dt><dd>${escapeHtml(provider.version)}</dd></div>
            <div><dt>Authentication</dt><dd>${escapeHtml(provider.authMode)}</dd></div>
            <div><dt>Models</dt><dd title="${escapeHtml(modelNames)}">${escapeHtml(modelNames)}</dd></div>
            <div><dt>Lab</dt><dd>${escapeHtml(titleCase(provider.lab))}</dd></div>
          </dl>
          <div class="provider-foot">
            <small>${provider.last_checked ? `Checked ${escapeHtml(formatTime(provider.last_checked))}` : "Not checked this session"}</small>
            <button class="secondary-button" type="button" data-probe-provider="${escapeHtml(provider.id)}">Check again</button>
          </div>
          ${providerReady(provider) ? "" : `
            <details class="provider-setup">
              <summary>Setup on this machine</summary>
              ${provider.installed ? "" : `<div><b>Install</b><code>${escapeHtml(provider.install_command || "See provider documentation")}</code></div>`}
              <div><b>Sign in</b><code>${escapeHtml(provider.login_command || "Open the provider CLI")}</code></div>
              <p>Run this yourself in a trusted terminal. The Web UI does not execute setup commands or store credentials.</p>
            </details>
          `}
        </div>
      </article>
    `;
  }).join("");
  hydrateOptionalAssets(target);
}

function jobStatus(job) {
  return ["running", "queued", "completed", "failed", "cancelled", "blocked"].includes(job.status) ? job.status : "queued";
}

function jobContext(job) {
  if (job.source?.type === "brief") return "Task only";
  if (job.source?.type === "github") return job.source.repository || "GitHub repository";
  return shortPath(job.workspace);
}

function stateVisual(job) {
  if (job.status === "completed" || job.status === "imported") return { key: "complete", label: "Complete" };
  if (job.status === "queued") return { key: "queued", label: "Queued" };
  const phase = String(job.phase || "").toLowerCase();
  if (phase.includes("layer3") || phase.includes("synth") || phase.includes("aggreg") || phase.includes("decide")) {
    return { key: "synthesis", label: "Synthesis" };
  }
  if (phase.includes("layer2") || phase.includes("review") || phase.includes("refin")) {
    return { key: "review", label: "Review" };
  }
  if (phase.includes("layer1") || phase.includes("parallel") || phase.includes("propos")) {
    return { key: "parallel", label: "Parallel work" };
  }
  return { key: "scouting", label: "Scouting" };
}

function jobRosterText(job) {
  const roster = job.roster || {};
  const proposerCount = Array.isArray(roster.proposers) ? roster.proposers.length : Number(job.proposer_count || 0);
  const refinerCount = Array.isArray(roster.refiners) ? roster.refiners.length : Number(job.refiner_count || 0);
  return proposerCount || refinerCount ? `${proposerCount}P · ${refinerCount}R` : job.model_count ? `${job.model_count} agents` : "Ensemble";
}

function activeJob() {
  return state.jobs.find((job) => ["running", "queued", "cancelling"].includes(job.status));
}

function renderActiveJob() {
  if (state.loading.jobs) return;
  const target = $("#active-run-card");
  target.setAttribute("aria-busy", "false");
  const job = activeJob();
  if (!job) {
    target.className = "empty-state compact";
    target.innerHTML = `
      <span class="empty-number">00</span>
      <div><h3>The runway is clear</h3><p>No run is active. Start with a task, choose your roster, and send it.</p></div>
      <button class="primary-button" type="button" data-route-button="new">Create a run</button>
    `;
    return;
  }
  const visual = stateVisual(job);
  target.className = "active-card";
  target.innerHTML = `
    <div class="active-card-head">
      <img class="active-state-art" src="/static/images/state-${visual.key}.webp" alt="">
      <div><p class="eyebrow">${escapeHtml(String(job.phase).toUpperCase())}</p><h3>${escapeHtml(job.title)}</h3></div>
      <span class="status-tag ${jobStatus(job)}">${escapeHtml(titleCase(job.status))}</span>
    </div>
    <div class="progress-track" aria-label="${Math.round(job.progress)} percent complete"><i style="width:${job.progress}%"></i></div>
    <div class="active-meta">
      <span>Context<strong>${escapeHtml(jobContext(job))}</strong></span>
      <span>Roster<strong>${escapeHtml(jobRosterText(job))}</strong></span>
      <span>Elapsed<strong data-elapsed="${escapeHtml(job.startedAt || job.createdAt)}">${escapeHtml(formatDuration(job.startedAt || job.createdAt))}</strong></span>
      <span>Progress<strong>${Math.round(job.progress)}%</strong></span>
    </div>
    <div class="active-card-foot">
      <p>${escapeHtml(job.summary || `The ensemble is working through ${titleCase(job.phase)}.`)}</p>
      <button class="primary-button" type="button" data-open-run="${escapeHtml(job.id)}">Watch this run</button>
    </div>
  `;
}

function renderRecentRuns() {
  if (state.loading.jobs) return;
  const target = $("#recent-runs");
  target.setAttribute("aria-busy", "false");
  const recent = state.jobs.filter((job) => !["running", "queued"].includes(job.status)).slice(0, 5);
  if (!recent.length) {
    target.innerHTML = `<div class="empty-state"><span class="empty-number">—</span><div><h3>No completed runs yet</h3><p>Your local archive will collect final plans, reports, and traces here.</p></div></div>`;
    return;
  }
  target.innerHTML = recent.map((job) => `
    <a class="run-row" href="/runs/${encodeURIComponent(job.id)}" data-open-run="${escapeHtml(job.id)}">
      <img class="run-state-thumb" src="/static/images/state-${stateVisual(job).key}.webp" alt="">
      <div><h3>${escapeHtml(job.title)}</h3><p>${escapeHtml(jobContext(job))} · ${escapeHtml(jobRosterText(job))}</p></div>
      <time datetime="${escapeHtml(job.finishedAt || job.createdAt || "")}">${escapeHtml(formatTime(job.finishedAt || job.createdAt))}</time>
      <span class="status-tag ${jobStatus(job)}">${escapeHtml(titleCase(job.status))}</span>
    </a>
  `).join("");
}

function filteredJobs() {
  const term = ($("#run-search")?.value || "").trim().toLowerCase();
  const status = $("#run-status-filter")?.value || "";
  return state.jobs.filter((job) => {
    const haystack = `${job.title} ${job.goal} ${jobContext(job)} ${job.workspace} ${jobRosterText(job)}`.toLowerCase();
    return (!term || haystack.includes(term)) && (!status || job.status === status);
  });
}

function renderArchive() {
  if (state.loading.jobs) return;
  const jobs = filteredJobs();
  $("#run-archive").setAttribute("aria-busy", "false");
  $("#run-result-count").textContent = `${jobs.length} run${jobs.length === 1 ? "" : "s"}`;
  $("#run-archive").innerHTML = jobs.length ? jobs.map((job) => `
    <tr>
      <td><div class="archive-run-cell"><img src="/static/images/state-${stateVisual(job).key}.webp" alt=""><div><span class="archive-title">${escapeHtml(job.title)}</span><span class="archive-id">${escapeHtml(job.id.slice(0, 12))}</span></div></div></td>
      <td>${escapeHtml(jobContext(job))}</td>
      <td>${escapeHtml(jobRosterText(job))}</td>
      <td>${escapeHtml(formatTime(job.startedAt || job.createdAt))}</td>
      <td><span class="status-tag ${jobStatus(job)}">${escapeHtml(titleCase(job.status))}</span></td>
      <td><a class="table-link" href="/runs/${encodeURIComponent(job.id)}" data-open-run="${escapeHtml(job.id)}">Open →</a></td>
    </tr>
  `).join("") : `<tr><td colspan="6">No runs match this view.</td></tr>`;
}

function modelsForRole(role) {
  const endpointModels = state.models.filter((model) => {
    if (!model.id || String(model.id).includes(":")) return false;
    const id = String(model.id);
    const harness = model.harness || model.provider_id || model.provider || "cli";
    if (harness === "gemini" || id === "gemini-cli-pro") return false;
    if (harness === "cursor" && !["composer", "cursor-grok"].includes(id)) return false;
    if (harness === "codex" && !["codex", "codex-sol", "codex-luna"].includes(id)) return false;
    if (["codex-reviewer", "codex-aggregator"].includes(id)) return false;
    if (role === "aggregator") return ["opus", "codex-sol"].includes(id);
    return true;
  }).map((model) => {
    const harness = model.harness || model.provider_id || model.provider || "cli";
    const provider = state.providers.find((item) => item.id === harness);
    return {
      id: model.id,
      name: model.name || model.label || model.id,
      model: model.canonicalModel || model.canonical_model || model.model || model.model_id || model.id,
      effort: model.effort || model.reasoning_effort || model.variant || "default",
      effortOptions: model.effort_options || model.effortOptions || [],
      effortControl: model.effort_control || model.effortControl || "model_id",
      lab: model.lab || model.vendor || model.providerId || model.provider_id || model.provider || model.id,
      harness,
      available: model.available ?? model.ready ?? providerReady(provider || {}),
      availabilityDetail: model.availability_detail || model.availabilityDetail || "",
      default: model.defaultRoles?.includes(role) || model.default_roles?.includes(role) || model.default === role || model.default === true,
    };
  });
  if (endpointModels.length) return endpointModels;
  return state.providers
    .filter((provider) => provider.id !== "gemini" && (role !== "aggregator" || ["claude", "codex"].includes(provider.harness)))
    .flatMap((provider) => {
      const models = provider.models.length ? provider.models : [{ id: provider.id, name: provider.name }];
      return models.slice(0, 1).map((model) => ({
        id: provider.id,
        name: provider.name,
        model: model.name || model.id,
        effort: model.effort || "default",
        effortOptions: model.effort_options || [],
        effortControl: model.effort_control || "model_id",
        lab: provider.lab,
        harness: provider.harness,
        available: providerReady(provider),
        default: provider.default_roles?.includes?.(role) || false,
      }));
    });
}

function defaultSelected(option, role, index) {
  const configured = bootstrap.defaults?.[`${role}s`] || bootstrap.defaults?.[role];
  if (Array.isArray(configured)) return configured.includes(option.id);
  if (typeof configured === "string") return configured === option.id;
  if (option.default) return true;
  const standard = {
    proposer: ["codex", "glm", "sonnet"],
    refiner: ["codex-sol", "qwen"],
    aggregator: ["opus"],
  };
  if (standard[role].includes(option.id)) return true;
  if (role === "aggregator" && !modelsForRole(role).some((item) => standard.aggregator.includes(item.id))) return index === 0;
  return false;
}

function displayModelName(option) {
  const model = option?.model || "";
  return option?.harness === "agy" ? model.replace(/-(?:low|medium|high)$/i, "") : model;
}

function renderModelOptions(role, targetId, inputType) {
  const options = modelsForRole(role);
  const target = $(targetId);
  target.setAttribute("aria-busy", "false");
  const groups = options.reduce((map, option) => {
    const key = option.harness || option.lab || "other";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(option);
    return map;
  }, new Map());
  let optionIndex = 0;
  target.innerHTML = options.length ? [...groups].map(([group, items]) => {
    const provider = state.providers.find((item) => item.id === group);
    const providerName = provider?.name || titleCase(group);
    const avatarPath = PROVIDER_AVATARS[group];
    const initials = providerName.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
    const groupReady = items.some((option) => option.available);
    const groupOpen = groupReady && items.some((option, index) => defaultSelected(option, role, optionIndex + index));
    const providerSearch = [providerName, group, items[0]?.lab].join(" ").toLowerCase();
    return `
      <details class="model-provider-group${groupReady ? "" : " is-unavailable"}" data-model-group data-provider-search="${escapeHtml(providerSearch)}" ${groupReady ? "" : "data-unavailable"} ${groupOpen ? "open" : ""}>
        <summary class="model-provider-head" aria-disabled="${String(!groupReady)}">
          <span class="chooser-avatar">
            ${avatarPath ? `<img src="${avatarPath}" alt="">` : ""}
            <b>${escapeHtml(initials)}</b>
          </span>
          <span class="provider-summary-copy">
            <strong>${escapeHtml(providerName)}</strong>
            <small>${escapeHtml(titleCase(items[0]?.lab || group))} · ${items.length} route${items.length === 1 ? "" : "s"}</small>
          </span>
          <span class="provider-ready-count">${items.filter((item) => item.available).length}/${items.length} ready</span>
        </summary>
        <div class="model-provider-options">
          ${items.map((option) => {
            const index = optionIndex++;
            const displayModel = displayModelName(option);
            const canChooseEffort = option.effortOptions.length > 0
              && option.effortControl === "flag"
              && !["cursor", "gemini", "opencode"].includes(option.harness);
            const rowSearch = `${option.name} ${option.model} ${option.lab} ${option.harness}`.toLowerCase();
            return `
              <div class="model-option" data-model-row data-search="${escapeHtml(rowSearch)}">
                <input
                  class="route-choice"
                  type="${inputType}"
                  id="${role}-${escapeHtml(option.id)}-${index}"
                  name="${role}"
                  value="${escapeHtml(option.id)}"
                  data-lab="${escapeHtml(option.lab)}"
                  data-model="${escapeHtml(option.model)}"
                  data-effort="${escapeHtml(option.effort)}"
                  data-default-effort="${escapeHtml(option.effort)}"
                  ${defaultSelected(option, role, index) ? "checked" : ""}
                  ${option.available ? "" : "disabled"}
                >
                <label class="model-row-main" for="${role}-${escapeHtml(option.id)}-${index}">
                  <span class="selection-box" aria-hidden="true"></span>
                  <span class="model-row-copy">
                    <strong>${escapeHtml(option.name)}</strong>
                    <small>${escapeHtml(displayModel)} · ${canChooseEffort ? "Adjustable effort" : `${escapeHtml(titleCase(option.effort))} effort`}${option.available || !option.availabilityDetail ? "" : ` · ${escapeHtml(option.availabilityDetail)}`}</small>
                  </span>
                  <span class="model-row-state ${option.available ? "" : "unavailable"}">${option.available ? "Selected" : "Unavailable"}</span>
                </label>
                ${canChooseEffort ? `
                  <fieldset class="effort-slider" data-effort-control hidden>
                    <legend>Reasoning effort</legend>
                    <div class="effort-slider-control">
                      <input
                        type="range"
                        min="0"
                        max="${option.effortOptions.length - 1}"
                        step="1"
                        value="${Math.max(0, option.effortOptions.indexOf(option.effort))}"
                        data-effort-range
                        data-effort-values="${escapeHtml(option.effortOptions.join(","))}"
                        aria-label="Reasoning effort for ${escapeHtml(option.name)}"
                        aria-valuetext="${escapeHtml(titleCase(option.effort))}"
                      >
                      <output data-effort-output>${escapeHtml(titleCase(option.effort))}</output>
                    </div>
                    <div class="effort-slider-stops" aria-hidden="true">
                      ${option.effortOptions.map((effort) => `<span>${escapeHtml(titleCase(effort))}</span>`).join("")}
                    </div>
                  </fieldset>
                ` : ""}
              </div>
            `;
          }).join("")}
        </div>
      </details>
    `;
  }).join("") : `<p>No ${escapeHtml(role)} models are available.</p>`;
}

function selectedValues(name) {
  return $$(`input[name="${name}"]:checked`, $("#launch-form")).map((input) => input.value);
}

function selectedModelOverrides() {
  const overrides = {};
  $$('input[name="proposer"]:checked, input[name="refiner"]:checked, input[name="aggregator"]:checked', $("#launch-form"))
    .forEach((input) => { if (input.dataset.model) overrides[input.value] = input.dataset.model; });
  return overrides;
}

function selectedEffortOverrides() {
  const overrides = {};
  $$('input[name="proposer"]:checked, input[name="refiner"]:checked, input[name="aggregator"]:checked', $("#launch-form"))
    .forEach((input) => {
      const selected = selectedEffortValue(input.closest(".model-option"));
      if (selected) overrides[input.value] = selected;
    });
  return overrides;
}

function selectedEffortValue(card) {
  const range = card?.querySelector("[data-effort-range]");
  if (range) {
    const values = (range.dataset.effortValues || "").split(",").filter(Boolean);
    return values[Number(range.value)] || values[0];
  }
  return card?.querySelector("[data-effort-choice]:checked")?.value;
}

function updateEffortSlider(range) {
  const values = (range.dataset.effortValues || "").split(",").filter(Boolean);
  const value = values[Number(range.value)] || values[0] || "default";
  range.setAttribute("aria-valuetext", titleCase(value));
  const output = range.closest("[data-effort-control]")?.querySelector("[data-effort-output]");
  if (output) output.textContent = titleCase(value);
}

function selectedRouteDescriptions(name) {
  return $$(`input[name="${name}"]:checked`, $("#launch-form")).map((input) => {
    const route = state.models.find((model) => model.id === input.value);
    const effort = selectedEffortValue(input.closest(".model-option")) || input.dataset.effort;
    const label = route?.name || titleCase(input.dataset.model || input.value);
    const model = route ? displayModelName(route) : input.dataset.model;
    return `${label}${model ? ` · ${model}` : ""}${effort && effort !== "default" ? ` · ${titleCase(effort)} effort` : ""}`;
  });
}

function syncEffortControls() {
  $$(".model-option").forEach((card) => {
    const route = $(".route-choice", card);
    const control = $("[data-effort-control]", card);
    if (!control || !route) return;
    control.hidden = !route.checked || route.disabled;
    $$("[data-effort-choice], [data-effort-range]", control).forEach((choice) => { choice.disabled = control.hidden; });
  });
}

function selectRoutes(role, preferred, limit) {
  const inputs = $$(`.route-choice[name="${role}"]`);
  const available = inputs.filter((input) => !input.disabled);
  const chosen = [];
  preferred.forEach((id) => {
    const match = available.find((input) => input.value === id);
    if (match && !chosen.includes(match)) chosen.push(match);
  });
  available.forEach((input) => {
    if (chosen.length < limit && !chosen.includes(input)) chosen.push(input);
  });
  inputs.forEach((input) => { input.checked = chosen.includes(input); });
}

function setSelectedEffort(mode, role = "") {
  const selector = role ? `.route-choice[name="${role}"]:checked` : ".route-choice:checked";
  $$(selector).forEach((route) => {
    const range = $("[data-effort-range]", route.closest(".model-option"));
    if (range) {
      const values = (range.dataset.effortValues || "").split(",").filter(Boolean);
      const desired = mode === "quick" ? "medium"
        : mode === "thorough" ? "high"
        : route.dataset.defaultEffort;
      const index = Math.max(0, values.includes(desired) ? values.indexOf(desired) : values.indexOf("high"));
      range.value = String(index);
      updateEffortSlider(range);
      return;
    }
    const choices = $$("[data-effort-choice]", route.closest(".model-option"));
    if (!choices.length) return;
    const desired = mode === "quick" ? "medium"
      : mode === "thorough" ? "high"
      : route.dataset.defaultEffort;
    const selected = choices.find((choice) => choice.value === desired)
      || choices.find((choice) => choice.value === "high")
      || choices[0];
    choices.forEach((choice) => { choice.checked = choice === selected; });
  });
}

function syncDepthPresentation(preset) {
  const presentation = DEPTH_PRESENTATION[preset] || DEPTH_PRESENTATION.balanced;
  const range = $("#depth-range");
  if (!range) return;
  range.value = String(presentation.value);
  range.setAttribute("aria-valuetext", titleCase(preset));
  $("#depth-output").textContent = titleCase(preset);
  $("#depth-illustration").src = presentation.image;
  $("#depth-caption").textContent = presentation.caption;
}

function optimizedProfile(preset) {
  const profiles = {
    quick: {
      proposer: { preferred: ["codex", "sonnet"], limit: 2 },
      refiner: { preferred: ["qwen"], limit: 1 },
      aggregator: { preferred: ["opus"], limit: 1 },
    },
    balanced: {
      proposer: { preferred: ["codex", "glm", "sonnet"], limit: 3 },
      refiner: { preferred: ["codex-sol", "qwen"], limit: 2 },
      aggregator: { preferred: ["opus"], limit: 1 },
    },
    thorough: {
      proposer: { preferred: ["codex", "glm", "sonnet", "agy-gemini-pro"], limit: 4 },
      refiner: { preferred: ["codex-sol", "qwen", "agy-gemini-pro"], limit: 3 },
      aggregator: { preferred: ["opus"], limit: 1 },
    },
  };
  return profiles[preset] || profiles.balanced;
}

function applyOptimizedRole(role, announce = true) {
  const config = optimizedProfile(state.depthPreset)[role];
  if (!config) return;
  selectRoutes(role, config.preferred, config.limit);
  syncEffortControls();
  setSelectedEffort(state.depthPreset, role);
  updateRosterChecks();
  if (announce) showToast(`Optimized ${role} loadout restored for ${titleCase(state.depthPreset)} depth.`);
}

function applyDepthPreset(preset, announce = true) {
  state.depthPreset = preset;
  syncDepthPresentation(preset);
  ["proposer", "refiner", "aggregator"].forEach((role) => applyOptimizedRole(role, false));
  if (announce) showToast(`${titleCase(preset)} depth applied to the roster.`);
}

function renderRoster() {
  if (state.loading.providers || state.loading.models) return;
  renderModelOptions("proposer", "#proposer-options", "checkbox");
  renderModelOptions("refiner", "#refiner-options", "checkbox");
  renderModelOptions("aggregator", "#aggregator-options", "radio");
  applyDepthPreset(state.depthPreset, false);
}

function updateRosterChecks() {
  const proposers = $$('input[name="proposer"]:checked');
  const refiners = $$('input[name="refiner"]:checked');
  const aggregator = $('input[name="aggregator"]:checked');
  $("#proposer-count").textContent = `${proposers.length} selected`;
  $("#refiner-count").textContent = `${refiners.length} selected`;
  const callout = $("#independence-callout");
  if (!aggregator || !refiners.length) {
    callout.className = "independence-callout";
    callout.innerHTML = `<span>LAB CHECK</span><p>Select refiners and an aggregator to check independence.</p>`;
    return;
  }
  const overlaps = refiners.filter((input) => input.dataset.lab === aggregator.dataset.lab);
  if (overlaps.length) {
    callout.className = "independence-callout is-warning";
    callout.innerHTML = `<span>SHARED LAB</span><p>${overlaps.length} refiner${overlaps.length === 1 ? "" : "s"} share the aggregator’s lab. Consider an independent reviewer for a stronger check.</p>`;
  } else {
    callout.className = "independence-callout";
    callout.innerHTML = `<span>INDEPENDENT</span><p>Your refinement lanes are lab-independent from the aggregator.</p>`;
  }
}

function setSourceMode(mode) {
  state.sourceMode = ["brief", "github"].includes(mode) ? mode : "brief";
  $$(".source-tab").forEach((tab) => {
    const active = tab.dataset.sourceMode === state.sourceMode;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-pressed", String(active));
  });
  $("#brief-source").hidden = state.sourceMode !== "brief";
  $("#github-source").hidden = state.sourceMode !== "github";
}

function renderGithubRepos() {
  const select = $("#github-repo");
  if (!state.githubRepos.length) {
    select.innerHTML = `<option value="">GitHub source unavailable</option>`;
    $("#github-repo-meta").textContent = `Connect GitHub CLI to browse ${bootstrap.github_owner || "the configured owner"} repositories.`;
    return;
  }
  select.innerHTML = `<option value="">Choose a repository…</option>${state.githubRepos.map((repo) => `
    <option value="${escapeHtml(repo.fullName)}">${escapeHtml(repo.name)}${repo.private ? " · private" : ""}</option>
  `).join("")}`;
  updateGithubMeta();
}

function selectedGithubRepo() {
  return state.githubRepos.find((repo) => repo.fullName === $("#github-repo").value);
}

function updateGithubMeta() {
  const repo = selectedGithubRepo();
  if (!repo) {
    $("#github-repo-meta").textContent = "Repository metadata comes from the connected GitHub service.";
    return;
  }
  $("#github-repo-meta").textContent = `${repo.description || "No description"}${repo.pushedAt ? ` · Updated ${formatTime(repo.pushedAt)}` : ""}`;
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderAttachments() {
  $("#attachment-list").innerHTML = state.pendingFiles.map((file, index) => `
    <div class="attachment-item">
      <strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong>
      <span>${escapeHtml(formatFileSize(file.size))}</span>
      <button class="attachment-remove" type="button" data-remove-file="${index}">Remove</button>
    </div>
  `).join("");
}

function addFiles(fileList) {
  const incoming = [...fileList];
  const allowed = incoming.filter((file) => file.size <= 25 * 1024 * 1024);
  const rejected = incoming.length - allowed.length;
  const known = new Set(state.pendingFiles.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
  allowed.forEach((file) => {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (state.pendingFiles.length < 10 && !known.has(key)) {
      state.pendingFiles.push(file);
      known.add(key);
    }
  });
  if (rejected) showToast(`${rejected} file${rejected === 1 ? " was" : "s were"} over the 25 MB limit.`, "error");
  if (state.pendingFiles.length === 10 && incoming.length) showToast("Reference files are capped at 10.");
  renderAttachments();
}

function renderReview() {
  const goal = $("#run-goal").value.trim();
  const proposers = selectedValues("proposer");
  const refiners = selectedValues("refiner");
  const aggregator = selectedValues("aggregator")[0];
  const proposerRoutes = selectedRouteDescriptions("proposer");
  const refinerRoutes = selectedRouteDescriptions("refiner");
  const aggregatorRoute = selectedRouteDescriptions("aggregator")[0];
  const repo = selectedGithubRepo();
  const source = state.sourceMode === "github"
    ? (repo ? `GitHub · ${repo.fullName}` : "GitHub repository")
    : "Task only";
  const networkNodes = (role) => $$(`input[name="${role}"]:checked`).map((input) => {
    const route = modelsForRole(role).find((item) => item.id === input.value);
    const harness = route?.harness || input.value;
    return {
      id: input.value,
      name: route?.name || titleCase(input.value),
      model: displayModelName(route) || input.dataset.model || input.value,
      harness,
      image: REVIEW_PIXEL_ART[harness] || PROVIDER_AVATARS[harness] || "/static/images/moax-mark.webp",
    };
  });
  const renderNetworkLayer = (label, title, nodes) => `
    <section class="network-layer">
      <header><span>${escapeHtml(label)}</span><strong>${escapeHtml(title)}</strong></header>
      <div class="network-node-list">${nodes.map((node) => `
        <article class="network-node">
          <span class="network-avatar" data-harness="${escapeHtml(node.harness)}"><img src="${escapeHtml(node.image)}" alt=""></span>
          <div><strong title="${escapeHtml(node.name)}">${escapeHtml(node.name)}</strong><small title="${escapeHtml(node.model)}">${escapeHtml(node.model)}</small></div>
        </article>
      `).join("")}</div>
    </section>
  `;
  const proposersNetwork = networkNodes("proposer");
  const refinersNetwork = networkNodes("refiner");
  const aggregatorNetwork = networkNodes("aggregator");
  $("#review-network").innerHTML = `
    ${renderNetworkLayer("Layer 1", "Proposers", proposersNetwork)}
    <div class="network-bridge" aria-hidden="true"><span>Share</span></div>
    ${renderNetworkLayer("Layer 2", "Refiners", refinersNetwork)}
    <div class="network-bridge" aria-hidden="true"><span>Unify</span></div>
    ${renderNetworkLayer("Layer 3", "Aggregator", aggregatorNetwork)}
    <div class="network-context">
      <img src="/static/images/context-roster.webp" alt="">
      <div><strong>${escapeHtml(titleCase(state.depthPreset))} ensemble · ${proposersNetwork.length + refinersNetwork.length + aggregatorNetwork.length} agents</strong><p>${escapeHtml(source)} flows through independent proposals, broadcast review, and one evidence-backed synthesis.</p></div>
    </div>
  `;
  $("#review-sheet").innerHTML = `
    <dl>
      <div class="review-row"><dt>Goal</dt><dd>${escapeHtml(goal || "Add a task")}</dd></div>
      <div class="review-row"><dt>Context</dt><dd>${escapeHtml(source)}</dd></div>
      <div class="review-row"><dt>Reference files</dt><dd>${escapeHtml(state.pendingFiles.length ? `${state.pendingFiles.length} file${state.pendingFiles.length === 1 ? "" : "s"}` : "None")}</dd></div>
      <div class="review-row"><dt>Planning depth</dt><dd>${escapeHtml(titleCase(state.depthPreset))} · roster shown below</dd></div>
      <div class="review-row"><dt>Proposers</dt><dd>${escapeHtml(proposerRoutes.join(", ") || proposers.join(", ") || "Choose at least one")}</dd></div>
      <div class="review-row"><dt>Refiners</dt><dd>${escapeHtml(refinerRoutes.join(", ") || refiners.join(", ") || "Choose at least one")}</dd></div>
      <div class="review-row"><dt>Aggregator</dt><dd>${escapeHtml(aggregatorRoute || aggregator || "Choose one")}</dd></div>
      <div class="review-row"><dt>Local profile</dt><dd>${escapeHtml(state.profile.name)}</dd></div>
    </dl>
  `;
}

function setStep(step) {
  state.step = Math.max(1, Math.min(5, step));
  $$(".wizard-step").forEach((node) => node.classList.toggle("is-active", Number(node.dataset.step) === state.step));
  $$(".step-item").forEach((node) => {
    const active = node.dataset.stepGroup === "roster"
      ? state.step >= 2 && state.step <= 4
      : Number(node.dataset.stepTarget) === state.step;
    node.classList.toggle("is-active", active);
    if (active) node.setAttribute("aria-current", "step"); else node.removeAttribute("aria-current");
  });
  $("#roster-subnav").hidden = state.step < 2 || state.step > 4;
  $$("[data-roster-step]").forEach((node) => {
    const active = Number(node.dataset.rosterStep) === state.step;
    node.classList.toggle("is-active", active);
    if (active) node.setAttribute("aria-current", "step"); else node.removeAttribute("aria-current");
  });
  if (state.step === 5) renderReview();
  $(".wizard-step.is-active h2")?.focus?.({ preventScroll: true });
  const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  requestAnimationFrame(() => window.scrollTo({ top: 0, behavior }));
}

function validateStep(step) {
  if (step === 1) {
    if (!$("#run-goal").value.trim()) return showToast("Add a task or outcome before continuing.", "error"), false;
    if (state.sourceMode === "github" && !$("#github-repo").value) return showToast("Choose a Driveline Research repository or use Task only.", "error"), false;
  }
  if (step === 2) {
    if (!selectedValues("proposer").length) return showToast("Choose at least one proposer.", "error"), false;
  }
  if (step === 3) {
    if (!selectedValues("refiner").length) return showToast("Choose at least one refiner.", "error"), false;
  }
  if (step === 4) {
    if (!selectedValues("aggregator").length) return showToast("Choose an aggregator.", "error"), false;
  }
  return true;
}

function coachModelLabel(model = {}) {
  return model.fallback ? "DeepSeek V4 Flash · backup" : "GPT-5.6 Luna";
}

function renderCoachProgress(stage) {
  const active = stage === "analyze" ? 1 : stage === "questions" ? 2 : 3;
  $("#prompt-coach-progress").innerHTML = [1, 2, 3]
    .map((step) => `<span class="${step <= active ? "is-active" : ""}"></span>`)
    .join("");
}

function renderCoachLoading(title, detail, stage = "analyze") {
  renderCoachProgress(stage);
  const body = $("#prompt-coach-body");
  body.setAttribute("aria-busy", "true");
  body.innerHTML = `
    <div class="coach-loading" role="status">
      <div class="coach-process-visual" aria-hidden="true">
        <div class="coach-process-questions"><i>?</i><i>?</i><i>?</i></div>
        <div class="coach-process-stream"><b></b><b></b><b></b></div>
        <div class="coach-process-brief"><span></span><span></span><span></span><span></span></div>
      </div>
      <div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(detail)}</p></div>
    </div>
  `;
}

function coachNotes(title, items = []) {
  const values = Array.isArray(items) ? items : [];
  return `<div><strong>${escapeHtml(title)}</strong>${
    values.length
      ? `<ul>${values.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : `<p class="muted">None noted.</p>`
  }</div>`;
}

async function finishPromptCoach() {
  const coach = state.promptCoach;
  renderCoachLoading("Drafting the stronger brief", "Turning your choices into a precise mission without changing your intent.", "preview");
  try {
    coach.result = await finalizePrompt({
      brief: coach.original,
      questions: coach.analysis?.questions || [],
      answers: coach.answers,
      context_mode: state.sourceMode,
      attachment_count: state.pendingFiles.length,
    });
    renderCoachPreview();
  } catch (error) {
    $("#prompt-coach-body").removeAttribute("aria-busy");
    $("#prompt-coach-body").innerHTML = `
      <div class="coach-question"><p class="eyebrow">COACH UNAVAILABLE</p><h3>The draft is still safe.</h3>
      <p>${escapeHtml(error.message)}</p><div class="coach-actions"><button class="secondary-button" id="coach-error-close" type="button">Keep my draft</button><button class="primary-button" id="coach-error-retry" type="button">Try again</button></div></div>`;
    $("#coach-error-close").addEventListener("click", () => $("#prompt-coach-dialog").close());
    $("#coach-error-retry").addEventListener("click", finishPromptCoach);
  }
}

function renderCoachQuestion() {
  const coach = state.promptCoach;
  const questions = coach.analysis?.questions || [];
  const question = questions[coach.index];
  if (!question) return finishPromptCoach();
  renderCoachProgress("questions");
  const body = $("#prompt-coach-body");
  body.removeAttribute("aria-busy");
  const prior = coach.answers[coach.index]?.answer || "";
  const recommended = question.options.find((option) => option.recommended)?.label || question.options[0]?.label || "";
  const selected = prior || recommended;
  body.innerHTML = `
    <div class="coach-question">
      <p class="eyebrow">QUESTION ${coach.index + 1} OF ${questions.length} · ${escapeHtml(coachModelLabel(coach.analysis.model))}</p>
      <h3>${escapeHtml(question.prompt)}</h3>
      <p>${escapeHtml(question.why)}</p>
      <div class="coach-options">
        ${question.options.map((option, index) => `
          <label class="coach-option">
            <input type="radio" name="coach-answer" value="${escapeHtml(option.label)}" ${selected === option.label ? "checked" : ""}>
            <span><strong>${escapeHtml(option.label)}</strong><small>${escapeHtml(option.description)}</small></span>
            ${option.recommended ? "<em>Recommended</em>" : ""}
          </label>`).join("")}
        ${question.allow_custom ? `
          <label class="coach-option coach-custom-option">
            <input type="radio" name="coach-answer" value="__custom" ${prior && !question.options.some((option) => option.label === prior) ? "checked" : ""}>
            <span><strong>Something else</strong><small>Write the answer in your own words.</small></span>
          </label>
          <div class="field coach-custom"><input id="coach-custom-answer" maxlength="1000" placeholder="Add your answer…" value="${prior && !question.options.some((option) => option.label === prior) ? escapeHtml(prior) : ""}"></div>` : ""}
      </div>
      <div class="coach-actions">
        <button class="secondary-button" id="coach-back" type="button" ${coach.index === 0 ? "disabled" : ""}>Back</button>
        <button class="primary-button" id="coach-next" type="button">${coach.index === questions.length - 1 ? "Build preview" : "Next question"}</button>
      </div>
    </div>`;
  $("#coach-custom-answer")?.addEventListener("focus", () => {
    $('input[name="coach-answer"][value="__custom"]')?.click();
  });
  $("#coach-back").addEventListener("click", () => {
    coach.index = Math.max(0, coach.index - 1);
    renderCoachQuestion();
  });
  $("#coach-next").addEventListener("click", () => {
    const picked = $('input[name="coach-answer"]:checked');
    let answer = picked?.value || "";
    if (answer === "__custom") answer = $("#coach-custom-answer")?.value.trim() || "";
    if (!answer) return showToast("Choose an answer or add your own.", "error");
    coach.answers[coach.index] = { question_id: question.id, answer };
    if (coach.index < questions.length - 1) {
      coach.index += 1;
      renderCoachQuestion();
    } else {
      finishPromptCoach();
    }
  });
}

function renderCoachAnalysis() {
  const coach = state.promptCoach;
  const questions = coach.analysis?.questions || [];
  if (questions.length) return renderCoachQuestion();
  renderCoachProgress("questions");
  const body = $("#prompt-coach-body");
  body.removeAttribute("aria-busy");
  body.innerHTML = `
    <div class="coach-question">
      <p class="eyebrow">BRIEF SCORE · ${Number(coach.analysis?.score || 0)}/100 · ${escapeHtml(coachModelLabel(coach.analysis?.model))}</p>
      <h3>No clarification needed.</h3>
      <p>${escapeHtml(coach.analysis?.summary || "This task is ready for the ensemble.")}</p>
      <div class="coach-actions"><button class="secondary-button" id="coach-ready-close" type="button">Keep as written</button><button class="primary-button" id="coach-ready-polish" type="button">Create polished preview</button></div>
    </div>`;
  $("#coach-ready-close").addEventListener("click", () => $("#prompt-coach-dialog").close());
  $("#coach-ready-polish").addEventListener("click", finishPromptCoach);
}

function renderCoachPreview() {
  const coach = state.promptCoach;
  renderCoachProgress("preview");
  const body = $("#prompt-coach-body");
  body.removeAttribute("aria-busy");
  body.innerHTML = `
    <div class="coach-question">
      <p class="eyebrow">PREVIEW · ${escapeHtml(coachModelLabel(coach.result?.model))}</p>
      <h3>Your intent, made ensemble-ready.</h3>
      <div class="coach-preview-grid">
        <article class="coach-preview-card"><h3>Original</h3><pre>${escapeHtml(coach.original)}</pre></article>
        <article class="coach-preview-card is-optimized"><h3>Optimized brief</h3><pre>${escapeHtml(coach.result.optimized_prompt)}</pre></article>
      </div>
      <div class="coach-notes">
        ${coachNotes("What changed", coach.result.changes)}
        ${coachNotes("Assumptions", coach.result.assumptions)}
        ${coachNotes("Still worth checking", coach.result.remaining_risks)}
      </div>
      <div class="coach-actions"><button class="secondary-button" id="coach-keep-original" type="button">Keep original</button><button class="primary-button" id="coach-apply" type="button">Use optimized brief</button></div>
    </div>`;
  $("#coach-keep-original").addEventListener("click", () => $("#prompt-coach-dialog").close());
  $("#coach-apply").addEventListener("click", () => {
    coach.undo = $("#run-goal").value;
    $("#run-goal").value = coach.result.optimized_prompt;
    $("#goal-count").textContent = $("#run-goal").value.length;
    localStorage.setItem("moax.runDraft", $("#run-goal").value);
    $("#prompt-undo-button").hidden = false;
    $("#prompt-coach-dialog").close();
    showToast("Optimized brief applied. You can still edit or undo it.");
  });
}

async function openPromptCoach() {
  const brief = $("#run-goal").value.trim();
  if (!brief) return showToast("Add a task or outcome before strengthening it.", "error");
  state.promptCoach = { original: brief, analysis: null, answers: [], index: 0, result: null, undo: state.promptCoach.undo || "" };
  $("#prompt-coach-dialog").showModal();
  renderCoachLoading("Reading the mission", "Luna is checking fit, ambiguity, constraints, and the decisions your ensemble needs to make.");
  try {
    state.promptCoach.analysis = await analyzePrompt({
      brief,
      context_mode: state.sourceMode,
      attachment_count: state.pendingFiles.length,
    });
    renderCoachAnalysis();
  } catch (error) {
    $("#prompt-coach-body").removeAttribute("aria-busy");
    $("#prompt-coach-body").innerHTML = `
      <div class="coach-question"><p class="eyebrow">COACH UNAVAILABLE</p><h3>Your draft was not changed.</h3><p>${escapeHtml(error.message)}</p>
      <div class="coach-actions"><button class="secondary-button" id="coach-analyze-close" type="button">Close</button><button class="primary-button" id="coach-analyze-retry" type="button">Try again</button></div></div>`;
    $("#coach-analyze-close").addEventListener("click", () => $("#prompt-coach-dialog").close());
    $("#coach-analyze-retry").addEventListener("click", () => {
      $("#prompt-coach-dialog").close();
      openPromptCoach();
    });
  }
}

async function launchRun(event) {
  event.preventDefault();
  if (![1, 2, 3, 4].every(validateStep)) return;
  const button = $("#launch-button");
  setButtonLoading(button, true, "Dispatching…", "Start run");
  const proposers = selectedValues("proposer");
  const refiners = selectedValues("refiner");
  const aggregator = selectedValues("aggregator")[0];
  const repo = selectedGithubRepo();
  const payload = {
    title: $("#run-name").value.trim() || undefined,
    name: $("#run-name").value.trim() || undefined,
    goal: $("#run-goal").value.trim(),
    preset: state.depthPreset,
    profile_id: state.profile.id,
    profile_name: state.profile.name,
    proposers,
    refiners,
    aggregator,
    roster: { proposers, refiners, aggregator },
    source_mode: state.sourceMode,
    github_repository: state.sourceMode === "github" ? repo?.fullName : undefined,
    options: {
      model_overrides: selectedModelOverrides(),
      effort_overrides: selectedEffortOverrides(),
    },
  };
  try {
    if (state.pendingFiles.length) {
      button.textContent = "Uploading references…";
      state.uploadedFiles = await uploadFiles(state.pendingFiles, {
        profile_id: state.profile.id,
      });
      payload.upload_ids = state.uploadedFiles.map((item) => item.id);
    }
    button.textContent = state.sourceMode === "github" ? "Preparing GitHub workspace…" : "Dispatching…";
    const job = await createJob(payload);
    state.jobs.unshift(job);
    localStorage.removeItem("moax.runDraft");
    state.pendingFiles = [];
    state.uploadedFiles = [];
    renderAttachments();
    if (payload.upload_ids?.length) {
      trackAttachmentPreparation(job);
    } else {
      showToast("Run queued. The local worker has it.");
      await navigate("run-detail", job.id);
    }
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonLoading(button, false, "Dispatching…", "Start run");
  }
}

function attachmentProgressPercent(update) {
  const files = Math.max(Number(update.file_count) || 1, 1);
  const file = Math.max(Math.min(Number(update.file_index) || 1, files), 1);
  const pages = Number(update.page_count) || 0;
  const page = Math.max(Math.min(Number(update.page_number) || 1, pages || 1), 1);
  const stage = String(update.stage || "");
  const inFile = pages
    ? ((page - 1) + (stage === "complete" ? 1 : stage === "recognizing" ? .72 : .2)) / pages
    : (stage === "complete" ? 1 : .08);
  return Math.max(2, Math.min(100, ((file - 1 + inFile) / files) * 100));
}

function trackAttachmentPreparation(job) {
  const dialog = $("#attachment-progress-dialog");
  const message = $("#attachment-progress-message");
  const pages = $("#attachment-progress-pages");
  const fill = $("#attachment-progress-fill");
  const track = $(".attachment-progress-track");
  let handedOff = false;
  let stop = () => {};
  const show = (update = {}) => {
    const stage = String(update.stage || "queued");
    const name = update.file_name || "reference files";
    const page = Number(update.page_number) || 0;
    const total = Number(update.page_count) || 0;
    if (total) {
      const action = {
        extracting: "Checking for text",
        rendering: "Rendering for OCR",
        recognizing: "Reading with OCR",
        complete: "Prepared",
      }[stage] || "Preparing";
      message.textContent = `${action}: ${name}`;
      pages.textContent = `Page ${page} of ${total}`;
    } else if (stage === "complete") {
      message.textContent = `Prepared ${name}`;
      pages.textContent = `File ${update.file_index || 1} of ${update.file_count || 1}`;
    } else if (stage === "queued") {
      message.textContent = "Waiting for the local worker to begin reference preparation…";
      pages.textContent = `${update.file_count || "Your"} reference file${Number(update.file_count) === 1 ? "" : "s"} queued`;
    } else {
      message.textContent = `Preparing ${name}`;
      pages.textContent = `File ${update.file_index || 1} of ${update.file_count || 1}`;
    }
    const percent = attachmentProgressPercent(update);
    fill.style.width = `${percent}%`;
    track.setAttribute("aria-valuenow", String(Math.round(percent)));
  };
  const handOff = async () => {
    if (handedOff) return;
    handedOff = true;
    stop();
    if (dialog.open) dialog.close();
    showToast("References are ready. The ensemble is starting.");
    await navigate("run-detail", job.id);
  };
  stop = subscribeToJob(job.id, {
    onEvent: (update) => {
      if (update.type === "attachment-progress") show(update);
      if (update.type === "attachment") handOff();
      if (update.type === "worker-error") {
        message.textContent = update.message || "Reference preparation failed.";
        pages.textContent = "Open the run details to review the error.";
      }
    },
    onState: (update) => {
      if (update.phase === "attachments") show(update);
      if (["failed", "cancelled"].includes(update.status)) handOff();
    },
  });
  show({ file_count: job.config?.upload_ids?.length || 1, stage: "queued" });
  dialog.showModal();
}

function phaseIndex(phase) {
  const value = String(phase || "").toLowerCase();
  if (value.includes("layer3") || value.includes("aggregate") || value.includes("decide")) return 3;
  if (value.includes("layer2") || value.includes("refin")) return 2;
  if (value.includes("layer1") || value.includes("propos")) return 1;
  return 0;
}

function renderRunDetail(job = state.detailJob) {
  if (!job) return;
  state.detailJob = { ...(state.detailJob || {}), ...job };
  job = state.detailJob;
  $("#run-detail-kicker").textContent = `RUN ${job.id.slice(0, 12).toUpperCase()}`;
  $("#run-detail-title").textContent = job.title;
  $("#run-detail-meta").textContent = `${jobContext(job)} · Started ${formatTime(job.startedAt || job.createdAt)} · ${jobRosterText(job)}`;
  const current = phaseIndex(job.phase);
  const done = job.status === "completed" ? 4 : current;
  const runArt = $("#run-detail-art");
  const visual = stateVisual(job);
  runArt.src = `/static/images/state-${visual.key}.webp`;
  $("#run-detail-art-caption").textContent = visual.label;
  renderResultShortcuts(job);
  const phases = [
    ["Layer 0", "Scout"],
    ["Layer 1", "Propose"],
    ["Layer 2", "Refine"],
    ["Layer 3", "Decide"],
  ];
  $("#phase-rail").innerHTML = phases.map(([label, name], index) => `
    <div class="phase-node ${index < done ? "is-done" : index === current && ["running", "queued"].includes(job.status) ? "is-current" : ""}">
      <strong>${label}</strong><small>${name}</small>
    </div>
  `).join("");
  $("#phase-rail").setAttribute("aria-busy", "false");
  const actions = $("#run-actions");
  if (["running", "queued", "cancelling"].includes(job.status)) {
    actions.innerHTML = `<button class="danger-button" type="button" data-cancel-run="${escapeHtml(job.id)}">Cancel run</button>`;
  } else if (job.status === "failed") {
    actions.innerHTML = `<button class="secondary-button" type="button" data-redispatch-run="${escapeHtml(job.id)}">Redispatch failures</button>`;
  } else {
    actions.innerHTML = `<button class="secondary-button" type="button" data-route-button="new">New run</button>`;
  }
  renderAgents(job);
  renderEvents();
  $("#agent-grid").setAttribute("aria-busy", "false");
  $("#event-feed").setAttribute("aria-busy", "false");
  renderResult(job);
}

function normalizeAgent(raw, index) {
  return {
    id: raw.id || raw.name || `agent-${index}`,
    name: raw.display_name || raw.name || raw.provider || `Agent ${index + 1}`,
    model: raw.model || raw.model_id || raw.provider || "Configured model",
    role: raw.role || raw.layer || "agent",
    status: String(raw.status || raw.state || "queued").toLowerCase(),
    summary: raw.summary || raw.message || raw.error || "",
    startedAt: raw.started_at || raw.startedAt,
    finishedAt: raw.finished_at || raw.finishedAt,
  };
}

function fallbackAgent(job, routeId, role, status) {
  const route = modelsForRole(role).find((option) => option.id === routeId);
  const runOptions = job.options || job.config?.options || {};
  const modelOverrides = runOptions.model_overrides || job.model_overrides || {};
  const effortOverrides = runOptions.effort_overrides || job.effort_overrides || {};
  const model = modelOverrides[routeId] || route?.model || routeId;
  const effort = effortOverrides[routeId] || route?.effort;
  return {
    id: routeId,
    name: `${route?.name || titleCase(routeId)} · ${role}`,
    model: `${model}${effort && effort !== "default" ? ` · ${titleCase(effort)} effort` : ""}`,
    role,
    status,
  };
}

function agentStatusFromEvents(routeId, logRole, fallback) {
  const needle = `${String(routeId).toLowerCase()} ${String(logRole).toLowerCase()}`;
  for (let index = state.events.length - 1; index >= 0; index -= 1) {
    const message = String(state.events[index]?.message || "").toLowerCase();
    const marker = message.indexOf(needle);
    if (marker < 0) continue;
    const outcome = message.slice(marker + needle.length);
    if (/:\s*ok\b/.test(outcome)) return "completed";
    if (/:\s*(?:fail|error)\b/.test(outcome)) return "failed";
  }
  return fallback;
}

function agentsFromJob(job) {
  if (Array.isArray(job.agents) && job.agents.length) return job.agents.map(normalizeAgent);
  const roster = job.roster || {};
  const runFinished = ["completed", "imported"].includes(job.status);
  const currentPhase = phaseIndex(job.phase);
  return [
    ...(roster.proposers || []).map((routeId) => fallbackAgent(
      job, routeId, "proposer", agentStatusFromEvents(
        routeId, "proposer", runFinished || currentPhase > 1 ? "completed" : job.status,
      ),
    )),
    ...(roster.refiners || []).map((routeId) => fallbackAgent(
      job, routeId, "refiner", agentStatusFromEvents(
        routeId, "refiner-broadcast", runFinished || currentPhase > 2 ? "completed" : currentPhase === 2 ? job.status : "queued",
      ),
    )),
    ...(roster.aggregator ? [fallbackAgent(
      job, roster.aggregator, "aggregator", agentStatusFromEvents(
        roster.aggregator, "aggregator", runFinished ? "completed" : currentPhase === 3 ? job.status : "queued",
      ),
    )] : []),
  ].map(normalizeAgent);
}

function agentVisual(agent) {
  const signature = `${agent.id} ${agent.name} ${agent.model}`.toLowerCase();
  if (signature.includes("agy") || signature.includes("gemini")) {
    return { key: "agy", label: "Antigravity", accent: "violet" };
  }
  if (signature.includes("claude") || signature.includes("sonnet") || signature.includes("opus")) {
    return { key: "claude", label: "Claude", accent: "amber" };
  }
  if (signature.includes("codex") || signature.includes("gpt-5")) {
    return { key: "codex", label: "Codex", accent: "teal" };
  }
  const accent = signature.includes("deepseek") ? "violet"
    : signature.includes("qwen") ? "gold"
    : signature.includes("glm") ? "green"
    : "green";
  return { key: "opencode", label: "OpenCode", accent };
}

function agentStateCopy(agent) {
  if (agent.summary) return agent.summary;
  if (agent.status === "running") return "Actively reading the task, checking evidence, and building its response.";
  if (agent.status === "queued") return "Ready and waiting for the preceding layer to finish.";
  if (agent.status === "completed") return "Response captured. This lane is standing by while the ensemble continues.";
  if (agent.status === "failed") return "This lane did not return a usable response. Open the trace for the recorded cause.";
  if (agent.status === "blocked") return "This lane did not run because an earlier stage ended the workflow.";
  if (agent.status === "cancelled") return "This lane stopped when the run was cancelled.";
  return "Waiting for the worker to report this lane’s state.";
}

function agentArtwork(visual, status) {
  const still = `/static/images/pixel-${visual.key}.webp`;
  if (status === "running") {
    return { still, src: `/static/images/pixel-${visual.key}-work-animated.webp` };
  }
  if (status === "completed") {
    return { still, src: `/static/images/pixel-${visual.key}-victory-animated.webp` };
  }
  return { still, src: still };
}

function renderAgents(job) {
  const agents = agentsFromJob(job);
  $("#agent-grid").innerHTML = agents.length ? agents.map((agent) => {
    const visual = agentVisual(agent);
    const status = jobStatus(agent);
    const artwork = agentArtwork(visual, status);
    return `
    <article class="agent-card agent-card-${escapeHtml(status)}">
      <div class="agent-card-layout">
        <div class="agent-card-copy">
          <div class="agent-card-head">
            <div><p class="eyebrow">${escapeHtml(String(agent.role).toUpperCase())}</p><h3>${escapeHtml(agent.name)}</h3><p class="agent-model">${escapeHtml(agent.model)}</p></div>
          </div>
          <p class="agent-summary">${escapeHtml(agentStateCopy(agent))}</p>
          <div class="agent-timing"><span>${escapeHtml(formatTime(agent.startedAt, false))}</span><span>${escapeHtml(formatDuration(agent.startedAt, agent.finishedAt || Date.now()))}</span></div>
        </div>
        <figure class="agent-pixel-stage is-${escapeHtml(status)} accent-${escapeHtml(visual.accent)}" aria-label="${escapeHtml(`${visual.label} character: ${titleCase(agent.status)}`)}">
          <picture>
            <source media="(prefers-reduced-motion: reduce)" srcset="${escapeHtml(artwork.still)}">
            <img src="${escapeHtml(artwork.src)}" alt="" width="320" height="320">
          </picture>
          <figcaption>
            <span class="status-tag ${escapeHtml(status)}">${escapeHtml(titleCase(agent.status))}</span>
            <small>${escapeHtml(visual.label)}</small>
          </figcaption>
        </figure>
      </div>
    </article>
  `;
  }).join("") : `<div class="empty-state"><span class="empty-number">—</span><div><h3>Roster pending</h3><p>Agent lanes appear when the worker resolves this run.</p></div></div>`;
}

function unwrapTraceEvent(source) {
  let event = source && typeof source === "object" ? { ...source } : { message: String(source || "") };
  const message = String(event.message || "").trim();
  if (message.startsWith("{") && message.endsWith("}")) {
    try {
      const nested = JSON.parse(message);
      if (nested && typeof nested === "object" && (nested.seq || nested.kind || nested.type)) {
        event = { ...(nested.data || {}), ...nested, type: nested.type || nested.kind || event.type };
      }
    } catch {
      // Preserve the original worker message as technical detail.
    }
  }
  return event;
}

function traceAgentFromMessage(message) {
  const match = String(message).match(/(?:ERROR]\s+|\]\s{2,})([a-z0-9-]+)(?:\s+proposer|\s+refiner|:)/i);
  return match ? match[1] : "";
}

function tracePresentation(source) {
  const event = unwrapTraceEvent(source);
  const type = String(event.type || event.kind || event.event || "update").toLowerCase();
  const message = String(event.summary || event.message || event.detail || event.status_message || "").trim();
  const phase = String(event.phase || event.layer || "").toLowerCase();
  const lower = message.toLowerCase();
  const raw = message || JSON.stringify(event.data || {});
  const base = {
    id: event.seq || event.id || `${type}:${event.created_at || event.timestamp || ""}:${message}`,
    createdAt: event.created_at || event.timestamp || "",
    raw,
    type,
    tone: "neutral",
    keep: !["heartbeat", "stdout", "stderr"].includes(type),
  };

  if (type === "log") {
    base.keep = /error|fatal|fail|spawning|done phase|report|final-plan|workspace immutability/i.test(message);
    if (/workspace immutability violation/i.test(message)) {
      const agent = traceAgentFromMessage(message);
      return {
        ...base,
        id: `workspace-safety:${agent || "lane"}`,
        title: agent ? `${titleCase(agent)} hit the workspace safety check` : "Workspace safety check stopped a lane",
        message: "A local tool or hook changed files while the models were running. MoA-X rejected the affected output to protect the repository.",
        tone: "error",
        keep: true,
      };
    }
    if (/existing layer1 has no successful proposers/i.test(message)) {
      return {
        ...base,
        id: "layer1-no-safe-output",
        title: "Review could not start",
        message: "Every proposal was rejected by the workspace safety check, so there was nothing safe to send to the reviewers.",
        tone: "error",
        keep: true,
      };
    }
    if (/layer 1: spawning/i.test(message)) {
      const count = (message.match(/'/g) || []).length / 2;
      return {
        ...base,
        title: "Proposal lanes started",
        message: `${count || "The configured"} independent models began working in parallel.`,
        tone: "active",
        keep: true,
      };
    }
    if (/layer1 done phase/i.test(message)) {
      const duration = message.match(/in\s+([0-9.]+s)/i)?.[1];
      return {
        ...base,
        id: "layer1-finished",
        title: "Proposal processes finished",
        message: `All proposal processes returned${duration ? ` after ${duration}` : ""}. Their outputs then went through safety validation.`,
        tone: "complete",
        keep: true,
      };
    }
    if (/fatal|error|fail/i.test(message)) {
      return { ...base, title: "The worker reported a problem", message: "The run needs attention. Open the technical detail below for the recorded cause.", tone: "error", keep: true };
    }
    return { ...base, title: "Worker update", message: message.replace(/^\[orchestrator]\s*/i, ""), keep: base.keep };
  }

  if (type.includes("phase")) {
    const starting = /^starting/i.test(message);
    const failed = /fail/i.test(message) || Number(event.exit_code) > 0;
    if (phase === "layer1") {
      return {
        ...base,
        id: starting ? "layer1-started" : "layer1-finished",
        title: starting ? "Proposal stage started" : "Proposal processes finished",
        message: starting
          ? "Independent models are reading the same task and preparing recommendations."
          : "The proposal processes exited. MoA-X is validating their outputs before review.",
        tone: failed ? "error" : starting ? "active" : "complete",
      };
    }
    if (phase === "layer2") {
      return {
        ...base,
        title: failed ? "Review stage was blocked" : starting ? "Review stage started" : "Review stage finished",
        message: failed
          ? "The reviewers did not run because no proposal passed the preceding safety validation."
          : starting ? "Reviewers are cross-checking the retained proposals and evidence." : "Cross-review is complete.",
        tone: failed ? "error" : starting ? "active" : "complete",
      };
    }
    if (phase === "layer3") {
      return {
        ...base,
        title: failed ? "Final decision failed" : starting ? "Final decision started" : "Final decision complete",
        message: failed ? "The final synthesis did not complete." : starting ? "The aggregator is combining the reviewed recommendations." : "The final recommendation is ready.",
        tone: failed ? "error" : starting ? "active" : "complete",
      };
    }
  }

  if (type.includes("artifact")) return { ...base, title: "New result available", message: event.path ? `Saved ${event.path}.` : "The worker saved a run artifact.", tone: "complete" };
  if (type.includes("warning")) return { ...base, title: "Attention needed", message: message || "The worker reported a warning.", tone: "warning" };
  if (type.includes("error") || type.includes("fail") || /failed with exit code/i.test(lower)) {
    return { ...base, title: "Run stopped", message: message || "The worker reported a failure.", tone: "error" };
  }
  if (type.includes("complete")) return { ...base, title: "Run complete", message: message || "The final recommendation and report are ready.", tone: "complete" };
  if (type === "job") {
    return {
      ...base,
      title: /started/i.test(message) ? "Run started" : /queued/i.test(message) ? "Run queued" : "Run status changed",
      message: /started/i.test(message) ? "The worker accepted the run and began orchestration." : message || "The worker updated the run.",
      tone: /started/i.test(message) ? "active" : "neutral",
    };
  }
  return { ...base, title: titleCase(type), message: message || "The worker reported a state change." };
}

function renderEvents() {
  const feed = $("#event-feed");
  const seen = new Set();
  const events = state.events
    .map(tracePresentation)
    .filter((event) => {
      if (!event.keep || seen.has(event.id)) return false;
      seen.add(event.id);
      return !state.profile.compactEvents || event.type !== "log" || ["error", "active", "complete"].includes(event.tone);
    })
    .slice(-100)
    .reverse();
  feed.innerHTML = events.length ? events.map((event) => `
    <article class="event-item is-${escapeHtml(event.tone)}">
      <time datetime="${escapeHtml(event.createdAt)}">${escapeHtml(formatTime(event.createdAt || Date.now(), false))}</time>
      <div class="event-copy">
        <strong>${escapeHtml(event.title)}</strong>
        <p>${escapeHtml(event.message)}</p>
        ${event.raw && event.raw !== event.message ? `
          <details class="event-technical">
            <summary>Technical detail</summary>
            <pre>${escapeHtml(event.raw)}</pre>
          </details>` : ""}
      </div>
    </article>
  `).join("") : `<div class="event-empty">Waiting for the first meaningful worker update. Connection heartbeats and repetitive setup logs stay hidden.</div>`;
}

function artifactEntries(artifacts) {
  const entries = Array.isArray(artifacts)
    ? artifacts.map((item) => typeof item === "string" ? [item.split("/").pop(), item] : [item.name || item.label, item.url || item.path])
    : Object.entries(artifacts || {}).map(([name, value]) => [name, typeof value === "string" ? value : value?.url || value?.path]);
  const priority = { report: 0, final_plan: 1, synthesis: 2, manifest: 3 };
  return entries.sort(([left], [right]) => (priority[left] ?? 20) - (priority[right] ?? 20));
}

function artifactLabel(name, prominent = false) {
  const labels = {
    report: prominent ? "View final report" : "Report",
    final_plan: prominent ? "Read final plan" : "Final plan",
    synthesis: prominent ? "Read synthesis notes" : "Synthesis notes",
    manifest: prominent ? "View run data" : "Run data",
  };
  return labels[name] || titleCase(name);
}

function artifactLinks(artifacts, prominent = false) {
  return artifacts.map(([name, url], index) => `
    <a class="${index === 0 && prominent ? "is-primary" : ""}" href="${escapeHtml(safeUrl(url))}" target="_blank" rel="noopener">
      ${escapeHtml(artifactLabel(name, prominent))}
    </a>
  `).join("");
}

function renderResultShortcuts(job) {
  const shortcuts = $("#run-result-shortcuts");
  const artifacts = artifactEntries(job.artifacts).filter(([, url]) => url);
  if (!artifacts.length) {
    shortcuts.hidden = true;
    shortcuts.innerHTML = "";
    return;
  }
  shortcuts.hidden = false;
  shortcuts.innerHTML = `
    <span>Final results</span>
    <div class="run-result-links">
      ${job.status === "completed" && job.artifacts?.report
        ? `<button class="report-share-button" type="button" data-share-report aria-label="Create a shareable link for the final report"><span aria-hidden="true">↗</span> Share final report</button>`
        : ""}
      ${artifactLinks(artifacts, true)}
    </div>
  `;
  shortcuts.querySelector("[data-share-report]")?.addEventListener("click", () => openReportShare(job));
}

async function openReportShare(job) {
  const dialog = $("#report-share-dialog");
  const field = $("#report-share-url");
  const status = $("#report-share-status");
  const revoke = $("#report-share-revoke");
  try {
    status.textContent = "Creating a new revocable report link…";
    revoke.hidden = true;
    if (!dialog.open) dialog.showModal();
    const shared = await createReportShare(job.id);
    field.value = new URL(shared.url, window.location.origin).href;
    status.textContent = "Anyone with this link can view this report. Creating another link or revoking it disables this one.";
    revoke.hidden = false;
    try {
      await navigator.clipboard.writeText(field.value);
      status.textContent = "Link copied. Anyone with it can view this report; revoke it here at any time.";
    } catch {
      field.select();
    }
  } catch (error) {
    status.textContent = error.message;
    showToast(error.message, "error");
  }
}

async function copyReportShare() {
  const field = $("#report-share-url");
  try {
    await navigator.clipboard.writeText(field.value);
    $("#report-share-status").textContent = "Link copied.";
  } catch {
    field.select();
  }
}

async function revokeCurrentReportShare() {
  const jobId = state.detailJob?.id;
  if (!jobId) return;
  try {
    await revokeReportShare(jobId);
    $("#report-share-url").value = "";
    $("#report-share-status").textContent = "The report link was revoked.";
    $("#report-share-revoke").hidden = true;
  } catch (error) {
    showToast(error.message, "error");
  }
}

function renderResult(job) {
  const panel = $("#result-panel");
  if (!["completed", "failed", "cancelled"].includes(job.status)) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const artifacts = artifactEntries(job.artifacts).filter(([, url]) => url);
  panel.innerHTML = `
    <p class="eyebrow">${job.status === "completed" ? "FINAL OUTPUT" : "RUN OUTCOME"}</p>
    <h2>${job.status === "completed" ? "The plan is ready" : titleCase(job.status)}</h2>
    <p>${escapeHtml(job.summary || (job.status === "completed"
      ? "The ensemble finished and preserved the decision trail."
      : "Review the agent lanes and event trace for details."))}</p>
    ${artifacts.length ? `<div class="result-links">${artifactLinks(artifacts)}</div>` : ""}
  `;
}

async function loadRunDetail(id) {
  $("#run-detail-title").textContent = "Loading run…";
  $("#run-detail-meta").textContent = "Retrieving the saved run and its latest worker state.";
  $("#phase-rail").setAttribute("aria-busy", "true");
  $("#phase-rail").innerHTML = `<div class="loading-bar" role="status"><span></span><b>Loading run state…</b></div>`;
  $("#agent-grid").setAttribute("aria-busy", "true");
  $("#agent-grid").innerHTML = `<div class="loading-stage compact" role="status"><span class="run-spinner" aria-hidden="true"></span><div><h3>Loading agent lanes</h3><p>Resolving the saved roster…</p></div></div>`;
  $("#event-feed").setAttribute("aria-busy", "true");
  $("#event-feed").innerHTML = `<div class="loading-stage compact" role="status"><span class="run-spinner" aria-hidden="true"></span><div><h3>Connecting to live trace</h3><p>Waiting for the latest worker event…</p></div></div>`;
  state.events = [];
  try {
    const job = await getJob(id);
    renderRunDetail(job);
    const live = $("#live-label");
    state.eventStop = subscribeToJob(id, {
      onEvent: (event) => {
        if (event.type !== "heartbeat") state.events.push(event);
        const jobUpdate = event.type !== "log"
          && (event.job || event.status || event.progress !== undefined || event.phase);
        if (jobUpdate) {
          const update = event.job || event;
          renderRunDetail({ ...state.detailJob, ...update });
        } else {
          renderEvents();
          if (event.type === "log" && /:\s*(?:OK|FAIL|ERROR)\b/i.test(String(event.message || ""))) {
            renderAgents(state.detailJob);
          }
        }
        const now = Date.now();
        if (now - state.detailRefreshAt > 1800) {
          state.detailRefreshAt = now;
          getJob(id).then(renderRunDetail).catch(() => {});
        }
      },
      onState: (update) => {
        if (update.connection) {
          live.textContent = update.connection === "live"
            ? "Live"
            : update.connection === "settled"
              ? (state.detailJob?.status === "completed" ? "Complete" : "Closed")
              : "Polling";
          live.classList.toggle("is-live", update.connection === "live");
        } else if (update.id) renderRunDetail(update);
      },
      onError: () => {
        live.textContent = "Reconnecting";
        live.classList.remove("is-live");
      },
    });
  } catch (error) {
    $("#run-detail-title").textContent = "Run unavailable";
    $("#run-detail-meta").textContent = error.message;
    ["#phase-rail", "#agent-grid", "#event-feed"].forEach((selector) => {
      $(selector).setAttribute("aria-busy", "false");
    });
    $("#phase-rail").innerHTML = "";
    $("#agent-grid").innerHTML = `<div class="empty-state"><span class="empty-number">!</span><div><h3>Agent lanes unavailable</h3><p>${escapeHtml(error.message)}</p></div></div>`;
    $("#event-feed").innerHTML = `<div class="empty-state"><span class="empty-number">!</span><div><h3>Trace unavailable</h3><p>Return to the archive and try opening this run again.</p></div></div>`;
    showToast(error.message, "error");
  }
}

async function refreshData() {
  const settle = async (key, request, apply, render) => {
    try {
      apply(await request);
      return true;
    } finally {
      state.loading[key] = false;
      render();
    }
  };
  const results = await Promise.allSettled([
    settle("providers", getProviders(), (providers) => {
      state.providers = providers.filter((provider) => provider.id !== "gemini");
    }, () => {
      renderMachineHealth();
      renderHealthRibbon();
      renderProviders();
      renderRoster();
    }),
    settle("models", getModels(), (models) => { state.models = models; }, renderRoster),
    settle("jobs", getJobs({ limit: 100 }), (jobs) => { state.jobs = jobs; }, () => {
      renderActiveJob();
      renderRecentRuns();
      renderArchive();
    }),
    settle("workspaces", getWorkspaces(), (workspaces) => { state.workspaces = workspaces; }, () => {}),
    settle("githubRepos", getGithubRepos(), (repos) => { state.githubRepos = repos; }, renderGithubRepos),
  ]);
  const failures = results.filter((result) => result.status === "rejected");
  if (failures.length && failures.length < results.length) {
    showToast("Some local data is still loading. Available controls remain active.");
  } else if (failures.length === results.length) {
    showToast("The control plane is not responding yet.", "error");
  }
}

function bindEvents() {
  document.addEventListener("click", async (event) => {
    const infoButton = event.target.closest(".info-button");
    if (infoButton) {
      toggleInfoPopover(infoButton);
      return;
    }
    if (!event.target.closest("#info-popover")) closeInfoPopover();
    const unavailableGroup = event.target.closest(".model-provider-group[data-unavailable] > .model-provider-head");
    if (unavailableGroup) {
      event.preventDefault();
      return;
    }

    const removeFile = event.target.closest("[data-remove-file]");
    if (removeFile) {
      state.pendingFiles.splice(Number(removeFile.dataset.removeFile), 1);
      renderAttachments();
      return;
    }
    const routeLink = event.target.closest("[data-route]");
    if (routeLink) {
      event.preventDefault();
      await navigate(routeLink.dataset.route);
      return;
    }
    const routeButton = event.target.closest("[data-route-button]");
    if (routeButton) {
      await navigate(routeButton.dataset.routeButton);
      return;
    }
    const runLink = event.target.closest("[data-open-run]");
    if (runLink) {
      event.preventDefault();
      await navigate("run-detail", runLink.dataset.openRun);
      return;
    }
    const probeButton = event.target.closest("[data-probe-provider]");
    if (probeButton) {
      setButtonLoading(probeButton, true, "Checking…", "Check again");
      try {
        const provider = await probeProvider(probeButton.dataset.probeProvider);
        const index = state.providers.findIndex((item) => item.id === provider.id);
        if (index >= 0) state.providers[index] = provider; else state.providers.push(provider);
        renderProviders();
        renderHealthRibbon();
        renderMachineHealth();
        renderRoster();
        showToast(`${provider.name} check finished.`);
      } catch (error) {
        showToast(error.message, "error");
        setButtonLoading(probeButton, false, "Checking…", "Check again");
      }
      return;
    }
    const cancelButton = event.target.closest("[data-cancel-run]");
    if (cancelButton) {
      if (!window.confirm("Cancel this run and stop its active child processes?")) return;
      setButtonLoading(cancelButton, true, "Cancelling…", "Cancel run");
      try {
        await cancelJob(cancelButton.dataset.cancelRun);
        showToast("Cancellation requested.");
      } catch (error) {
        showToast(error.message, "error");
        setButtonLoading(cancelButton, false, "Cancelling…", "Cancel run");
      }
      return;
    }
    const retryButton = event.target.closest("[data-redispatch-run]");
    if (retryButton) {
      setButtonLoading(retryButton, true, "Queuing…", "Redispatch failures");
      try {
        const recovery = state.detailJob?.recovery;
        const recentPhase = [...state.events].reverse().find((item) => ["layer1", "layer2"].includes(item.phase))?.phase;
        const phase = recovery?.phase || recentPhase || "layer1";
        const roster = state.detailJob?.roster || {};
        const suggested = recovery?.agents?.length
          ? recovery.agents
          : phase === "layer2" ? roster.refiners || [] : roster.proposers || [];
        const entered = window.prompt(`Agents to redispatch in ${phase} (comma-separated)`, suggested.join(", "));
        if (entered === null) return;
        const agents = entered.split(",").map((item) => item.trim()).filter(Boolean);
        if (!agents.length) throw new Error("Choose at least one agent to redispatch.");
        const next = await redispatchJob(retryButton.dataset.redispatchRun, { phase, agents });
        state.jobs.unshift(next);
        showToast("Failed lanes queued for redispatch.");
        await navigate("run-detail", next.id);
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        setButtonLoading(retryButton, false, "Queuing…", "Redispatch failures");
      }
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeInfoPopover();
  });
  window.addEventListener("resize", closeInfoPopover);

  $("#menu-button").addEventListener("click", () => {
    const open = $("#sidebar").classList.toggle("is-open");
    $("#menu-button").setAttribute("aria-expanded", String(open));
  });
  $("#launch-form").addEventListener("submit", launchRun);
  $$(".source-tab").forEach((button) => button.addEventListener("click", () => {
    setSourceMode(button.dataset.sourceMode);
  }));
  $("#github-repo").addEventListener("change", updateGithubMeta);
  $("#run-files").addEventListener("change", (event) => {
    addFiles(event.target.files);
    event.target.value = "";
  });
  const dropZone = $("#drop-zone");
  ["dragenter", "dragover"].forEach((name) => dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.add("is-dragging");
  }));
  ["dragleave", "drop"].forEach((name) => dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.remove("is-dragging");
  }));
  dropZone.addEventListener("drop", (event) => addFiles(event.dataTransfer.files));
  dropZone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      $("#run-files").click();
    }
  });
  $$("#launch-form [data-next-step]").forEach((button) => button.addEventListener("click", () => {
    if (validateStep(state.step)) setStep(state.step + 1);
  }));
  $$("#launch-form [data-prev-step]").forEach((button) => button.addEventListener("click", () => setStep(state.step - 1)));
  $$(".step-item").forEach((button) => button.addEventListener("click", () => {
    const target = Number(button.dataset.stepTarget);
    if (target < state.step || Array.from({ length: target - 1 }, (_, index) => index + 1).every(validateStep)) setStep(target);
  }));
  $$("[data-roster-step]").forEach((button) => button.addEventListener("click", () => {
    const target = Number(button.dataset.rosterStep);
    if (target < state.step || Array.from({ length: target - 1 }, (_, index) => index + 1).every(validateStep)) setStep(target);
  }));
  $$("[data-optimized-loadout]").forEach((button) => button.addEventListener("click", () => {
    applyOptimizedRole(button.dataset.optimizedLoadout);
    if (validateStep(state.step)) setStep(state.step + 1);
  }));
  $("#depth-range").addEventListener("input", (event) => {
    applyDepthPreset(DEPTH_KEYS[Number(event.target.value)] || "balanced", false);
  });
  $("#depth-range").addEventListener("change", (event) => {
    applyDepthPreset(DEPTH_KEYS[Number(event.target.value)] || "balanced");
  });
  $("#launch-form").addEventListener("change", (event) => {
    if (event.target.matches('input[name="proposer"], input[name="refiner"], input[name="aggregator"]')) {
      if (event.target.checked) {
        $$(`input[name="${event.target.name}"]`).forEach((input) => {
          if (input !== event.target && input.value === event.target.value) input.checked = false;
        });
      }
      updateRosterChecks();
      syncEffortControls();
    }
  });
  $("#launch-form").addEventListener("input", (event) => {
    if (event.target.matches("[data-effort-range]")) {
      updateEffortSlider(event.target);
      updateRosterChecks();
    }
  });
  $("#run-goal").addEventListener("input", () => {
    $("#goal-count").textContent = $("#run-goal").value.length;
    localStorage.setItem("moax.runDraft", $("#run-goal").value);
  });
  $("#prompt-coach-button").addEventListener("click", openPromptCoach);
  $("#prompt-coach-close").addEventListener("click", () => $("#prompt-coach-dialog").close());
  $("#report-share-copy").addEventListener("click", copyReportShare);
  $("#report-share-revoke").addEventListener("click", revokeCurrentReportShare);
  $("#prompt-undo-button").addEventListener("click", () => {
    if (!state.promptCoach.undo) return;
    $("#run-goal").value = state.promptCoach.undo;
    $("#goal-count").textContent = $("#run-goal").value.length;
    localStorage.setItem("moax.runDraft", $("#run-goal").value);
    state.promptCoach.undo = "";
    $("#prompt-undo-button").hidden = true;
    showToast("Prompt change undone.");
  });
  $("#run-search").addEventListener("input", renderArchive);
  $("#run-status-filter").addEventListener("change", renderArchive);
  $("#import-history-button").addEventListener("click", async () => {
    const button = $("#import-history-button");
    const workspace = state.workspaces[0]?.path;
    if (!workspace) return showToast("No local workspace is available to scan.", "error");
    setButtonLoading(button, true, "Scanning .moa…", "Import .moa history");
    try {
      const result = await importHistory(workspace);
      state.jobs = await getJobs({ limit: 100 });
      renderArchive();
      renderRecentRuns();
      showToast(`Imported ${result.count || 0} historical run${result.count === 1 ? "" : "s"}.`);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setButtonLoading(button, false, "Scanning .moa…", "Import .moa history");
    }
  });
  $("#probe-all-button").addEventListener("click", async () => {
    const button = $("#probe-all-button");
    setButtonLoading(button, true, "Checking machine…", "Recheck all");
    try {
      state.providers = (await probeAllProviders()).filter((provider) => provider.id !== "gemini");
      renderProviders();
      renderHealthRibbon();
      renderMachineHealth();
      renderRoster();
      showToast("All provider checks finished.");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setButtonLoading(button, false, "Checking machine…", "Recheck all");
    }
  });
  $$("[data-open-panel='settings']").forEach((button) => button.addEventListener("click", () => {
    $("#profile-name").value = state.profile.name;
    $("#compact-events").checked = state.profile.compactEvents;
    $("#settings-dialog").showModal();
  }));
  $("#save-settings").addEventListener("click", async () => {
    state.profile.name = $("#profile-name").value.trim() || "Local operator";
    state.profile.compactEvents = $("#compact-events").checked;
    localStorage.setItem("moax.profile", JSON.stringify(state.profile));
    let persisted = true;
    try {
      await saveProfile(state.profile);
    } catch {
      persisted = false;
    }
    updateProfileChrome();
    renderEvents();
    $("#settings-dialog").close();
    showToast(
      persisted ? "Local preferences saved." : "Saved in this browser, but the server profile could not be updated.",
      persisted ? "info" : "error",
    );
  });
  window.addEventListener("popstate", async () => {
    const current = routeFromPath();
    await navigate(current.route, current.id, false);
  });
  window.setInterval(() => {
    $$("[data-elapsed]").forEach((node) => { node.textContent = formatDuration(node.dataset.elapsed); });
  }, 1000);
}

async function init() {
  installBrandAssets();
  hydrateOptionalAssets();
  bindEvents();
  setSourceMode(state.sourceMode);
  updateProfileChrome();
  $("#run-goal").value = localStorage.getItem("moax.runDraft") || "";
  $("#goal-count").textContent = $("#run-goal").value.length;
  try {
    await establishProfileSession();
  } catch (error) {
    showToast(
      `Private profile session could not be established: ${error.message}`,
      "error",
    );
  }
  const current = routeFromPath();
  await navigate(current.route, current.id, false);
  await refreshData();
  window.setInterval(async () => {
    if (document.hidden || state.route === "run-detail") return;
    try {
      state.jobs = await getJobs({ limit: 100 });
      renderActiveJob();
      renderRecentRuns();
      if (state.route === "runs") renderArchive();
    } catch { /* Preserve the last good local snapshot. */ }
  }, 8000);
}

init();
