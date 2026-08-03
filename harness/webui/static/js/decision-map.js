(function decisionMapModule(global) {
  "use strict";

  const STAGE_LABELS = {
    setup: "Ready for dispatch",
    proposals: "Evidence collected",
    review: "Claims under review",
    complete: "Decision complete",
  };
  const STATUS_LABELS = {
    unreviewed: "Not reviewed",
    unverified: "Unverified",
    verified: "Verified",
    contradicted: "Contradicted",
    disputed: "Disputed",
    pending: "Pending",
  };

  function element(tag, attributes = {}, children = []) {
    const node = document.createElement(tag);
    Object.entries(attributes).forEach(([key, value]) => {
      if (value === undefined || value === null) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "hidden") node.hidden = Boolean(value);
      else node.setAttribute(key, String(value));
    });
    const values = Array.isArray(children) ? children : [children];
    values.filter(Boolean).forEach((child) => node.append(child));
    return node;
  }

  function titleCase(value) {
    return String(value || "")
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function percent(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? `${Math.round(numeric * 100)}%` : "—";
  }

  function copy(value) {
    return value && typeof value === "object"
      ? JSON.parse(JSON.stringify(value))
      : value;
  }

  function pendingQuality() {
    return {
      level: "pending",
      evidence_ceiling: "pending",
      stated_confidence: null,
      effective_confidence: "pending",
      total_claims: 0,
      critical_claims: 0,
      supported_critical_claims: 0,
      verified_critical_claims: 0,
      contradicted_critical_claims: 0,
      support_coverage: 0,
      review_coverage: 0,
      verification_coverage: 0,
      independent_verified_coverage: 0,
      step_evidence_coverage: 0,
      source_concentration: 0,
      independent_sources: 0,
      external_domains: [],
      gates: [],
      warnings: ["Evidence gates begin when proposal output is retained."],
    };
  }

  function createSetupMap({ title, summary, agents = [], stage = "setup" } = {}) {
    const normalizedAgents = agents.map((agent, index) => ({
      id: String(agent.id || agent.name || `agent-${index + 1}`),
      label: String(agent.label || agent.name || agent.id || `Agent ${index + 1}`),
      role: String(agent.role || "agent"),
      lab_id: String(agent.lab_id || agent.labId || "independent"),
      lab: String(agent.lab || "Independent"),
      model: String(agent.model || agent.id || "Configured model"),
      status: String(agent.status || "queued"),
    }));
    const claim = {
      id: "claim-pending",
      text: stage === "setup"
        ? "Evidence receipts and independently reviewed claims will appear here."
        : "The next retained checkpoint will populate evidence and claim status.",
      status: "pending",
      critical: true,
      proposer_ids: [],
      evidence_ids: [],
      verification_ids: [],
      decision_ids: ["decision-pending"],
      independent_sources: 0,
      verified_reviewer_labs: [],
    };
    const edges = normalizedAgents.map((agent) => ({
      id: `edge-${agent.id}-pending`,
      from: `agent:${agent.id}`,
      to: claim.id,
      kind: agent.role === "refiner" ? "verifies" : "contributes",
      status: agent.status,
    }));
    edges.push({
      id: "edge-pending-decision",
      from: claim.id,
      to: "decision-pending",
      kind: "pending",
      status: "pending",
    });
    return {
      version: 1,
      session_id: "preview",
      input_digest: "",
      stage,
      generated_at: null,
      title: title || "Evidence-weighted decision",
      summary: summary || "The same map will evolve as evidence, review findings, and final decisions arrive.",
      repository: {},
      agents: normalizedAgents,
      evidence: [],
      claims: [claim],
      verifications: [],
      decisions: [{
        id: "decision-pending",
        title: "Final implementation decision",
        description: "The aggregator will connect accepted steps to their material evidence.",
        status: "pending",
        claim_ids: [claim.id],
        evidence_ids: [],
        files_touched: [],
      }],
      edges,
      quality: pendingQuality(),
      warnings: [],
    };
  }

  function mergeAgentState(map, agents = [], stage) {
    const output = copy(map) || createSetupMap();
    const live = new Map(agents.map((agent) => [String(agent.id), agent]));
    output.agents = (output.agents || []).map((agent) => {
      const current = live.get(String(agent.id));
      if (!current) return agent;
      return {
        ...agent,
        label: current.label || current.name || agent.label,
        model: current.model || agent.model,
        lab_id: current.lab_id || current.labId || agent.lab_id,
        lab: current.lab || agent.lab,
        status: current.status || agent.status,
      };
    });
    live.forEach((agent, id) => {
      if (output.agents.some((existing) => String(existing.id) === id)) return;
      output.agents.push({
        id,
        label: agent.label || agent.name || id,
        role: agent.role || "agent",
        lab_id: agent.lab_id || agent.labId || "independent",
        lab: agent.lab || "Independent",
        model: agent.model || id,
        status: agent.status || "queued",
      });
    });
    if (stage) output.stage = stage;
    return output;
  }

  function nodeIndex(map) {
    const index = new Map();
    (map.agents || []).forEach((item) => index.set(`agent:${item.id}`, { kind: "agent", item }));
    (map.evidence || []).forEach((item) => index.set(item.id, { kind: "evidence", item }));
    (map.claims || []).forEach((item) => index.set(item.id, { kind: "claim", item }));
    (map.decisions || []).forEach((item) => index.set(item.id, { kind: "decision", item }));
    return index;
  }

  function detailFor(kind, item, map) {
    if (kind === "agent") {
      return {
        eyebrow: `${titleCase(item.role)} · ${titleCase(item.status)}`,
        title: item.label,
        body: `${item.lab || "Independent lab"} · ${item.model || "Configured model"}`,
        meta: [],
      };
    }
    if (kind === "evidence") {
      const integrity = item.capture_status === "captured"
        ? "Repository content was hashed during the live run."
        : item.capture_status === "declared_excerpt"
          ? "This is the model-declared source excerpt; no page snapshot was captured."
          : `Capture status: ${titleCase(item.capture_status || "unknown")}.`;
      return {
        eyebrow: `${titleCase(item.type)} evidence receipt`,
        title: item.label,
        body: item.snippet || integrity,
        meta: [
          item.file_sha256 ? `File SHA-256 ${item.file_sha256.slice(0, 16)}…` : "",
          item.line_sha256 ? `Line SHA-256 ${item.line_sha256.slice(0, 16)}…` : "",
          item.url || "",
          integrity,
        ].filter(Boolean),
      };
    }
    if (kind === "claim") {
      const reviews = (map.verifications || []).filter((value) => value.claim_id === item.id);
      return {
        eyebrow: `${item.critical ? "Critical" : "Supporting"} claim · ${STATUS_LABELS[item.status] || titleCase(item.status)}`,
        title: item.text,
        body: reviews.length
          ? reviews.map((value) => `${value.reviewer_id}: ${value.finding}`).join(" · ")
          : "No resolved reviewer finding is attached to this claim yet.",
        meta: [
          `${item.evidence_ids?.length || 0} evidence receipt${item.evidence_ids?.length === 1 ? "" : "s"}`,
          `${item.verified_reviewer_labs?.length || 0} verifying model lab${item.verified_reviewer_labs?.length === 1 ? "" : "s"}`,
        ],
      };
    }
    return {
      eyebrow: `${titleCase(item.status)} final decision`,
      title: item.title,
      body: item.description || "No description recorded.",
      meta: [
        `${item.claim_ids?.length || 0} linked claim${item.claim_ids?.length === 1 ? "" : "s"}`,
        ...(item.files_touched || []).map((value) => `File: ${value}`),
      ],
    };
  }

  function renderDetail(host, kind, item, map) {
    const detail = detailFor(kind, item, map);
    host.replaceChildren(
      element("span", { class: "dm-detail-kicker", text: detail.eyebrow }),
      element("strong", { class: "dm-detail-title", text: detail.title }),
      element("p", { class: "dm-detail-body", text: detail.body }),
    );
    if (detail.meta.length) {
      host.append(element(
        "ul",
        { class: "dm-detail-meta" },
        detail.meta.map((value) => element("li", { text: value })),
      ));
    }
  }

  function renderNode(kind, item, map, detail, select) {
    const id = kind === "agent" ? `agent:${item.id}` : item.id;
    const state = kind === "agent" ? item.status : item.status || item.capture_status;
    const eyebrow = kind === "agent"
      ? `${titleCase(item.role)} · ${item.lab || item.lab_id || "Independent"}`
      : kind === "evidence"
        ? `${titleCase(item.type)} · ${titleCase(item.capture_status || "recorded")}`
        : kind === "claim"
          ? `${item.critical ? "Critical" : "Supporting"} · ${STATUS_LABELS[item.status] || titleCase(item.status)}`
          : `${titleCase(item.status)} decision`;
    const title = kind === "agent" ? item.label
      : kind === "evidence" ? item.label
        : kind === "claim" ? item.text
          : item.title;
    const button = element("button", {
      class: `dm-node dm-node-${kind} is-${String(state || "pending")}`,
      type: "button",
      "data-map-node": id,
      "aria-label": `${eyebrow}: ${title}`,
    }, [
      element("span", { class: "dm-node-kicker", text: eyebrow }),
      element("strong", { text: title }),
    ]);
    button.addEventListener("click", () => select(id, kind, item));
    button.addEventListener("focus", () => select(id, kind, item, false));
    return button;
  }

  function renderLedger(map) {
    const claims = map.claims || [];
    const details = element("details", { class: "dm-ledger" });
    details.append(element("summary", {
      text: `Accessible evidence ledger · ${claims.length} claim${claims.length === 1 ? "" : "s"}`,
    }));
    if (!claims.length) {
      details.append(element("p", { class: "dm-empty", text: "No claims have been retained yet." }));
      return details;
    }
    const table = element("table", { class: "dm-ledger-table" });
    table.append(element("thead", {}, element("tr", {}, [
      element("th", { scope: "col", text: "Claim" }),
      element("th", { scope: "col", text: "Status" }),
      element("th", { scope: "col", text: "Evidence" }),
      element("th", { scope: "col", text: "Independent review" }),
      element("th", { scope: "col", text: "Decision use" }),
    ])));
    const body = element("tbody");
    claims.forEach((claim) => {
      body.append(element("tr", { class: claim.critical ? "is-critical" : "" }, [
        element("th", { scope: "row", text: claim.text }),
        element("td", { text: STATUS_LABELS[claim.status] || titleCase(claim.status) }),
        element("td", { text: String(claim.evidence_ids?.length || 0) }),
        element("td", { text: String(claim.verified_reviewer_labs?.length || 0) }),
        element("td", { text: String(claim.decision_ids?.length || 0) }),
      ]));
    });
    table.append(body);
    details.append(element("div", { class: "dm-table-scroll", tabindex: "0", role: "region", "aria-label": "Decision-map evidence ledger" }, table));
    return details;
  }

  function renderQuality(map) {
    const quality = map.quality || pendingQuality();
    const panel = element("div", { class: "dm-quality" });
    const metrics = [
      ["Evidence ceiling", titleCase(quality.evidence_ceiling || "pending")],
      ["Effective / stated", `${titleCase(quality.effective_confidence || "pending")} / ${titleCase(quality.stated_confidence || "none")}`],
      ["Critical verified", `${quality.verified_critical_claims || 0}/${quality.critical_claims || 0}`],
      ["Independent review", percent(quality.independent_verified_coverage)],
      ["Independent sources", String(quality.independent_sources || 0)],
    ];
    metrics.forEach(([label, value]) => panel.append(element("div", { class: "dm-metric" }, [
      element("span", { text: label }),
      element("strong", { text: value }),
    ])));
    return panel;
  }

  function renderGates(map) {
    const quality = map.quality || pendingQuality();
    const details = element("details", { class: "dm-gates" });
    details.append(element("summary", {
      text: `Quality gates · ${(quality.gates || []).filter((gate) => gate.status === "pass").length}/${(quality.gates || []).filter((gate) => gate.status !== "pending" && gate.status !== "n/a").length || 0} passing`,
    }));
    const list = element("ul");
    (quality.gates || []).forEach((gate) => list.append(element("li", { class: `is-${gate.status.replace("/", "-")}` }, [
      element("span", { class: "dm-gate-status", text: gate.status }),
      element("strong", { text: gate.label }),
      element("p", { text: gate.detail }),
    ])));
    if (!(quality.gates || []).length) list.append(element("li", { text: "Quality gates begin with retained proposal evidence." }));
    details.append(list);
    return details;
  }

  function visibleGraph(map, maxClaims) {
    const allClaims = map.claims || [];
    const critical = allClaims.filter((claim) => claim.critical);
    const claims = (critical.length ? critical : allClaims).slice(0, maxClaims);
    const claimIds = new Set(claims.map((claim) => claim.id));
    const evidenceIds = new Set(claims.flatMap((claim) => claim.evidence_ids || []));
    const decisionIds = new Set(claims.flatMap((claim) => claim.decision_ids || []));
    const evidence = (map.evidence || []).filter((item) => evidenceIds.has(item.id));
    const decisions = (map.decisions || []).filter((item) => decisionIds.has(item.id));
    return { claims, evidence, decisions, hiddenClaims: Math.max(0, (critical.length || allClaims.length) - claims.length) };
  }

  function drawEdges(network, svg, map, visibleIds) {
    const bounds = network.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    svg.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`);
    svg.setAttribute("width", String(bounds.width));
    svg.setAttribute("height", String(bounds.height));
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
    marker.setAttribute("id", `dm-arrow-${network.dataset.mapInstance}`);
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "9");
    marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "5");
    marker.setAttribute("markerHeight", "5");
    marker.setAttribute("orient", "auto-start-reverse");
    const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path");
    arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    arrow.setAttribute("fill", "currentColor");
    marker.append(arrow);
    defs.append(marker);
    svg.replaceChildren(defs);
    (map.edges || []).forEach((edge) => {
      if (!visibleIds.has(edge.from) || !visibleIds.has(edge.to)) return;
      const source = network.querySelector(`[data-map-node="${CSS.escape(edge.from)}"]`);
      const target = network.querySelector(`[data-map-node="${CSS.escape(edge.to)}"]`);
      if (!source || !target) return;
      const from = source.getBoundingClientRect();
      const to = target.getBoundingClientRect();
      const x1 = from.right - bounds.left;
      const y1 = from.top + from.height / 2 - bounds.top;
      const x2 = to.left - bounds.left;
      const y2 = to.top + to.height / 2 - bounds.top;
      const bend = Math.max(28, (x2 - x1) * 0.46);
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
      path.setAttribute("class", `dm-edge is-${edge.kind} state-${edge.status}`);
      path.setAttribute("data-edge-from", edge.from);
      path.setAttribute("data-edge-to", edge.to);
      path.setAttribute("marker-end", `url(#dm-arrow-${network.dataset.mapInstance})`);
      svg.append(path);
    });
  }

  let instanceSequence = 0;
  function render(host, rawMap, options = {}) {
    if (!host) return { destroy() {} };
    const map = copy(rawMap) || createSetupMap();
    const maxClaims = Number(options.maxClaims) || (options.mode === "report" ? 16 : 10);
    const graph = visibleGraph(map, maxClaims);
    const index = nodeIndex(map);
    host.replaceChildren();
    host.classList.add("decision-map-host");
    host.dataset.stage = map.stage || "setup";

    const shell = element("section", { class: `dm-shell dm-mode-${options.mode || "app"}`, "aria-label": "Evidence-weighted living decision map" });
    const header = element("header", { class: "dm-header" }, [
      element("div", { class: "dm-header-copy" }, [
        element("span", { class: "dm-eyebrow", text: "LIVING DECISION MAP" }),
        element("h3", { text: map.title || "Evidence-weighted decision" }),
        element("p", { text: map.summary || "Evidence, claims, and decisions share one trace." }),
      ]),
      element("span", { class: `dm-stage is-${map.stage || "setup"}`, text: STAGE_LABELS[map.stage] || titleCase(map.stage) }),
    ]);
    shell.append(header, renderQuality(map));

    const network = element("div", { class: "dm-network" });
    network.dataset.mapInstance = String(++instanceSequence);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "dm-edge-layer");
    svg.setAttribute("aria-hidden", "true");
    network.append(svg);
    const detail = element("aside", { class: "dm-detail", "aria-live": "polite" });
    const visibleIds = new Set();

    const select = (id, kind, item, focusDetail = true) => {
      network.querySelectorAll(".dm-node").forEach((node) => {
        const selected = node.dataset.mapNode === id;
        const connected = (map.edges || []).some((edge) => (
          (edge.from === id && edge.to === node.dataset.mapNode)
          || (edge.to === id && edge.from === node.dataset.mapNode)
        ));
        node.classList.toggle("is-selected", selected);
        node.classList.toggle("is-connected", connected);
        node.classList.toggle("is-muted", !selected && !connected);
      });
      svg.querySelectorAll(".dm-edge").forEach((edge) => {
        const connected = edge.dataset.edgeFrom === id || edge.dataset.edgeTo === id;
        edge.classList.toggle("is-selected", connected);
        edge.classList.toggle("is-muted", !connected);
      });
      renderDetail(detail, kind, item, map);
      if (focusDetail) detail.scrollIntoView({ block: "nearest" });
    };

    const agentRail = element("section", { class: "dm-agent-rail", "aria-label": "Ensemble lanes" }, [
      element("span", { class: "dm-lane-title", text: "Model-lab lanes" }),
    ]);
    (map.agents || []).forEach((agent) => {
      const id = `agent:${agent.id}`;
      visibleIds.add(id);
      agentRail.append(renderNode("agent", agent, map, detail, select));
    });
    network.append(agentRail);

    const canvas = element("div", { class: "dm-canvas" });
    const lanes = [
      ["evidence", "Evidence receipts", graph.evidence],
      ["claim", "Claims under review", graph.claims],
      ["decision", "Final decisions", graph.decisions],
    ];
    lanes.forEach(([kind, label, values]) => {
      const lane = element("section", { class: `dm-lane dm-lane-${kind}`, "aria-label": label }, [
        element("span", { class: "dm-lane-title", text: label }),
      ]);
      values.forEach((item) => {
        visibleIds.add(item.id);
        lane.append(renderNode(kind, item, map, detail, select));
      });
      if (!values.length) {
        lane.append(element("p", {
          class: "dm-empty",
          text: kind === "evidence"
            ? "Receipts appear after proposal evidence is retained."
            : kind === "claim"
              ? "Claims appear after proposal validation."
              : "Decisions appear after final synthesis.",
        }));
      }
      canvas.append(lane);
    });
    network.append(canvas);
    if (graph.hiddenClaims) network.append(element("p", { class: "dm-overflow-note", text: `${graph.hiddenClaims} additional claim${graph.hiddenClaims === 1 ? " is" : "s are"} available in the ledger below.` }));
    shell.append(network, detail, renderGates(map), renderLedger(map));

    const warnings = map.warnings || map.quality?.warnings || [];
    if (warnings.length) {
      const warningBox = element("details", { class: "dm-warnings" });
      warningBox.append(element("summary", { text: `Evidence debt · ${warnings.length} item${warnings.length === 1 ? "" : "s"}` }));
      warningBox.append(element("ul", {}, warnings.map((warning) => element("li", { text: warning }))));
      shell.append(warningBox);
    }
    host.append(shell);

    const firstClaim = graph.claims[0];
    if (firstClaim) renderDetail(detail, "claim", firstClaim, map);
    else if (map.agents?.[0]) renderDetail(detail, "agent", map.agents[0], map);
    const redraw = () => drawEdges(network, svg, map, visibleIds);
    // Draw once before returning so rapid live-state rerenders never leave an
    // otherwise complete graph without edges for a frame. The animation-frame
    // pass then corrects geometry after layout/font settling.
    redraw();
    requestAnimationFrame(redraw);
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(redraw) : null;
    observer?.observe(network);
    return { destroy() { observer?.disconnect(); } };
  }

  global.MoaDecisionMap = Object.freeze({
    createSetupMap,
    mergeAgentState,
    render,
  });
})(window);
