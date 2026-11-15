// The results view (QV12): jobs side by side, Sampler bars or Estimator points.
import embed, { type Result as EmbedResult } from "vega-embed";
import { expressionInterpreter } from "vega-interpreter";
import { busy, button, el, fmt } from "../dom";
import { callServer, cssVar, onThemeChange, report, setAppTools, textResult, type AppTool, type Json } from "../host";
import { pictureTools, setFacts, shell } from "./common";

interface JobRow {
  job_id: string;
  label: string;
  backend: string;
  backend_kind: string;
  job_status: string;
  primitive: "sampler" | "estimator";
  circuit_qasm3: string | null;
  parameter_values: number[][] | null;
  observables: string[] | null;
  shots: number | null;
  done: boolean;
}

interface Comparison {
  primitive: "sampler" | "estimator";
  jobs: { job_id: string; label: string; backend: string }[];
  sets: Json[];
  num_sets: number;
  observables?: string[];
  classification?: { accuracy: Record<string, number | null>; flipped_from_reference: Record<string, number[]>; threshold: number };
}

interface ResultsData {
  jobs: JobRow[];
  comparison: Comparison | null;
  expected: number[] | null;
}

const MAX_BARS = 32;

export async function resultsView(request: Json): Promise<void> {
  const view = shell("Results");
  let current: Json = { ...request };
  view.message("Reading the jobs…");
  let data = (await callServer("view_data_results", current)) as unknown as ResultsData;
  view.message("");
  let chart: EmbedResult | null = null;
  const frame = el("div", { class: "frame chart" });
  const panel = el("section", { class: "panel", hidden: true });
  view.body.append(frame, panel);

  const backendSelect = el("select", { "aria-label": "Backend to run on" });
  const runOn = button("Run on this backend", () => void busy(runOn, async () => {
    const source = data.jobs.find((j) => j.done && j.circuit_qasm3) ?? data.jobs[0];
    if (!source?.circuit_qasm3) throw new Error("No job here carries its circuit, so it cannot be run again.");
    const args: Json = { circuit: source.circuit_qasm3, backend_name: backendSelect.value, label: backendSelect.value };
    if (source.parameter_values) args.parameter_values = source.parameter_values;
    let out: Json;
    view.message(`Running on ${backendSelect.value}…`);
    if (source.primitive === "estimator") {
      out = await callServer("run_estimator", { ...args, observables: source.observables ?? "Z" });
    } else {
      out = await callServer("run_sampler", { ...args, ...(source.shots ? { shots: source.shots } : {}) });
    }
    await reload({ job_ids: [...(current.job_ids as string[]), String(out.job_id)],
      labels: [...labelsNow(), backendSelect.value] });
    view.message(`Ran on ${backendSelect.value} as ${out.job_id}.`);
  }, (m) => view.message(m, true)));
  const jobSelect = el("select", { "aria-label": "Job to add" });
  const addJob = button("Add to the comparison", () => void busy(addJob, async () => {
    if (!jobSelect.value) return;
    await reload({ job_ids: [...(current.job_ids as string[]), jobSelect.value], labels: [...labelsNow(), ""] });
  }, (m) => view.message(m, true)));
  view.tools.append(backendSelect, runOn, jobSelect, addJob);

  function labelsNow(): string[] {
    return data.jobs.map((j) => j.label);
  }

  void (async () => {
    try {
      const out = await callServer("list_backends", { include_fake: true });
      const local = (out.local as { name: string }[]).map((b) => b.name);
      const fakes = (out.fake as { name: string; num_qubits: number }[]).filter((b) => b.num_qubits >= 5).map((b) => b.name);
      const real = ((out.hardware ?? []) as { name: string }[]).map((b) => b.name);
      backendSelect.replaceChildren(
        ...local.map((n) => el("option", { value: n, text: n })),
        ...fakes.map((n) => el("option", { value: n, text: `${n} (noisy simulation)` })),
        ...real.map((n) => el("option", { value: n, text: `${n} (real device, uses quantum time)` })),
      );
      backendSelect.value = String(out.default_fake_backend ?? local[0]);
      const jobs = await callServer("list_my_jobs", { limit: 25 });
      const listed = (jobs.local_jobs as { job_id: string; label?: string; backend: string; primitive: string }[]) ?? [];
      jobSelect.replaceChildren(
        el("option", { value: "", text: "Add a job…" }),
        ...listed.map((j) => el("option", { value: j.job_id, text: `${j.label || j.backend}: ${j.primitive} ${j.job_id}` })),
      );
    } catch (error) {
      view.message(String((error as Error).message), true);
    }
  })();

  async function reload(change: Json) {
    current = { ...current, ...change };
    data = (await callServer("view_data_results", current)) as unknown as ResultsData;
    await render();
  }

  async function render() {
    const cmp = data.comparison;
    const facts: [string, string | number][] = [["Jobs", data.jobs.length]];
    const pending = data.jobs.filter((j) => !j.done);
    if (pending.length) facts.push(["Not finished", pending.map((j) => `${j.label} (${j.job_status})`).join(", ")]);
    if (cmp?.primitive === "sampler") {
      const tvd = (cmp.sets[0]?.total_variation_distance ?? {}) as Record<string, number>;
      const ref = cmp.jobs[0]?.label ?? "the reference";
      for (const [id, v] of Object.entries(tvd)) facts.push([`Total variation distance of ${labelOf(id)} from ${ref}`, fmt(v, 3)]);
    }
    if (cmp?.classification) {
      for (const [id, a] of Object.entries(cmp.classification.accuracy)) facts.push([`Accuracy ${labelOf(id)}`, a === null ? "n/a" : `${Math.round(a * 100)}%`]);
    }
    setFacts(view.facts, facts);
    if (!cmp) {
      frame.replaceChildren(el("p", { class: "status", text: "No job has finished yet. Results appear when one is done." }));
      return;
    }
    const spec = cmp.primitive === "sampler" ? samplerSpec(cmp) : estimatorSpec(cmp, data.expected);
    chart?.finalize();
    frame.replaceChildren();
    // The interpreter evaluates chart expressions without eval, so the page needs no
    // unsafe-eval in its content security policy.
    chart = await embed(frame, spec as never, {
      actions: false, renderer: "svg", config: themeConfig() as never, ast: true, expr: expressionInterpreter as never,
    });
    chart.view.addEventListener("click", (_event, item) => {
      const datum = (item as { datum?: Json } | null)?.datum;
      if (datum) choose(datum);
    });
    report(
      { job_ids: current.job_ids, job: current.job_ids },
      { view: `The results view compares ${data.jobs.map((j) => `${j.label} (${j.job_id})`).join(", ")}.` },
    );
  }

  function labelOf(id: string): string {
    return data.jobs.find((j) => j.job_id === id)?.label ?? id;
  }

  function choose(datum: Json) {
    const cmp = data.comparison;
    if (!cmp) return;
    if (cmp.primitive === "sampler" && datum.bitstring !== undefined) {
      const row = (cmp.sets[0].probabilities as Json[]).find((r) => r.bitstring === datum.bitstring) ?? {};
      const per = Object.fromEntries(cmp.jobs.map((j) => [j.label, row[j.job_id] as number]));
      const text = `bitstring ${datum.bitstring}: ` + Object.entries(per).map(([k, v]) => `${k} ${fmt(v, 3)}`).join(", ");
      view.selection.replaceChildren("Selected ", el("b", { text }));
      report({ selection: { kind: "bitstring", value: datum.bitstring, probabilities: per } },
        { selection: `The person selected ${text} in the results view.` });
    } else if (datum.set !== undefined) {
      const index = Number(datum.set);
      const entry = cmp.sets[index] as { jobs: Record<string, { values: (number | null)[]; standard_errors: (number | null)[] }>; expected?: number; parameter_values?: number[] };
      const per = Object.fromEntries(cmp.jobs.map((j) => [j.label, entry.jobs[j.job_id]?.values?.[0] ?? null]));
      const flips = Object.entries(cmp.classification?.flipped_from_reference ?? {}).filter(([, f]) => f.includes(index)).map(([id]) => labelOf(id));
      const text = `parameter set ${index}: ` + Object.entries(per).map(([k, v]) => `${k} ${fmt(v, 3)}`).join(", ") +
        (entry.expected !== undefined ? `, expected class ${entry.expected}` : "") +
        (flips.length ? `, class flipped on ${flips.join(", ")}` : "");
      view.selection.replaceChildren("Selected ", el("b", { text }));
      report(
        { selection: { kind: "parameter_set", index, values: per, expected: entry.expected ?? null, parameter_values: entry.parameter_values ?? null, flipped_on: flips }, parameter_set: index },
        { selection: `The person selected ${text} in the results view.` },
      );
    }
  }

  onThemeChange(() => void render());

  const showJobs: AppTool = {
      name: "show_jobs",
      description: "Set which jobs the view compares (the first is the reference), with optional labels.",
      inputSchema: {
        type: "object",
        properties: { job_ids: { type: "array", items: { type: "string" }, minItems: 1 }, labels: { type: "array", items: { type: "string" } } },
        required: ["job_ids"],
      },
      run: async (args) => {
        const ids = (args.job_ids as unknown[]).map(String);
        await reload({ job_ids: ids, labels: (args.labels as string[] | undefined) ?? null });
        return textResult(`Comparing ${ids.join(", ")}.`, { job_ids: ids });
      },
  };
  setAppTools([showJobs, ...pictureTools(() => frame, () => ({ text: "The results chart.", under: {} })).filter((t) => t.name === "capture")]);

  await render();
}

function themeConfig(): Json {
  const text = cssVar("--color-text-primary");
  const muted = cssVar("--color-text-secondary");
  const grid = cssVar("--color-border-primary");
  return {
    background: cssVar("--color-background-primary"),
    font: "system-ui, sans-serif",
    axis: { labelColor: muted, titleColor: text, gridColor: grid, domainColor: grid, tickColor: grid },
    legend: { labelColor: text, titleColor: text },
    title: { color: text },
    view: { stroke: null },
  };
}

function samplerSpec(cmp: Comparison): Json {
  const set = cmp.sets[0] as { probabilities: Json[] };
  let rows = set.probabilities;
  let note = "";
  if (rows.length > MAX_BARS) {
    rows = [...rows]
      .sort((a, b) => Math.max(...cmp.jobs.map((j) => Number(b[j.job_id]))) - Math.max(...cmp.jobs.map((j) => Number(a[j.job_id]))))
      .slice(0, MAX_BARS);
    note = ` (the ${MAX_BARS} most likely of ${set.probabilities.length})`;
  }
  const values = rows.flatMap((r) => cmp.jobs.map((j) => ({ bitstring: r.bitstring, job: j.label, probability: r[j.job_id] })));
  return {
    $schema: "https://vega.github.io/schema/vega-lite/v6.json",
    width: "container", height: 260, autosize: { type: "fit-x", contains: "padding" },
    title: { text: `Probability per bitstring${note}`, anchor: "start", fontSize: 12 },
    data: { values },
    mark: { type: "bar", tooltip: true },
    encoding: {
      x: { field: "bitstring", type: "nominal", title: "Bitstring", sort: "ascending", axis: { labelAngle: -45 } },
      xOffset: { field: "job" },
      y: { field: "probability", type: "quantitative", title: "Probability", axis: { format: ".2f" } },
      color: { field: "job", type: "nominal", title: "Job", legend: { orient: "bottom", labelLimit: 280 } },
    },
  };
}

function estimatorSpec(cmp: Comparison, expected: number[] | null): Json {
  const values: Json[] = [];
  for (const entry of cmp.sets as { index: number; jobs: Record<string, { values: (number | null)[]; standard_errors: (number | null)[] }> }[]) {
    for (const j of cmp.jobs) {
      const v = entry.jobs[j.job_id]?.values?.[0];
      const e = entry.jobs[j.job_id]?.standard_errors?.[0] ?? 0;
      if (v === null || v === undefined) continue;
      values.push({ set: entry.index, job: j.label, value: v, low: v - (e ?? 0), high: v + (e ?? 0),
        expected: expected?.[entry.index] ?? null });
    }
  }
  const threshold = cmp.classification?.threshold ?? 0;
  const layers: Json[] = [
    { mark: { type: "rule", strokeDash: [4, 4] }, encoding: { y: { datum: threshold } } },
    {
      mark: { type: "rule" },
      encoding: { x: { field: "set", type: "ordinal" }, xOffset: { field: "job" }, y: { field: "low", type: "quantitative" }, y2: { field: "high" }, color: { field: "job", type: "nominal" } },
    },
    {
      mark: { type: "point", filled: true, size: 60, tooltip: true },
      encoding: {
        x: { field: "set", type: "ordinal", title: "Parameter set" },
        xOffset: { field: "job" },
        y: { field: "value", type: "quantitative", title: `⟨${cmp.observables?.[0] ?? "O"}⟩`, scale: { domain: [-1.05, 1.05] } },
        color: { field: "job", type: "nominal", title: "Job", legend: { orient: "bottom", labelLimit: 280 } },
        shape: expected ? { field: "expected", type: "nominal", title: "Expected class", legend: { orient: "bottom" } } : { value: "circle" },
      },
    },
  ];
  return {
    $schema: "https://vega.github.io/schema/vega-lite/v6.json",
    width: "container", height: 280, autosize: { type: "fit-x", contains: "padding" },
    title: { text: `Expectation value per parameter set${expected ? "; shape is the expected class, the dashed line the class threshold" : ""}`, anchor: "start", fontSize: 12 },
    data: { values },
    layer: layers,
  };
}
