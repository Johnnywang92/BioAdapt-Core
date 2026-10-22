const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

// Load the actual helper in memory without building the app or writing artifacts.
const filename = path.join(__dirname, "src/lib/evidencePresentation.ts");
const { outputText } = ts.transpileModule(readFileSync(filename, "utf8"), {
  fileName: filename,
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
  },
});
const helper = new Module(filename, module);
helper.filename = filename;
helper.paths = module.paths;
helper._compile(outputText, filename);
const { evidenceMarkers, queryMarkers } = helper.exports;

function markerEvidence(content, overrides = {}) {
  return {
    evidence_id: "ev-marker",
    kind: "marker_specificity",
    role: "supporting",
    partition: "confirmation",
    source: {
      claim_id: null,
      dataset_id: "dataset-test",
      version: "1",
      run_id: "run-test",
      asset_id: null,
      doi: null,
      pmid: null,
      locator: null,
      license_class: null,
      usage_scope: null,
    },
    provenance: { source: "tool", object_id: "ev-marker", note: null },
    independence_group: null,
    used_cell_set_fingerprint: null,
    content,
    ...overrides,
  };
}

test("evidenceMarkers extracts string genes and trims whitespace", () => {
  assert.deepEqual(
    evidenceMarkers({ marker: markerEvidence({ genes: [" IL7R ", "\tCCR7\n", "LTB"] }) }),
    ["IL7R", "CCR7", "LTB"],
  );
});

test("evidenceMarkers reads service-shaped gene statistics plus qualifying genes", () => {
  // Matches the evidence service's content.genes statistics rows.
  const evidence = markerEvidence({
    excluded_population: "cluster-other",
    support_rule_version: "1",
    min_qualifying_markers: 1,
    qualifying_genes: ["IL7R", "CCR7"],
    support_rule_reasons: [],
    genes: [{
      gene: " IL7R ",
      log2fc: 2.4,
      fdr: 0.001,
      detection_in_candidate: 0.8,
      detection_in_excluded: 0.1,
      donors_tested: 4,
      donors_same_direction: 4,
    }],
  });
  assert.deepEqual(evidenceMarkers({ marker: evidence }), ["IL7R", "CCR7"]);
});

test("evidenceMarkers accepts qualifying genes without displayed gene rows", () => {
  assert.deepEqual(
    evidenceMarkers({ marker: markerEvidence({ qualifying_genes: [" CCR7 ", { gene: " LTB " }] }) }),
    ["CCR7", "LTB"],
  );
});

test("evidenceMarkers ignores cross_modal even when it contains gene fields", () => {
  const crossModal = markerEvidence(
    { genes: ["MS4A1", { gene: "CD79A" }], qualifying_genes: ["CD74"] },
    { kind: "cross_modal", evidence_id: "ev-cross-modal" },
  );
  assert.deepEqual(evidenceMarkers({ crossModal }), []);
  assert.deepEqual(
    evidenceMarkers({ crossModal, marker: markerEvidence({ genes: ["IL7R"] }) }),
    ["IL7R"],
  );
});

test("evidenceMarkers returns no example genes for empty evidence or arrays", () => {
  assert.deepEqual(evidenceMarkers({}), []);
  assert.deepEqual(
    evidenceMarkers({ marker: markerEvidence({ genes: [], qualifying_genes: [] }) }),
    [],
  );
});

test("evidenceMarkers returns no example genes for missing or non-array gene fields", () => {
  // Evidence requires content; its individual gene fields are untrusted/optional.
  const contents = [
    {},
    { markers: ["IL7R"], gene: "CCR7" },
    { genes: undefined, qualifying_genes: undefined },
    { genes: null, qualifying_genes: null },
    { genes: "IL7R", qualifying_genes: "CCR7" },
    { genes: { gene: "IL7R" }, qualifying_genes: { gene: "CCR7" } },
    { genes: 42, qualifying_genes: false },
  ];
  for (const content of contents) {
    assert.deepEqual(evidenceMarkers({ marker: markerEvidence(content) }), [], JSON.stringify(content));
  }
});

test("evidenceMarkers skips malformed and blank entries in both gene arrays", () => {
  const invalid = [null, undefined, 42, false, {}, [], { symbol: "IL7R" },
    { gene: null }, { gene: 42 }, { gene: false }, { gene: ["IL7R"] },
    "", " \t\n", { gene: "" }, { gene: " \n" }];
  assert.deepEqual(
    evidenceMarkers({ marker: markerEvidence({ genes: invalid, qualifying_genes: invalid }) }),
    [],
  );
  assert.deepEqual(
    evidenceMarkers({ marker: markerEvidence({
      genes: [...invalid, " IL7R "],
      qualifying_genes: [...invalid, { gene: " CCR7 " }],
    }) }),
    ["IL7R", "CCR7"],
  );
});

test("evidenceMarkers deduplicates in first-seen order across fields and records", () => {
  assert.deepEqual(evidenceMarkers({
    first: markerEvidence({
      genes: [" CCR7 ", { gene: "IL7R" }, "CCR7"],
      qualifying_genes: [" IL7R ", "LTB"],
    }),
    second: markerEvidence({
      genes: [{ gene: " LTB " }, "MS4A1", "CCR7"],
      qualifying_genes: ["CD79A", "MS4A1", "il7r"],
    }, { evidence_id: "ev-second" }),
  }), ["CCR7", "IL7R", "LTB", "MS4A1", "CD79A", "il7r"]);
});

test("evidenceMarkers does not apply the query's eight-marker cap", () => {
  const genes = ["IL7R", "CCR7", "LTB", "MS4A1", "CD79A", "CD74", "NKG7", "GNLY", "LYZ"];
  assert.deepEqual(evidenceMarkers({ marker: markerEvidence({ genes }) }), genes);
});

test("queryMarkers returns no example genes for empty or delimiter-only input", () => {
  for (const input of ["", " \t\r\n", ",， ,\t，\n"]) {
    assert.deepEqual(queryMarkers(input), []);
  }
});

test("queryMarkers splits commas, Chinese commas, and whitespace", () => {
  assert.deepEqual(
    queryMarkers("  IL7R, CCR7，LTB\tMS4A1\nCD79A\r\nCD74   NKG7  "),
    ["IL7R", "CCR7", "LTB", "MS4A1", "CD79A", "CD74", "NKG7"],
  );
});

test("queryMarkers preserves first-seen deduplication order and case", () => {
  assert.deepEqual(
    queryMarkers(" CCR7，IL7R,CCR7\tLTB\nIL7R，il7r,LTB "),
    ["CCR7", "IL7R", "LTB", "il7r"],
  );
});

test("queryMarkers caps the first eight distinct markers after deduplication", () => {
  assert.deepEqual(
    queryMarkers("IL7R,IL7R，CCR7 CCR7\tLTB,MS4A1,CD79A,CD74,NKG7,GNLY,LYZ,S100A8,CCR7"),
    ["IL7R", "CCR7", "LTB", "MS4A1", "CD79A", "CD74", "NKG7", "GNLY"],
  );
});
