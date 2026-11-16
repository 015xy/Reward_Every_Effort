// Local record import/export. No requests or model calls leave the browser.
const MAX_IMPORT_BYTES = 20 * 1024 * 1024;
const importDialog = $("data-dialog");
let importRevision = 0;

function rejectDuplicateKeys(source) {
  // Syntax has already been checked by JSON.parse. Quoted tokens are consumed
  // whole, so punctuation inside strings cannot alter the container stack.
  const tokens = /"(?:\\[\s\S]|[^"\\])*"|[{}\[\]]/g;
  const stack = [];
  let match;
  while ((match = tokens.exec(source))) {
    const token = match[0];
    if (token === "{") stack.push(new Set());
    else if (token === "[") stack.push(null);
    else if (token === "}" || token === "]") stack.pop();
    else {
      let next = tokens.lastIndex;
      while (/\s/.test(source[next] || "") && next < source.length) next += 1;
      if (source[next] !== ":") continue;
      const key = JSON.parse(token),
        keys = stack[stack.length - 1];
      if (keys.has(key)) throw new Error(t("import_duplicate", { key }));
      keys.add(key);
    }
  }
}

function validateImportedRecord(item, index) {
  const fail = (detail) => {
    throw new Error(t("import_invalid", { index: index + 1, detail }));
  };
  const isObject = (value) =>
    value !== null && typeof value === "object" && !Array.isArray(value);
  const objectField = (parent, name, path, nullable = false) => {
    if (!(name in parent) || (nullable && parent[name] === null)) return {};
    if (!isObject(parent[name])) fail(t("import_field_object", { name: path }));
    return parent[name];
  };
  const listField = (parent, name, path, objects = false, nullable = false) => {
    if (!(name in parent) || (nullable && parent[name] === null)) return [];
    const value = parent[name];
    if (!Array.isArray(value)) fail(t("import_list", { name: path }));
    if (objects && value.some((entry) => !isObject(entry)))
      fail(t("import_entries", { name: path }));
    return value;
  };
  const scores = (parent, names, path = "", boolean = false) => {
    for (const name of names) {
      if (
        !(name in parent) ||
        parent[name] === null ||
        (boolean && typeof parent[name] === "boolean")
      )
        continue;
      if (typeof parent[name] !== "number" || !Number.isFinite(parent[name]))
        fail(t("import_score", { name: path + name }));
    }
  };
  const judgement = (value, path) => {
    scores(value, ["value"], path + ".", true);
    listField(value, "evidence_refs", path + ".evidence_refs");
    listField(value, "annotations", path + ".annotations", true);
    if ("judge_fact" in value)
      judgement(
        objectField(value, "judge_fact", path + ".judge_fact"),
        path + ".judge_fact",
      );
  };
  const subject = (value, path) => {
    scores(value, ["raw_score", "final_score"], path + ".");
    for (const name of ["depends_on", "blocked_by"])
      listField(value, name, path + "." + name);
    listField(value, "constraints", path + ".constraints", true);
    for (const [name, detail] of Object.entries(
      objectField(value, "judgements", path + ".judgements"),
    )) {
      if (!isObject(detail))
        fail(t("import_field_object", { name: path + ".judgements." + name }));
      judgement(detail, path + ".judgements." + name);
    }
  };
  const harness = (value, path) => {
    listField(value, "tools", path + ".tools", true).forEach((tool, index) =>
      objectField(
        tool,
        "parameters",
        path + ".tools[" + index + "].parameters",
        true,
      ),
    );
    listField(value, "skills", path + ".skills", true);
    listField(value, "permissions", path + ".permissions");
    objectField(value, "compression", path + ".compression");
  };
  if (!isObject(item)) fail(t("import_object"));
  if (Object.hasOwn(item, "request")) fail(t("import_raw_case"));
  if (!["ok", "invalid"].includes(item.status)) fail(t("import_status"));
  if ((item.schema_version ?? 1) !== 1 || item.schema_version === null)
    fail(t("import_version"));
  const result = { ...item };
  for (const name of ["subjects", "tasks", "criteria", "groups", "errors"]) {
    if (!(name in result)) result[name] = [];
    listField(result, name, name, name !== "errors");
  }
  for (const name of ["case", "metadata"]) {
    if (!(name in result)) result[name] = {};
    objectField(result, name, name);
  }
  scores(result, ["reward", "base", "quality", "deduction", "bonus"]);
  result.subjects.forEach((value, index) =>
    subject(value, `subjects[${index}]`),
  );
  result.tasks.forEach((task, index) => {
    const path = `tasks[${index}]`;
    scores(task, ["score"], path + ".");
    listField(task, "hops", path + ".hops", true).forEach((hop, hopIndex) => {
      const hopPath = `${path}.hops[${hopIndex}]`;
      listField(hop, "subjects", hopPath + ".subjects");
      scores(hop, ["score"], hopPath + ".");
    });
  });
  for (const name of ["criteria", "groups"])
    result[name].forEach((value, index) => {
      scores(value, ["score", "cost", "weight"], `${name}[${index}].`);
      if (name === "criteria") judgement(value, `${name}[${index}]`);
    });
  for (const name of ["gate", "bonus_details"]) {
    const value = objectField(result, name, name);
    scores(value, ["reward", "cap", "uncapped"], name + ".");
    listField(value, "checks", name + ".checks", true).forEach(
      (check, index) => {
        const path = `${name}.checks[${index}]`;
        scores(
          check,
          ["granted", "max_value", "configured_reward"],
          path + ".",
        );
        judgement(check, path);
      },
    );
  }
  if ("conversation" in result)
    judgement(
      objectField(result, "conversation", "conversation"),
      "conversation",
    );
  const composition = objectField(result, "composition", "composition");
  scores(
    composition,
    ["before_clip", "base", "deduction", "bonus", "gate_reward"],
    "composition.",
  );
  scores(
    objectField(composition, "bounds", "composition.bounds"),
    ["min", "max"],
    "composition.bounds.",
  );
  const profile = objectField(result.metadata, "profile", "metadata.profile");
  scores(
    profile,
    [
      "half_life",
      "max_cost",
      "deduction_max",
      "bonus_max",
      "reward_min",
      "reward_max",
    ],
    "metadata.profile.",
  );
  const table = listField(profile, "table", "metadata.profile.table");
  scores(
    table,
    table.map((_value, index) => index),
    "metadata.profile.table.",
  );
  const weights = objectField(
    profile,
    "group_weights",
    "metadata.profile.group_weights",
  );
  scores(weights, Object.keys(weights), "metadata.profile.group_weights.");
  for (const name of ["gates", "bonuses"])
    listField(result.metadata, name, "metadata." + name, true).forEach(
      (rule, index) =>
        scores(
          rule,
          ["reward", "max_value", "min_base", "min_quality"],
          `metadata.${name}[${index}].`,
        ),
    );
  const caseData = result.case;
  for (const name of ["history", "events", "artifacts"])
    listField(caseData, name, "case." + name, true);
  listField(caseData, "subjects", "case.subjects", true, true).forEach(
    (value, index) => subject(value, `case.subjects[${index}]`),
  );
  harness(objectField(caseData, "harness", "case.harness"), "case.harness");
  listField(caseData, "events", "case.events", true).forEach((event, index) => {
    const path = `case.events[${index}]`;
    objectField(event, "arguments", path + ".arguments");
    harness(
      objectField(event, "harness", path + ".harness", true),
      path + ".harness",
    );
  });
  const context = objectField(caseData, "context", "case.context", true);
  for (const name of ["success_criteria", "constraints", "references"])
    listField(context, name, "case.context." + name);
  listField(context, "examples", "case.context.examples", true);
  objectField(context, "harness", "case.context.harness");
  objectField(context, "output_schema", "case.context.output_schema", true);
  return result;
}

function parseRecords(text) {
  if (new TextEncoder().encode(text).length > MAX_IMPORT_BYTES)
    throw new Error(t("import_too_large"));
  const source = text.replace(/^\uFEFF/, "").trim();
  if (!source) throw new Error(t("import_empty"));
  const revive = (_key, value) => {
    if (typeof value === "number" && !Number.isFinite(value))
      throw new Error(t("import_number"));
    return value;
  };
  const parseJson = (value) => {
    const result = JSON.parse(value, revive);
    rejectDuplicateKeys(value);
    return result;
  };
  let parsed;
  try {
    parsed = parseJson(source);
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    parsed = source.split(/\r?\n/).flatMap((line, index) => {
      if (!line.trim()) return [];
      try {
        return [parseJson(line)];
      } catch (error) {
        if (!(error instanceof SyntaxError)) throw error;
        throw new Error(t("import_syntax", { line: index + 1 }));
      }
    });
  }
  const imported = Array.isArray(parsed) ? parsed : [parsed];
  if (!imported.length) throw new Error(t("import_empty"));
  return imported.map(validateImportedRecord);
}

function importError(error) {
  $("import-error").textContent = error.message || t("import_read_error");
  $("import-error").hidden = false;
}

function installRecords(text, source) {
  const imported = parseRecords(text);
  replaceRecords(imported);
  $("data-feedback").textContent = t("import_loaded", {
    count: imported.length,
    source,
  });
  $("data-feedback").hidden = false;
  importDialog.close();
}

$("open-data").addEventListener("click", () => {
  importRevision += 1;
  $("import-error").hidden = true;
  $("data-file").value = "";
  importDialog.showModal();
});
$("close-data").addEventListener("click", () => importDialog.close());
importDialog.addEventListener("close", () => {
  importRevision += 1;
});
$("load-pasted").addEventListener("click", () => {
  importRevision += 1;
  try {
    installRecords($("data-text").value, t("pasted_data"));
  } catch (error) {
    importError(error);
  }
});
$("data-file").addEventListener("change", async () => {
  const file = $("data-file").files[0];
  if (!file) return;
  const revision = ++importRevision;
  try {
    if (file.size > MAX_IMPORT_BYTES) throw new Error(t("import_too_large"));
    const text = await file.text();
    if (revision !== importRevision || !importDialog.open) return;
    installRecords(text, file.name);
  } catch (error) {
    if (revision === importRevision) importError(error);
  }
});
$("export-record").addEventListener("click", () => {
  if (selectedRecord == null || !records[selectedRecord]) return;
  const blob = new Blob(
    [JSON.stringify(records[selectedRecord], null, 2) + "\n"],
    {
      type: "application/json",
    },
  );
  const url = URL.createObjectURL(blob);
  const link = element("a");
  link.href = url;
  link.download = `reward-record-${selectedRecord + 1}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
