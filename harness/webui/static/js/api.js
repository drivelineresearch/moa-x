const JSON_HEADERS = { "Content-Type": "application/json", "Accept": "application/json" };

function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta?.content) return meta.content;
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

async function request(path, options = {}) {
  const headers = { ...JSON_HEADERS, ...(options.headers || {}) };
  const token = csrfToken();
  if (token && !["GET", "HEAD"].includes(options.method || "GET")) {
    headers["X-CSRF-Token"] = token;
  }
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const message = payload?.error || payload?.message || payload || `Request failed (${response.status})`;
    const error = new Error(String(message));
    error.status = response.status;
    throw error;
  }
  return payload;
}

function arrayFrom(payload, key) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.[key])) return payload[key];
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

export function normalizeProvider(raw = {}) {
  const id = raw.id || raw.key || raw.name || raw.harness || "unknown";
  const installed = raw.installed ?? raw.cli_installed ?? raw.available ?? false;
  const authenticated = raw.authenticated ?? raw.auth_ok ?? raw.ready ?? false;
  const status = raw.status || (installed && authenticated ? "ready" : installed ? "needs_auth" : "missing");
  const models = Array.isArray(raw.models)
    ? raw.models.map((model) => typeof model === "string" ? { id: model, name: model } : model)
    : raw.model ? [{ id: raw.model, name: raw.model }] : [];
  return {
    ...raw,
    id: String(id),
    name: raw.display_name || raw.label || raw.name || String(id),
    installed: Boolean(installed),
    authenticated: Boolean(authenticated),
    available: raw.available ?? (Boolean(installed) && Boolean(authenticated)),
    status: String(status).toLowerCase(),
    detail: raw.detail || raw.message || raw.reason || "",
    version: raw.version || raw.cli_version || "—",
    authMode: raw.auth_mode || raw.auth || "Local CLI account",
    lab: raw.lab || raw.vendor || raw.id || "unknown",
    harness: raw.harness || raw.cli || raw.id || "unknown",
    models,
    roles: raw.roles || raw.supported_roles || ["proposer", "refiner", "aggregator"],
  };
}

export function normalizeJob(raw = {}) {
  const id = raw.id || raw.job_id || raw.session_id || "";
  const progress = Number(raw.progress ?? raw.progress_percent ?? raw.completion ?? 0);
  const sourceStatus = String(raw.status || raw.state || "queued").toLowerCase();
  const status = sourceStatus === "succeeded" || sourceStatus === "imported"
    ? "completed"
    : sourceStatus;
  const config = raw.roster || raw.config || {};
  return {
    ...raw,
    id: String(id),
    title: raw.title || raw.name || raw.goal || raw.objective || `Run ${String(id).slice(0, 8)}`,
    goal: raw.goal || raw.objective || raw.title || "",
    workspace: raw.workspace || raw.repo || raw.cwd || "—",
    status,
    phase: raw.phase || raw.current_phase || raw.layer || "queued",
    progress: Number.isFinite(progress) ? Math.max(0, Math.min(100, progress <= 1 && progress > 0 ? progress * 100 : progress)) : 0,
    createdAt: raw.created_at || raw.createdAt || raw.queued_at || null,
    startedAt: raw.started_at || raw.startedAt || null,
    finishedAt: raw.finished_at || raw.finishedAt || raw.completed_at || null,
    summary: raw.summary || raw.status_message || raw.message || "",
    roster: {
      proposers: config.proposers || [],
      refiners: config.refiners || [],
      aggregator: config.aggregator || null,
    },
    agents: raw.agents || raw.tasks || [],
    artifacts: raw.artifacts || raw.outputs || {},
  };
}

export async function getProviders() {
  const payload = await request("/api/providers");
  return arrayFrom(payload, "providers").map(normalizeProvider);
}

export async function getModels() {
  try {
    const payload = await request("/api/models");
    return arrayFrom(payload, "models").map((model) => ({
      ...model,
      id: model.id || model.provider_id || model.name,
      name: model.name || model.label || model.id,
      providerId: model.provider_id || model.provider || model.id,
      canonicalModel: model.canonical_model || model.model || model.model_id || model.id,
      effort: model.effort || model.reasoning_effort || model.variant || "default",
      lab: model.lab || model.vendor || model.provider_id || "Independent",
      roles: model.roles || model.supported_roles,
      defaultRoles: model.default_roles || [],
    }));
  } catch (error) {
    if (!String(error.message).includes("404")) throw error;
    return [];
  }
}

export async function getJobs(params = {}) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== "" && value !== undefined && value !== null) search.set(key, value);
  });
  const payload = await request(`/api/jobs${search.size ? `?${search}` : ""}`);
  return arrayFrom(payload, "jobs").map(normalizeJob);
}

export async function getJob(id) {
  return normalizeJob(await request(`/api/jobs/${encodeURIComponent(id)}`));
}

export async function getWorkspaces() {
  try {
    const payload = await request("/api/workspaces");
    const combined = Array.isArray(payload?.workspaces)
      ? payload.workspaces
      : [payload?.current, ...(payload?.recent || []), ...(payload?.roots || [])].filter(Boolean);
    const unique = [...new Set(combined.map((item) => typeof item === "string" ? item : item.path || item.value))];
    return unique.map((path) => combined.find((item) => typeof item !== "string" && (item.path || item.value) === path) || path).map((item) => (
      typeof item === "string" ? { path: item, name: item.split("/").filter(Boolean).pop() || item } : item
    ));
  } catch (error) {
    if (!String(error.message).includes("404")) throw error;
    return [];
  }
}

export async function createJob(payload) {
  return normalizeJob(await request("/api/jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  }));
}

export async function saveProfile(profile) {
  return request("/api/profiles", {
    method: "POST",
    body: JSON.stringify({
      id: profile.id,
      display_name: profile.name,
      settings: { compact_events: profile.compactEvents },
    }),
  });
}

export async function importHistory(workspace) {
  return request("/api/history/import", {
    method: "POST",
    body: JSON.stringify({ workspace }),
  });
}

export async function getProfile(id) {
  try {
    return await request(`/api/profiles/${encodeURIComponent(id)}`);
  } catch (error) {
    if (error.status === 404) return null;
    throw error;
  }
}

export async function getGithubRepos() {
  try {
    const payload = await request("/api/github/repos");
    return arrayFrom(payload, "repositories").map((repo) => ({
      ...repo,
      id: repo.id || repo.nameWithOwner || repo.full_name || repo.name,
      fullName: repo.nameWithOwner || repo.full_name || `${payload.owner}/${repo.name}`,
      name: repo.name || String(repo.nameWithOwner || repo.full_name || "").split("/").pop(),
      description: repo.description || "",
      workspace: repo.workspace || repo.local_path || repo.path || null,
      private: Boolean(repo.isPrivate ?? repo.private),
      pushedAt: repo.pushedAt || repo.pushed_at || null,
    }));
  } catch (error) {
    if (error.status === 404 || error.status === 501) return [];
    throw error;
  }
}

export async function uploadFiles(files, metadata = {}) {
  if (!files?.length) return [];
  const body = new FormData();
  files.forEach((file) => body.append("files", file, file.name));
  Object.entries(metadata).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") body.append(key, String(value));
  });
  const headers = { "Accept": "application/json" };
  const token = csrfToken();
  if (token) headers["X-CSRF-Token"] = token;
  const response = await fetch("/api/uploads", {
    method: "POST",
    headers,
    body,
    credentials: "same-origin",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || payload.message || `Upload failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return payload.uploads || [];
}

export async function checkoutGithubRepo(repo, profileId) {
  return request("/api/workspaces/github", {
    method: "POST",
    body: JSON.stringify({ repo, profile_id: profileId }),
  });
}

export async function cancelJob(id) {
  return request(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" });
}

export async function redispatchJob(id, payload) {
  return request(`/api/jobs/${encodeURIComponent(id)}/redispatch`, {
    method: "POST",
    body: JSON.stringify(payload || {}),
  });
}

export async function probeProvider(id) {
  return normalizeProvider(await request(`/api/providers/${encodeURIComponent(id)}/probe`, {
    method: "POST",
    body: "{}",
  }));
}

export async function probeAllProviders() {
  return Promise.all((await getProviders()).map((provider) => probeProvider(provider.id).catch(() => provider)));
}

export function subscribeToJob(id, { onEvent, onState, onError } = {}) {
  let source;
  let stopped = false;
  let pollTimer;
  let lastEventId = "";

  const poll = async () => {
    if (stopped) return;
    try {
      onState?.(await getJob(id));
    } catch (error) {
      onError?.(error);
    } finally {
      if (!stopped) pollTimer = window.setTimeout(poll, 3000);
    }
  };

  if ("EventSource" in window) {
    const query = lastEventId ? `?after=${encodeURIComponent(lastEventId)}` : "";
    source = new EventSource(`/api/jobs/${encodeURIComponent(id)}/events${query}`, { withCredentials: true });
    const consume = (message) => {
      lastEventId = message.lastEventId || lastEventId;
      try {
        const event = JSON.parse(message.data);
        onEvent?.({
          ...(event.data || {}),
          ...event,
          type: event.type || event.kind || message.type,
          id: event.id || event.seq || message.lastEventId,
        });
      } catch {
        onEvent?.({ type: message.type || "message", message: message.data, id: message.lastEventId });
      }
    };
    source.onmessage = consume;
    ["job", "phase", "agent", "progress", "artifact", "warning", "error", "complete", "log", "heartbeat"].forEach((type) => {
      source.addEventListener(type, consume);
    });
    source.onopen = () => onState?.({ connection: "live" });
    source.onerror = () => {
      source?.close();
      if (!pollTimer && !stopped) {
        onState?.({ connection: "polling" });
        poll();
      }
    };
  } else {
    onState?.({ connection: "polling" });
    poll();
  }

  return () => {
    stopped = true;
    source?.close();
    window.clearTimeout(pollTimer);
  };
}
