"use strict";

// Saved trajectories are untrusted: every visible payload uses textContent.
const data = JSON.parse(
  document.getElementById("evaluation-record").textContent,
);
const locale = JSON.parse(document.getElementById("ui-locale").textContent);
let strings = locale.strings;
const $ = (id) => document.getElementById(id);
const t = (key, values = {}) =>
  (strings[key] || key).replace(/\{(\w+)\}/g, (_, name) =>
    String(values[name] ?? ""),
  );
const list = (value) => (Array.isArray(value) ? value : []);
const object = (value) =>
  value && typeof value === "object" && !Array.isArray(value) ? value : {};
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const fmt = (value, digits = 4) => {
  if (!finite(value)) return "—";
  const magnitude = Math.abs(value);
  return magnitude !== 0 && (magnitude < 10 ** -digits || magnitude >= 1e7)
    ? value.toExponential(digits)
    : value.toFixed(digits);
};
const observed = (value) =>
  typeof value === "boolean" ? Number(value) : finite(value) ? value : null;
const pretty = (value) =>
  typeof value === "string" ? value : JSON.stringify(value ?? null, null, 2);
const position = (prefix, index) =>
  Number.isInteger(index) ? prefix + (index + 1) : prefix + "?";
const element = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
};
const math = (text) => element("div", text, "math");
const note = (text) => element("p", text, "note");
const badge = (code, text) =>
  element("span", text || t("status_" + code), "status " + code);
const truth = (value) =>
  typeof value === "boolean" ? t(value ? "yes" : "no") : "—";
const groupName = (name) => strings["group_" + name] || name || "—";
const displayQuery = (item) => {
  const query = object(item.metadata).display_query;
  return typeof query === "string" && query.trim()
    ? query
    : object(item.case).request || t("no_request");
};
const judgementFlag = (j) =>
  j.status === "ok"
    ? badge(
        observed(j.value) === 0 ? "failed" : "ok",
        typeof j.value === "boolean" ? truth(j.value) : fmt(j.value),
      )
    : badge(
        j.status || "neutral",
        j.status ? t("status_" + j.status) : t("unavailable"),
      );
let records = Array.isArray(data) ? data : [data];
const stages = ["gate", "base", "deduction", "bonus", "reward"];
const ALL_GROUPS = "*";
let selectedRecord = 0,
  selectedSubject = null,
  selectedGroup = null,
  activeStage = "reward";
let record,
  caseData,
  metadata,
  profile,
  subjects,
  tasks,
  criteria,
  groups,
  subjectMap;
let evidenceMap = new Map(),
  evidencePieces = [],
  annotationNotes = [],
  highlightedTargets = [];

function refreshLocalization() {
  for (const node of document.querySelectorAll("[data-i18n]"))
    node.textContent = t(node.dataset.i18n);
  document.title = "Reward Every Effort | " + t("title");
  if (document.documentElement)
    document.documentElement.lang = locale.locale === "zh" ? "zh-CN" : "en";
  $("query-search").placeholder = t("query_search");
  $("query-search").setAttribute("aria-label", t("query_search"));
  $("search").placeholder = t("search");
  $("search").setAttribute("aria-label", t("search"));
  $("status-filter").setAttribute("aria-label", t("all_statuses"));
  $("flow-diagram").setAttribute("aria-label", t("flow_title"));
  const state = $("status-filter").value || "all";
  $("status-filter").replaceChildren();
  for (const code of [
    "all",
    "failed",
    "ok",
    "not_applicable",
    "missing_evidence",
    "error",
    "not_run",
  ]) {
    const option = element(
      "option",
      code === "all"
        ? t("all_statuses")
        : code === "failed"
          ? t("failed")
          : code === "ok"
            ? t("evaluated")
            : t("status_" + code),
    );
    option.value = code;
    $("status-filter").append(option);
  }
  $("status-filter").value = state;
  const link = $("alternate-language");
  if (locale.other || locale.alternate) {
    link.hidden = false;
    link.textContent = locale.other
      ? locale.other.locale === "zh"
        ? "中文"
        : "English"
      : locale.alternate.label;
    if (locale.alternate) link.href = locale.alternate.href;
  }
}
function switchLanguage(event) {
  if (!locale.other) return;
  event.preventDefault();
  const current = { locale: locale.locale, strings };
  const next = locale.other;
  locale.locale = next.locale;
  locale.strings = next.strings;
  strings = next.strings;
  locale.other = current;
  if (locale.alternate && locale.current_href) {
    const href = locale.alternate.href;
    locale.alternate.href = locale.current_href;
    locale.current_href = href;
  }
  refreshLocalization();
  $("data-feedback").hidden = true;
  $("import-error").hidden = true;
  if (record) selectRecord(selectedRecord, { preserveState: true });
  else {
    renderEmptyRecord();
    renderQueryList();
  }
}
refreshLocalization();
$("print").addEventListener("click", () => window.print());
$("alternate-language").addEventListener("click", switchLanguage);
$("query-search").addEventListener("input", applyQueryFilter);
$("search").addEventListener("input", () => {
  clearAnnotations();
  renderCriteria();
});
$("status-filter").addEventListener("change", () => {
  clearAnnotations();
  renderCriteria();
});
for (const stage of stages)
  $("flow-" + stage).addEventListener("click", () => selectStage(stage, true));

function matchingRecordIndices() {
  const query = $("query-search").value.trim().toLowerCase();
  return records.flatMap((item, index) => {
    const title = displayQuery(item);
    return !query || title.toLowerCase().includes(query) ? [index] : [];
  });
}
function applyQueryFilter() {
  const matches = matchingRecordIndices();
  if (!matches.includes(selectedRecord)) {
    if (matches.length) selectRecord(matches[0]);
    else {
      selectedRecord = null;
      renderEmptyRecord();
      renderQueryList();
    }
  } else renderQueryList();
}
function renderQueryList() {
  const target = $("query-list");
  target.replaceChildren();
  const matches = matchingRecordIndices();
  for (const index of matches) {
    const item = records[index],
      title = displayQuery(item);
    const button = element(
      "button",
      undefined,
      "query-item" + (index === selectedRecord ? " active" : ""),
    );
    button.type = "button";
    button.title = title;
    button.setAttribute("aria-pressed", index === selectedRecord);
    button.append(element("span", title, "query-title"));
    const info = element("span", undefined, "query-meta");
    info.append(
      element("span", t("record_index", { index: index + 1 })),
      element("strong", fmt(item.status === "ok" ? item.reward : null, 3)),
    );
    button.append(info);
    button.addEventListener("click", () => selectRecord(index));
    target.append(button);
  }
  $("query-count").textContent = t("query_count", { count: matches.length });
  if (!matches.length) target.append(note(t("no_matches")));
}

function clearAnnotations() {
  for (const { node, text } of evidencePieces) node.textContent = text;
  for (const target of highlightedTargets)
    target.className = target._traceClass;
  for (const box of annotationNotes) box.remove?.();
  annotationNotes = [];
  highlightedTargets = [];
}
function automaticJump() {
  return !window.matchMedia?.("(max-width: 599px)").matches;
}
function evidenceRefs(judgement) {
  return [
    ...new Set([
      ...list(judgement.evidence_refs),
      ...list(judgement.annotations)
        .map(object)
        .map((a) => a.ref),
    ]),
  ].filter((ref) => typeof ref === "string");
}
function openEvidencePiece(piece) {
  for (let node = piece.node; node; node = node.parentElement)
    if (node.tagName === "DETAILS") node.open = true;
}
function annotate(judgement, jump = true) {
  clearAnnotations();
  const kinds = new Map(),
    reasons = new Map(),
    ranges = new Map();
  const priority = { related: 0, support: 1, issue: 2 };
  function highlight(target, kind, reason) {
    if (!kinds.has(target) || priority[kind] > priority[kinds.get(target)])
      kinds.set(target, kind);
    if (reason) {
      if (!reasons.has(target)) reasons.set(target, new Set());
      reasons.get(target).add(reason);
    }
  }
  for (const ref of evidenceRefs(judgement)) {
    const entry = evidenceMap.get(ref);
    if (!entry) continue;
    for (const piece of entry.pieces) {
      openEvidencePiece(piece);
      highlight(piece.target, "related");
    }
    if (!entry.pieces.length) highlight(entry.target, "related");
  }
  for (const annotation of list(judgement.annotations).map(object)) {
    if (
      !["issue", "support"].includes(annotation.kind) ||
      !evidenceMap.has(annotation.ref)
    )
      continue;
    const entry = evidenceMap.get(annotation.ref);
    const quote = annotation.quote;
    if (quote != null && (typeof quote !== "string" || !quote.length)) continue;
    const pieces =
      typeof quote === "string"
        ? entry.pieces.filter((piece) => piece.text.includes(quote))
        : entry.pieces;
    for (const piece of pieces) {
      openEvidencePiece(piece);
      highlight(piece.target, annotation.kind, annotation.reason);
      if (typeof quote !== "string") continue;
      if (!ranges.has(piece)) ranges.set(piece, []);
      let offset = 0,
        at;
      while ((at = piece.text.indexOf(quote, offset)) !== -1) {
        ranges
          .get(piece)
          .push({ start: at, end: at + quote.length, kind: annotation.kind });
        offset = at + quote.length;
      }
    }
    if (!quote && !entry.pieces.length)
      highlight(entry.target, annotation.kind, annotation.reason);
  }
  const anchors = [];
  for (const [piece, hits] of ranges) {
    const boundaries = [
      ...new Set([
        0,
        piece.text.length,
        ...hits.flatMap((hit) => [hit.start, hit.end]),
      ]),
    ].sort((a, b) => a - b);
    const segments = [];
    for (let i = 0; i < boundaries.length - 1; i++) {
      const start = boundaries[i],
        end = boundaries[i + 1];
      const matching = hits.filter(
        (hit) => hit.start <= start && hit.end >= end,
      );
      const kind = matching.some((hit) => hit.kind === "issue")
        ? "issue"
        : matching.length
          ? "support"
          : null;
      const previous = segments[segments.length - 1];
      if (previous && previous.kind === kind) previous.end = end;
      else segments.push({ start, end, kind });
    }
    piece.node.replaceChildren();
    for (const segment of segments) {
      const node = element(
        segment.kind ? "mark" : "span",
        piece.text.slice(segment.start, segment.end),
        segment.kind ? "quote-" + segment.kind : undefined,
      );
      piece.node.append(node);
      if (segment.kind)
        anchors.push({ target: piece.target, node, kind: segment.kind });
    }
  }
  for (const [target, kind] of kinds) {
    target._traceClass = target.className;
    highlightedTargets.push(target);
    target.className +=
      " evidence-" +
      kind +
      (kind === "related" ? " selected-evidence" : "") +
      (anchors.some((anchor) => anchor.target === target) ? " has-quote" : "");
    const label = t(
      kind === "issue"
        ? "issue_location"
        : kind === "support"
          ? "support_evidence"
          : "related_evidence",
    );
    const marker = element("span", label, "trace-evidence-label " + kind);
    if (target._traceHeader) target._traceHeader.append(marker);
    else target.append(marker);
    annotationNotes.push(marker);
    const detail = element(
      "div",
      [...(reasons.get(target) || [])].join(" / ") || label,
      "annotation-note " + kind,
    );
    target.append(detail);
    annotationNotes.push(detail);
  }
  if (jump && kinds.size) {
    const entries = [...kinds];
    const primary =
      entries.find(([, kind]) => kind === "issue") ||
      entries.find(([, kind]) => kind === "support") ||
      entries[0];
    const anchor =
      anchors.find((a) => a.target === primary[0] && a.kind === "issue") ||
      anchors.find((a) => a.target === primary[0]);
    reveal(primary[0], anchor?.node);
  }
}
function reveal(target, anchor = target) {
  setMobilePane("trace");
  for (let parent = anchor; parent; parent = parent.parentElement)
    if (parent.tagName === "DETAILS") parent.open = true;
  target.tabIndex = -1;
  anchor.scrollIntoView?.({
    block: "center",
    behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
  });
  target.focus?.({ preventScroll: true });
}
function references(judgement) {
  const row = element("div", undefined, "refs"),
    button = inspectButton(judgement);
  if (button) row.append(button);
  return row;
}
function inspectButton(judgement) {
  const refs = evidenceRefs(judgement);
  if (!refs.length) return null;
  const button = element("button", t("evidence_jump"), "inspect-evidence");
  button.type = "button";
  button.disabled = !refs.some((ref) => evidenceMap.has(ref));
  if (button.disabled) button.title = t("unresolved_ref");
  button.addEventListener("click", () => annotate(judgement));
  return button;
}
function addPiece(
  target,
  content,
  className = "message-body",
  evidenceTarget = target,
) {
  const text = pretty(content),
    node = element("div", text, className + " evidence-piece");
  target.append(node);
  const piece = { node, text, target: evidenceTarget };
  evidencePieces.push(piece);
  return piece;
}
function register(ref, target, pieces) {
  evidenceMap.set(ref, { target, pieces });
}
function renderTrajectory() {
  evidenceMap = new Map();
  evidencePieces = [];
  annotationNotes = [];
  highlightedTargets = [];
  const target = $("trace-content");
  target.replaceChildren();
  let index = 0;
  function shell(role, title, detail) {
    const node = element(
      "article",
      undefined,
      "message-item " + role + " evidence-target",
    );
    node.id = "trace-" + index++;
    const head = element("div", undefined, "message-header");
    head.append(element("b", title, "message-role"));
    if (detail) head.append(element("span", detail, "message-detail"));
    node._traceHeader = head;
    node.append(head);
    target.append(node);
    return node;
  }
  function folded(parent, label, value, observableTarget = null) {
    const text = pretty(value),
      details = element("details", undefined, "trace-fold");
    const summary = element("summary");
    summary.append(
      element("span", label),
      element(
        "span",
        t("characters", { count: [...text].length }),
        "character-count",
      ),
    );
    details.append(summary);
    parent.append(details);
    if (observableTarget)
      return addPiece(details, text, "trace-json", observableTarget);
    details.append(element("pre", text, "trace-json"));
    return null;
  }
  function reasoning(parent, ref, kind) {
    if (ref !== "response" && !["message", "tool_call"].includes(kind)) return;
    const text = object(metadata.display_reasoning)[ref];
    if (typeof text !== "string" || !text.trim()) return;
    const details = element(
      "details",
      undefined,
      "trace-fold recorded-reasoning",
    );
    const summary = element("summary");
    summary.append(
      element("span", t("recorded_reasoning")),
      element(
        "span",
        t("characters", { count: [...text].length }),
        "character-count",
      ),
    );
    details.append(
      summary,
      note(t("reasoning_display_only")),
      element("pre", text, "trace-json"),
    );
    parent.append(details);
  }
  const harness = object(caseData.harness),
    context = caseData.context;
  if (
    Object.values(harness).some((value) =>
      typeof value === "string"
        ? value.length
        : Array.isArray(value)
          ? value.length
          : Object.keys(object(value)).length,
    ) ||
    context
  ) {
    const node = shell("context", t("model_context"));
    if (Object.keys(harness).length) folded(node, t("system"), harness);
    if (context) folded(node, t("context"), context);
  }
  const historyPieces = [];
  let historyTarget;
  for (const message of list(caseData.history).map(object)) {
    const role = ["system", "user", "assistant", "tool"].includes(message.role)
      ? message.role
      : "message";
    const node = shell(role, t(role));
    historyTarget ||= node;
    const piece =
      role === "system" || role === "tool"
        ? folded(
            node,
            t(role === "system" ? "system" : "tool_result"),
            message.content || "",
            node,
          )
        : addPiece(node, message.content || "");
    historyPieces.push(piece);
  }
  if (historyTarget) register("history", historyTarget, historyPieces);
  const request = shell("user", t("user"));
  const query = displayQuery(record),
    display = object(metadata).display_query;
  if (typeof display === "string" && display.trim()) {
    request.append(element("div", query, "message-body"));
    const piece = folded(
      request,
      t("full_input"),
      caseData.request || "",
      request,
    );
    register("request", request, [piece]);
  } else
    register("request", request, [
      addPiece(request, caseData.request || t("no_request")),
    ]);
  for (const item of list(caseData.events).map(object)) {
    const kind = [
      "message",
      "tool_call",
      "tool_result",
      "context",
      "verification",
    ].includes(item.kind)
      ? item.kind
      : "event";
    const role =
      kind === "message" || kind === "tool_call"
        ? "assistant"
        : kind === "tool_result"
          ? "tool"
          : "context";
    const title = t(role === "context" ? kind : role),
      detail =
        kind === "tool_call" || kind === "tool_result"
          ? [t(kind), item.tool].filter(Boolean).join(" · ")
          : undefined;
    const node = shell(role, title, detail);
    reasoning(node, item.ref, kind);
    const pieces = [];
    if (item.content)
      pieces.push(
        ["context", "tool_result", "verification"].includes(kind)
          ? folded(node, t(kind), item.content, node)
          : addPiece(node, item.content),
      );
    if (kind === "tool_call")
      pieces.push(
        folded(node, t("tool_arguments"), item.arguments || {}, node),
      );
    if (item.output !== null && item.output !== undefined)
      pieces.push(
        folded(
          node,
          t(kind === "verification" ? "verification" : "tool_result"),
          item.output,
          node,
        ),
      );
    if (item.error)
      pieces.push(folded(node, t("status_error"), item.error, node));
    if (item.visible_context)
      folded(node, t("model_context"), item.visible_context);
    if (item.harness) folded(node, t("model_context"), item.harness);
    if (typeof item.ref === "string") register(item.ref, node, pieces);
  }
  const response = shell("assistant", t("assistant"), t("response"));
  reasoning(response, "response", "message");
  const reply = typeof caseData.response === "string" ? caseData.response : "";
  register("response", response, [addPiece(response, reply)]);
  if (!reply) response.append(note(t("empty_response")));
  for (const item of list(caseData.artifacts).map(object)) {
    const node = shell("artifact", t("artifact"), item.name);
    register(item.ref, node, [
      folded(node, t("artifact"), item.content || "", node),
    ]);
  }
}

function skipped() {
  return (
    object(record.composition).path === "gate" ||
    object(record.gate).status === "routed"
  );
}
function stageSkipped(stage) {
  if (skipped()) return ["base", "deduction", "bonus"].includes(stage);
  if (stage === "base") return object(record.gate).status === "invalid";
  if (stage === "deduction")
    return (
      groups.length > 0 && groups.every((group) => group.status === "not_run")
    );
  if (stage === "bonus")
    return object(record.bonus_details).status === "not_run";
  return false;
}
function selectStage(stage, interactive = false) {
  if (!record) return;
  activeStage = stage;
  $("score-details").scrollTop = 0;
  for (const name of stages) {
    $("stage-" + name).hidden = name !== stage;
    const node = $("flow-" + name);
    node.className = node._flowClass + (name === stage ? " active" : "");
    node.setAttribute("aria-pressed", name === stage);
  }
  if (interactive) {
    clearAnnotations();
    if (stage === "deduction" && !stageSkipped(stage))
      selectGroup(selectedGroup, true);
    if (stage === "base" && !stageSkipped(stage))
      selectSubject(selectedSubject, true);
    if (stage === "gate") {
      const check =
        list(object(record.gate).checks).find(
          (c) => c.name === object(record.gate).matched_rule,
        ) ||
        list(object(record.gate).checks).find(
          (c) => list(c.annotations).length,
        );
      if (check) annotate(check, automaticJump());
    }
    if (stage === "bonus") {
      const check = list(object(record.bonus_details).checks).find(
        (c) => list(c.annotations).length || list(c.evidence_refs).length,
      );
      if (check) annotate(check, automaticJump());
    }
  }
}
function renderFlow() {
  const gate = object(record.gate),
    details = object(record.bonus_details),
    routed = skipped();
  const gateText = !record.gate
    ? t("not_recorded")
    : gate.status === "routed"
      ? t("gate_hit")
      : gate.status === "invalid"
        ? t("unavailable")
        : !gate.enabled
          ? t("off")
          : t("gate_passed");
  const terms = {
    gate: gateText,
    base: stageSkipped("base") ? t("not_run") : fmt(record.base),
    deduction: stageSkipped("deduction") ? t("not_run") : fmt(record.deduction),
    bonus:
      routed || details.status === "not_run"
        ? t("not_run")
        : !record.bonus_details
          ? t("not_recorded")
          : fmt(record.bonus),
    reward: fmt(record.status === "ok" ? record.reward : null),
  };
  for (const stage of stages) {
    const node = $("flow-" + stage);
    node.disabled = false;
    node.replaceChildren(
      element(
        "span",
        t(
          stage === "deduction"
            ? "deduct"
            : stage === "reward"
              ? "final_reward"
              : stage,
        ),
        "flow-name",
      ),
      element("span", terms[stage], "flow-value"),
    );
    const textValue =
      stage === "gate" ||
      stageSkipped(stage) ||
      (stage === "bonus" &&
        (!record.bonus_details || details.status === "not_run"));
    node._flowClass =
      (stage === "reward" ? "final " : "") +
      (textValue ? "text-value " : "") +
      (stageSkipped(stage) ? "skipped " : "") +
      (stage === "gate" && routed ? "routed" : "");
  }
  $("flow-diagram").className = "flow-diagram" + (routed ? " gate-route" : "");
  const summary = $("score-summary");
  summary.replaceChildren();
  if (routed)
    summary.append(
      note(t("gate_route_note", { rule: gate.matched_rule || "—" })),
    );
  else {
    const blocked = subjects.filter((s) => list(s.blocked_by).length).length;
    if (subjects.length)
      summary.append(
        element(
          "p",
          t("blocked_count", { count: blocked, total: subjects.length }),
        ),
      );
    const failures = criteria.filter(
      (c) =>
        c.status === "ok" &&
        observed(c.value) !== null &&
        observed(c.value) < 1,
    ).length;
    summary.append(
      element(
        "p",
        failures ? t("violation_count", { count: failures }) : t("no_losses"),
      ),
    );
  }
  if (record.status !== "ok") summary.append(note(t("not_training")));
}
function labelledRow(label, content) {
  const row = element("div", undefined, "detail-row");
  row.append(
    element("span", label, "detail-label"),
    typeof content === "string" ? element("p", content) : content,
  );
  return row;
}
function renderChecks(target, checks, kind) {
  for (const check of checks) {
    const box = element("div", undefined, "criterion-item");
    box.append(
      element(
        "div",
        check.description || check.name || t("policy"),
        "criterion-name",
      ),
    );
    const head = element("div", undefined, "criterion-top");
    head.append(
      badge(
        check.status || "neutral",
        check.status === "ok"
          ? t("evaluated")
          : check.status
            ? t("status_" + check.status)
            : t("not_recorded"),
      ),
    );
    if (kind === "bonus")
      head.append(element("span", t("granted") + " " + fmt(check.granted)));
    else if (typeof check.value === "boolean")
      head.append(element("span", t("hit") + ": " + truth(check.value)));
    if (kind === "gate" && finite(check.configured_reward))
      box.append(
        note(
          t("priority") +
            ": " +
            (check.priority ?? "—") +
            " · " +
            t("configured_reward") +
            ": " +
            fmt(check.configured_reward),
        ),
      );
    box.append(
      head,
      element("p", check.reason || t("no_reason")),
      references(check),
    );
    const details = element("details", undefined, "calculation");
    details.append(
      element("summary", t("policy_record")),
      element("pre", pretty(check), "trace-json"),
    );
    box.append(details);
    target.append(box);
  }
}
function renderGate() {
  const target = $("gate-detail");
  target.replaceChildren();
  if (!record.gate) {
    target.append(note(t("not_recorded_note")));
    return;
  }
  const gate = object(record.gate);
  target.append(labelledRow(t("outcome"), t("status_" + gate.status)));
  if (!gate.enabled) target.append(note(t("gate_no_policy")));
  if (gate.matched_rule)
    target.append(
      labelledRow(t("policy"), String(gate.matched_rule)),
      math(t("final_reward") + " = " + t("gate") + " = " + fmt(gate.reward)),
    );
  if (gate.status === "routed") target.append(note(t("stage_skipped")));
  renderChecks(target, list(gate.checks), "gate");
}
function renderBonus() {
  const target = $("bonus-detail");
  target.replaceChildren();
  if (!record.bonus_details) {
    target.append(note(t("not_recorded_note")));
    return;
  }
  const detail = object(record.bonus_details);
  if (skipped() || detail.status === "not_run") {
    target.append(note(t("stage_skipped")));
    return;
  }
  target.append(labelledRow(t("outcome"), t("status_" + detail.status)));
  if (detail.status === "disabled") target.append(note(t("bonus_no_policy")));
  target.append(
    math(
      t("bonus") +
        " = min(" +
        fmt(detail.cap) +
        ", " +
        fmt(detail.uncapped) +
        ") = " +
        fmt(record.bonus),
    ),
  );
  renderChecks(target, list(detail.checks), "bonus");
}
function renderBase() {
  $("subject-list").replaceChildren();
  $("base-equations").replaceChildren();
  $("lookup-head").replaceChildren();
  $("lookup-rows").replaceChildren();
  if (stageSkipped("base")) {
    $("subject-detail").replaceChildren(note(t("stage_skipped")));
    return;
  }
  if (!subjects.length) {
    $("subject-detail").replaceChildren(note(t("no_subjects")));
    if (record.conversation) {
      const j = object(record.conversation),
        box = element("div", undefined, "detail-row");
      box.append(
        element("span", t("conversation"), "detail-label"),
        judgementFlag(j),
        element("p", j.reason || t("no_reason")),
        references(j),
      );
      $("subject-detail").append(
        box,
        math(t("base") + " = " + fmt(record.base)),
      );
    }
  }
  for (const subject of subjects) {
    const button = element("button", undefined, "select-item");
    button.type = "button";
    button._subjectIndex = subject.index;
    button.append(
      element(
        "span",
        position("S", subject.index) + " " + (subject.subject || ""),
        "item-name",
      ),
      element(
        "span",
        fmt(subject.raw_score, 3) + " → " + fmt(subject.final_score, 3),
        "item-score",
      ),
    );
    button.title = subject.subject || "";
    button.addEventListener("click", () => selectSubject(subject.index, true));
    $("subject-list").append(button);
  }
  if (subjects.length) selectSubject(selectedSubject, false);
  const equations = $("base-equations");
  equations.append(note(t("position_note")));
  for (const task of tasks) {
    for (const hop of list(task.hops)) {
      const scores = list(hop.subjects).map(
        (i) =>
          position("S", i) + ":" + fmt(object(subjectMap.get(i)).final_score),
      );
      equations.append(
        math(
          position("T", task.index) +
            "/" +
            position("H", hop.index) +
            " = (" +
            scores.join(" + ") +
            ") / " +
            (hop.denominator ?? "—") +
            " = " +
            fmt(hop.score),
        ),
      );
    }
    equations.append(
      math(
        position("T", task.index) +
          " = (" +
          list(task.hops)
            .map((hop) => fmt(hop.score))
            .join(" + ") +
          ") / " +
          list(task.hops).length +
          " = " +
          fmt(task.score),
      ),
    );
  }
  if (tasks.length)
    equations.append(
      math(
        t("base") +
          " = (" +
          tasks.map((task) => fmt(task.score)).join(" + ") +
          ") / " +
          tasks.length +
          " = " +
          fmt(record.base),
      ),
    );
  equations.append(
    note(t("subject_definition")),
    note(t("task_definition")),
    note(t("hop_definition")),
  );
  const head = element("tr");
  for (const key of [
    "row",
    "process",
    "effective_result",
    "explanation",
    "profile_score",
  ])
    head.append(element("th", t(key)));
  $("lookup-head").append(head);
  const rows = [
    [true, true, true],
    [true, true, false],
    [true, false, true],
    [true, false, false],
    [false, false, true],
    [false, false, false],
  ];
  rows.forEach(([process, result, explanation], index) => {
    const row = element("tr");
    row._tableRow = index + 1;
    for (const value of [
      index + 1,
      truth(process),
      truth(result),
      truth(explanation),
      fmt(list(profile.table)[index]),
    ])
      row.append(element("td", value));
    $("lookup-rows").append(row);
  });
  for (const row of $("lookup-rows").children)
    row.className =
      row._tableRow === object(subjectMap.get(selectedSubject)).row
        ? "active-row"
        : "";
  if (!list(profile.table).length) equations.append(note(t("no_profile")));
  equations.append(note(t("lookup_note")));
}
function selectSubject(index, interactive = false) {
  const subject = subjectMap.get(index) || subjects[0];
  if (!subject) return;
  selectedSubject = subject.index;
  for (const button of $("subject-list").children) {
    const active = button._subjectIndex === selectedSubject;
    button.className = "select-item" + (active ? " active" : "");
    button.setAttribute("aria-pressed", active);
  }
  const target = $("subject-detail");
  target.replaceChildren();
  target.append(
    element(
      "h4",
      position("S", subject.index) + " " + (subject.subject || ""),
      "detail-heading",
    ),
  );
  target.append(
    note(position("T", subject.task) + " / " + position("H", subject.hop)),
    math(
      t("raw_to_final") +
        ": " +
        fmt(subject.raw_score) +
        " → " +
        fmt(subject.final_score),
    ),
  );
  const branch =
    subject.row !== null && subject.row !== undefined
      ? t("row") + " " + subject.row
      : object(subject.judgements).clarification
        ? t("special_branch")
        : object(object(subject.judgements).understanding).value === false
          ? t("understanding_failed")
          : t("unavailable");
  target.append(
    note(
      t("table_row") +
        ": " +
        branch +
        " · " +
        t("own_eligibility") +
        ": " +
        truth(subject.row1) +
        " · " +
        t("final_eligibility") +
        ": " +
        truth(subject.final_row1),
    ),
  );
  if (list(subject.depends_on).length) {
    const row = element("div", undefined, "detail-row");
    row.append(element("span", t("direct_dependency"), "detail-label"));
    for (const dependency of list(subject.depends_on)) {
      const upstream = object(subjectMap.get(dependency)),
        blocked = list(subject.blocked_by).includes(dependency);
      const button = element(
        "button",
        position("S", dependency) +
          " " +
          (upstream.subject || "") +
          " · " +
          t(blocked ? "blocked_by" : "retained"),
        "dependency-link" + (blocked ? " blocked" : ""),
      );
      button.type = "button";
      button.addEventListener("click", () => selectSubject(dependency, true));
      row.append(button);
    }
    target.append(row);
  }
  for (const [name, value] of Object.entries(object(subject.judgements))) {
    const j = object(value),
      row = element("div", undefined, "detail-row");
    row.append(
      element("span", strings[name] || name, "detail-label"),
      judgementFlag(j),
      element("p", j.reason || t("no_reason")),
      references(j),
    );
    target.append(row);
  }
  if (list(subject.constraints).length)
    target.append(
      labelledRow(
        t("constraints"),
        element("pre", pretty(subject.constraints), "trace-json"),
      ),
    );
  for (const row of $("lookup-rows").children)
    row.className = row._tableRow === subject.row ? "active-row" : "";
  if (interactive) {
    const judgements = Object.values(object(subject.judgements));
    const focus =
      judgements.find(
        (j) =>
          j.status === "ok" &&
          observed(j.value) < 1 &&
          list(j.annotations).length,
      ) ||
      judgements.find(
        (j) =>
          j.status === "ok" &&
          observed(j.value) < 1 &&
          list(j.evidence_refs).length,
      );
    if (focus) annotate(focus, automaticJump());
    else clearAnnotations();
  }
}
function criterionCost(item) {
  const value = observed(item.value);
  return item.status === "ok" && finite(item.weight) && value !== null
    ? item.weight * (1 - value)
    : null;
}
function availableGroups() {
  const names = [
    ...new Set([
      ...groups.map((g) => g.name),
      ...criteria.map((c) => c.group),
      ...Object.keys(object(profile.group_weights)),
    ]),
  ].filter((name) => typeof name === "string");
  return names.map((name) => {
    const scored = groups.find((g) => g.name === name);
    if (scored) return scored;
    const members = criteria.filter((c) => c.group === name);
    return {
      name,
      weight: object(profile.group_weights)[name] ?? null,
      cost: null,
      score: null,
      status:
        members.length && members.every((c) => c.status === "not_applicable")
          ? "not_applicable"
          : "not_recorded",
      unscored: true,
    };
  });
}
function filteredCriteria() {
  const query = $("search").value.trim().toLowerCase(),
    state = $("status-filter").value;
  return criteria.filter(
    (c) =>
      (!selectedGroup ||
        selectedGroup === ALL_GROUPS ||
        c.group === selectedGroup) &&
      (!query ||
        [c.name, c.reason, ...list(c.evidence_refs)]
          .join(" ")
          .toLowerCase()
          .includes(query)) &&
      (state === "all" || state === "" || state === "failed"
        ? state !== "failed" || criterionCost(c) > 0
        : c.status === state),
  );
}
function renderDeduction() {
  $("quality-equations").replaceChildren();
  $("group-list").replaceChildren();
  $("curve").replaceChildren();
  $("group-equations").replaceChildren();
  if (stageSkipped("deduction")) {
    $("group-detail").replaceChildren(note(t("stage_skipped")));
    $("criterion-list").replaceChildren();
    $("group-aggregation").replaceChildren();
    return;
  }

  const options = availableGroups();
  const allButton = element("button", t("all_groups"));
  allButton.type = "button";
  allButton._groupName = ALL_GROUPS;
  allButton.addEventListener("click", () => selectGroup(ALL_GROUPS, true));
  $("group-list").append(allButton);
  for (const group of options) {
    const button = element(
      "button",
      groupName(group.name) +
        " · " +
        t("group_score") +
        " " +
        (group.unscored
          ? t(
              group.status === "not_applicable"
                ? "status_not_applicable"
                : "not_recorded",
            )
          : fmt(group.score)),
    );
    button.type = "button";
    button._groupName = group.name;
    button.addEventListener("click", () => selectGroup(group.name, true));
    $("group-list").append(button);
  }
  if (options.length || selectedGroup === ALL_GROUPS)
    selectGroup(selectedGroup, false);
  else {
    $("group-detail").replaceChildren(note(t("no_groups")));
    renderCriteria();
  }
  renderAggregation();
}
function weightedQualityEquation() {
  const active = groups.filter(activeGroup);
  return active.length
    ? math(
        t("weighted_quality") +
          " = (" +
          active.map((g) => fmt(g.weight) + "×" + fmt(g.score)).join(" + ") +
          ") / (" +
          active.map((g) => fmt(g.weight)).join(" + ") +
          ") = " +
          fmt(record.quality),
      )
    : groups.length
      ? math(t("weighted_quality") + " = " + fmt(record.quality))
      : note(t("no_active_default") + " " + fmt(record.quality));
}
function finishGroupSelection(interactive) {
  renderCriteria();
  if (!interactive) return;
  const losses = filteredCriteria()
    .filter((c) => criterionCost(c) > 0)
    .sort((a, b) => criterionCost(b) - criterionCost(a));
  const first =
    losses.find((c) => list(c.annotations).some((a) => a.kind === "issue")) ||
    losses.find((c) => list(c.evidence_refs).length);
  if (first) annotate(first, automaticJump());
  else clearAnnotations();
}
function selectGroup(name, interactive = false) {
  if (name === ALL_GROUPS) {
    selectedGroup = ALL_GROUPS;
    for (const button of $("group-list").children) {
      const active = button._groupName === ALL_GROUPS;
      button.className = active ? "active" : "";
      button.setAttribute("aria-pressed", active);
    }
    const target = $("group-detail");
    target.replaceChildren(
      element("h4", t("all_groups"), "detail-heading"),
      note(t("group_costs_note")),
    );
    $("group-equations").replaceChildren(
      weightedQualityEquation(),
      note(t("inactive_note")),
    );
    $("curve").replaceChildren();
    finishGroupSelection(interactive);
    return;
  }
  const options = availableGroups(),
    group =
      options.find((g) => g.name === name) ||
      [...groups].sort((a, b) => (b.cost || 0) - (a.cost || 0))[0] ||
      options[0];
  if (!group) return;
  selectedGroup = group.name;
  for (const button of $("group-list").children) {
    const active = button._groupName === selectedGroup;
    button.className = active ? "active" : "";
    button.setAttribute("aria-pressed", active);
  }
  const target = $("group-detail");
  target.replaceChildren();
  target.append(
    element("h4", groupName(group.name), "detail-heading"),
    metricRow(t("group_cost"), group.cost),
    metricRow(t("group_score"), group.score, true),
    note(t("group_cost_note")),
  );
  if (group.unscored)
    target.append(
      badge(
        "not_applicable",
        t(
          group.status === "not_applicable"
            ? "status_not_applicable"
            : "not_recorded",
        ),
      ),
      note(t("group_not_scored")),
    );
  if (group.status === "invalid") target.append(note(t("inactive_note")));
  const all = $("group-equations");
  all.replaceChildren();
  if (group.unscored) {
    all.append(note(t("group_not_scored")));
    $("curve").replaceChildren();
  } else {
    const costs = criteria
      .filter((c) => c.group === group.name && finite(criterionCost(c)))
      .map((c) => fmt(c.weight) + "×(1−" + fmt(observed(c.value)) + ")");
    all.append(
      math("C = " + (costs.join(" + ") || "—") + " = " + fmt(group.cost)),
      math("q(C) = max(0, 1 − (1 − 2^(−C/H)) / (1 − 2^(−C_max/H)))"),
      math(
        "H = " +
          fmt(profile.half_life) +
          "; C_max = " +
          fmt(profile.max_cost) +
          "; q = " +
          fmt(group.score),
      ),
    );
    all.append(weightedQualityEquation(), note(t("inactive_note")));
    renderCurve(group);
  }
  finishGroupSelection(interactive);
}
function numericText(value) {
  if (!finite(value)) return "—";
  const text = fmt(value);
  return text.includes("e")
    ? text
    : text.replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");
}
function numericCell(value, score = false) {
  const cell = element(
    "td",
    score ? fmt(value) : numericText(value),
    "numeric-cell",
  );
  if (finite(value)) cell.title = String(value);
  return cell;
}
function tableHead(table, keys) {
  const head = element("thead"),
    row = element("tr");
  for (const key of keys) row.append(element("th", t(key)));
  head.append(row);
  table.append(head);
}
function criterionCard(item) {
  const row = element("tr", undefined, "criterion-item");
  row._criterionName = item.name;
  const name = element("td");
  name.append(
    element(
      "span",
      (selectedGroup === ALL_GROUPS ? groupName(item.group) + " · " : "") +
        (item.name || "—"),
      "criterion-name",
    ),
  );
  const explanation = element("details", undefined, "criterion-reason");
  explanation.append(element("summary", t("reason")));
  if (item.description)
    explanation.append(element("p", item.description, "criterion-description"));
  explanation.append(element("p", item.reason || t("no_reason")));
  name.append(explanation);
  const state = element("td"),
    value = observed(item.value),
    cost = criterionCost(item);
  state.append(
    badge(
      item.status === "ok"
        ? value === null
          ? "neutral"
          : value < 1
            ? "failed"
            : "ok"
        : item.status || "neutral",
      item.status === "ok"
        ? value === null
          ? t("unavailable")
          : value < 1
            ? t("violation")
            : t("pass")
        : t("status_" + item.status),
    ),
  );
  const score = numericCell(item.status === "ok" ? value : null),
    weight = numericCell(item.weight),
    costCell = numericCell(cost),
    evidence = element("td");
  if (item.status === "not_applicable")
    costCell.append(element("small", t("excluded"), "cell-note"));
  evidence.append(references(item));
  row.append(name, state, score, weight, costCell, evidence);
  return row;
}
function renderCriteria() {
  if (!record) return;
  const target = $("criterion-list");
  target.replaceChildren();
  const members = criteria.filter(
      (item) =>
        !selectedGroup ||
        selectedGroup === ALL_GROUPS ||
        item.group === selectedGroup,
    ),
    shown = filteredCriteria();
  $("check-count").textContent = t("check_count", {
    count: shown.length,
    total: members.length,
  });
  if (!shown.length) {
    target.append(note(t("no_checks")));
    return;
  }
  const viewport = element(
      "div",
      undefined,
      "table-viewport criteria-table-viewport",
    ),
    table = element("table", undefined, "criteria-table"),
    body = element("tbody");
  tableHead(table, [
    "criterion",
    "status_heading",
    "criterion_score",
    "criterion_weight_heading",
    "criterion_cost",
    "evidence_heading",
  ]);
  for (const item of shown) body.append(criterionCard(item));
  table.append(body);
  viewport.append(table);
  target.append(viewport, note(t("criterion_cost_note")));
}
function activeGroup(group) {
  return (
    !group.unscored &&
    (group.status == null || group.status === "ok") &&
    finite(group.weight) &&
    finite(group.score)
  );
}
function sumText(values) {
  if (!values.length) return "0";
  const scale = Math.max(...values.map(Math.abs));
  if (!scale) return "0";
  const normalized = values.reduce((total, value) => total + value / scale, 0),
    sum = scale * normalized;
  return finite(sum)
    ? numericText(sum)
    : numericText(normalized) + " × " + numericText(scale);
}
function metricRow(label, value, score = false) {
  const row = element("div", undefined, "metric-row");
  row.append(
    element("span", label),
    element(
      "strong",
      typeof value === "string"
        ? value
        : score
          ? fmt(value)
          : numericText(value),
    ),
  );
  return row;
}
function renderAggregation() {
  const target = $("group-aggregation");
  target.replaceChildren(
    element("h4", t("group_aggregation"), "detail-heading"),
  );
  const viewport = element("div", undefined, "table-viewport"),
    table = element("table", undefined, "aggregation-table"),
    body = element("tbody"),
    options = availableGroups();
  tableHead(table, [
    "group",
    "group_cost",
    "group_score",
    "group_aggregation_weight",
    "weighted_contribution",
  ]);
  for (const group of options) {
    const row = element("tr"),
      name = element("td", groupName(group.name));
    if (!activeGroup(group))
      name.append(
        element(
          "small",
          group.status === "not_applicable" ? t("excluded") : t("unavailable"),
          "cell-note",
        ),
      );
    row.append(
      name,
      numericCell(group.cost),
      numericCell(group.score, true),
      numericCell(group.weight),
      numericCell(activeGroup(group) ? group.weight * group.score : null),
    );
    body.append(row);
  }
  table.append(body);
  viewport.append(table);
  target.append(viewport, note(t("inactive_note")));
  const active = groups.filter(activeGroup),
    summary = $("quality-equations");
  summary.replaceChildren(
    element("h4", t("deduction_summary"), "detail-heading"),
  );
  if (active.length)
    summary.append(
      metricRow(
        t("contribution_sum"),
        sumText(active.map((group) => group.weight * group.score)),
      ),
      metricRow(
        t("active_weight_sum"),
        sumText(active.map((group) => group.weight)),
      ),
    );
  else if (finite(record.quality))
    summary.append(
      note(t("no_active_default") + " " + numericText(record.quality)),
    );
  summary.append(
    metricRow(t("overall_quality"), record.quality, true),
    note(
      t(
        finite(record.quality)
          ? "quality_average_note"
          : "quality_unresolved_note",
      ),
    ),
    metricRow(t("deduction_max"), profile.deduction_max),
    metricRow(t("deduction"), record.deduction),
    note(
      t("deduction_arithmetic", {
        max: numericText(profile.deduction_max),
        quality: numericText(record.quality),
        deduction: numericText(record.deduction),
      }),
    ),
  );
}
function renderCurve(selected) {
  const target = $("curve");
  target.replaceChildren();
  const H = profile.half_life,
    Cmax = profile.max_cost;
  if (!finite(H) || H <= 0 || !finite(Cmax) || Cmax <= 0) {
    target.append(note(t("no_profile")));
    return;
  }
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 320 170");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", t("curve_title"));
  function shape(tag, attributes) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attributes))
      node.setAttribute(name, value);
    svg.append(node);
    return node;
  }
  function label(text, x, y, anchor = "start") {
    const node = shape("text", {
      x,
      y,
      "text-anchor": anchor,
      fill: "#778299",
      "font-size": 10,
    });
    node.textContent = text;
  }
  const xmax = Math.max(Cmax, finite(selected.cost) ? selected.cost : 0),
    x = (cost) => 28 + 266 * (cost / xmax),
    y = (score) => 135 - 108 * Math.max(0, Math.min(1, score));
  shape("line", { x1: 28, y1: 27, x2: 28, y2: 135, stroke: "#d7e0eb" });
  shape("line", { x1: 28, y1: 135, x2: 294, y2: 135, stroke: "#d7e0eb" });
  const denominator = -Math.expm1(-Math.LN2 * (Cmax / H));
  const points = [];
  for (let i = 0; i <= 80; i++) {
    const cost = xmax * (i / 80);
    const q = denominator
      ? Math.max(0, 1 + Math.expm1(-Math.LN2 * (cost / H)) / denominator)
      : Math.max(0, 1 - cost / Cmax);
    points.push(x(cost) + "," + y(q));
  }
  shape("polyline", {
    points: points.join(" "),
    fill: "none",
    stroke: "#6d8cbf",
    "stroke-width": 2,
  });
  label("1", 14, 31);
  label("0", 14, 139);
  label("0", 28, 151);
  label(fmt(xmax, 2), 294, 151, "end");
  label(t("curve_axis"), 160, 167, "middle");
  if (finite(selected.cost) && finite(selected.score)) {
    const px = x(selected.cost),
      py = y(selected.score);
    shape("line", {
      x1: px,
      y1: py,
      x2: px,
      y2: 135,
      stroke: "#a8b6ca",
      "stroke-dasharray": "3 3",
    });
    shape("circle", {
      cx: px,
      cy: py,
      r: 4,
      fill: selected.cost > 0 ? "#b94b48" : "#247a67",
    });
    label(
      groupName(selected.name) + " " + fmt(selected.score, 3),
      Math.min(285, Math.max(45, px)),
      Math.max(16, py - 10),
      "end",
    );
  }
  target.append(svg);
}
function renderReward() {
  const target = $("reward-detail");
  target.replaceChildren();
  const composition = object(record.composition),
    bounds = object(composition.bounds);
  if (skipped()) {
    target.append(
      math(
        t("final_reward") +
          " = " +
          t("gate") +
          " = " +
          fmt(object(record.gate).reward),
      ),
      note(
        t("gate_route_note", { rule: object(record.gate).matched_rule || "—" }),
      ),
    );
  } else if (record.composition) {
    target.append(
      math(
        t("base") +
          " − " +
          t("deduct") +
          " + " +
          t("bonus") +
          "\n" +
          fmt(record.base) +
          " − " +
          fmt(record.deduction) +
          " + " +
          fmt(record.bonus) +
          " = " +
          fmt(composition.before_clip),
      ),
      math(
        "clip[" +
          fmt(bounds.min) +
          ", " +
          fmt(bounds.max) +
          "](" +
          fmt(composition.before_clip) +
          ") = " +
          fmt(record.status === "ok" ? record.reward : null),
      ),
    );
  } else {
    target.append(
      math(
        t("base") +
          " = " +
          fmt(record.base) +
          "\n" +
          t("deduct") +
          " = " +
          fmt(record.deduction),
      ),
      note(t("not_recorded_note")),
    );
    if (finite(profile.reward_min) && finite(profile.reward_max))
      target.append(
        note(
          t("bounds") +
            " [" +
            fmt(profile.reward_min) +
            ", " +
            fmt(profile.reward_max) +
            "]",
        ),
      );
  }
  target.append(
    element(
      "div",
      fmt(record.status === "ok" ? record.reward : null),
      "formula-result",
    ),
    badge(
      record.status,
      t(record.status === "ok" ? "valid_scalar" : "status_invalid"),
    ),
  );
  if (record.status !== "ok") target.append(note(t("not_training")));
  if (list(record.errors).length) {
    target.append(element("h4", t("issues"), "detail-heading"));
    for (const error of record.errors) target.append(note(pretty(error)));
  }
  const losses = criteria
    .filter((c) => criterionCost(c) > 0)
    .sort((a, b) => criterionCost(b) - criterionCost(a))
    .slice(0, 4);
  if (losses.length) {
    target.append(element("h4", t("largest_deductions"), "detail-heading"));
    for (const loss of losses) {
      const button = element("button", undefined, "loss-link");
      button.type = "button";
      button.append(
        element("span", loss.name),
        element("span", t("cost") + " " + fmt(criterionCost(loss), 3)),
      );
      button.addEventListener("click", () => {
        selectStage("deduction");
        selectGroup(loss.group);
        annotate(loss, automaticJump());
      });
      target.append(button);
    }
  }
}
let mobilePane = "score";
function setMobilePane(pane) {
  mobilePane = pane;
  $("workbench").className =
    "workbench " + (pane === "score" ? "show-score" : "show-trace");
  for (const name of ["trace", "score"])
    $("mobile-" + name).setAttribute("aria-selected", pane === name);
}
$("mobile-trace").addEventListener("click", () => setMobilePane("trace"));
$("mobile-score").addEventListener("click", () => setMobilePane("score"));
function selectRecord(index, { preserveState = false } = {}) {
  if (!records[index]) {
    selectedRecord = null;
    renderEmptyRecord();
    renderQueryList();
    return;
  }
  const previousStage = activeStage,
    previousPane = mobilePane;
  selectedRecord = index;
  record = object(records[index]);
  caseData = object(record.case);
  metadata = object(record.metadata);
  profile = object(metadata.profile);
  subjects = list(record.subjects);
  tasks = list(record.tasks);
  criteria = list(record.criteria);
  groups = list(record.groups);
  subjectMap = new Map(subjects.map((s) => [s.index, s]));
  if (!preserveState) {
    selectedSubject = subjects[0]?.index ?? null;
    selectedGroup = null;
    $("search").value = "";
    $("status-filter").value = "all";
  }
  $("search").disabled = false;
  $("status-filter").disabled = false;
  $("export-record").disabled = false;
  $("record-status").className = "status " + record.status;
  $("record-status").textContent = t("status_" + record.status);
  $("request-title").textContent = displayQuery(record);
  $("request-title").title = displayQuery(record);
  $("run-line").textContent = [
    metadata.pack_name
      ? t("pack") +
        ": " +
        metadata.pack_name +
        " " +
        (metadata.pack_version || "")
      : "",
    metadata.model ? t("model") + ": " + metadata.model : "",
  ]
    .filter(Boolean)
    .join(" · ");
  $("metadata-content").textContent = pretty(metadata);
  renderQueryList();
  renderTrajectory();
  renderFlow();
  renderGate();
  renderBase();
  renderDeduction();
  renderBonus();
  renderReward();
  selectStage(preserveState ? previousStage : "reward");
  setMobilePane(preserveState ? previousPane : "score");
  if (!preserveState) {
    $("trace-content").scrollTop = 0;
    $("score-details").scrollTop = 0;
  }
}
function renderEmptyRecord() {
  clearAnnotations();
  record = null;
  caseData = {};
  metadata = {};
  profile = {};
  subjects = [];
  tasks = [];
  criteria = [];
  groups = [];
  subjectMap = new Map();
  selectedSubject = null;
  selectedGroup = null;
  evidenceMap = new Map();
  evidencePieces = [];
  $("request-title").textContent = t("no_selection");
  $("request-title").title = "";
  $("record-status").textContent = "";
  $("record-status").className = "status neutral";
  $("run-line").textContent = "";
  $("metadata-content").textContent = "";
  $("trace-content").replaceChildren(note(t("no_selection")));
  $("flow-diagram").className = "flow-diagram";
  for (const stage of stages) {
    const node = $("flow-" + stage);
    node.disabled = true;
    node._flowClass = "skipped text-value";
    node.className = node._flowClass;
    node.setAttribute("aria-pressed", false);
    node.replaceChildren(
      element(
        "span",
        t(
          stage === "deduction"
            ? "deduct"
            : stage === "reward"
              ? "final_reward"
              : stage,
        ),
        "flow-name",
      ),
      element("span", "—", "flow-value"),
    );
    $("stage-" + stage).hidden = stage !== "reward";
  }
  for (const id of [
    "gate-detail",
    "subject-list",
    "subject-detail",
    "base-equations",
    "lookup-head",
    "lookup-rows",
    "quality-equations",
    "group-list",
    "group-detail",
    "curve",
    "group-equations",
    "group-aggregation",
    "criterion-list",
    "bonus-detail",
  ])
    $(id).replaceChildren();
  $("check-count").textContent = "";
  $("score-summary").replaceChildren();
  $("reward-detail").replaceChildren(note(t("no_selection")));
  $("search").disabled = true;
  $("status-filter").disabled = true;
  $("export-record").disabled = true;
  activeStage = "reward";
}
function getSelectedRecord() {
  return record || null;
}
function replaceRecords(nextRecords) {
  if (!Array.isArray(nextRecords))
    throw new TypeError(
      "replaceRecords expects an array of validated evaluation records",
    );
  const previous = {
    records,
    selectedRecord,
    selectedSubject,
    selectedGroup,
    activeStage,
    mobilePane,
    query: $("query-search").value,
    search: $("search").value,
    status: $("status-filter").value,
    traceScroll: $("trace-content").scrollTop,
    scoreScroll: $("score-details").scrollTop,
  };
  try {
    records = [...nextRecords];
    selectedRecord = records.length ? 0 : null;
    selectedSubject = null;
    selectedGroup = null;
    $("query-search").value = "";
    $("search").value = "";
    $("status-filter").value = "all";
    if (records.length) selectRecord(0);
    else {
      renderEmptyRecord();
      renderQueryList();
      setMobilePane("score");
    }
  } catch (error) {
    records = previous.records;
    selectedRecord = previous.selectedRecord;
    selectedSubject = previous.selectedSubject;
    selectedGroup = previous.selectedGroup;
    activeStage = previous.activeStage;
    mobilePane = previous.mobilePane;
    $("query-search").value = previous.query;
    $("search").value = previous.search;
    $("status-filter").value = previous.status;
    if (selectedRecord != null)
      selectRecord(selectedRecord, { preserveState: true });
    else {
      renderEmptyRecord();
      renderQueryList();
      setMobilePane(previous.mobilePane);
    }
    $("trace-content").scrollTop = previous.traceScroll;
    $("score-details").scrollTop = previous.scoreScroll;
    throw error;
  }
}

replaceRecords(records);
