// The backend view (QV13): the device map colored by a calibration metric.
import { drawDevice, edgeKey, ramp, rampCss, type Device } from "../device-draw";
import { busy, button, el, fmt } from "../dom";
import { app, callServer, onThemeChange, report, setAppTools, textResult, type Json } from "../host";
import { pictureTools, setFacts, shell } from "./common";

interface QubitMetric { qubit: number; t1_us: number | null; t2_us: number | null; readout_error: number | null }
interface EdgeMetric { edge: [number, number]; gate: string | null; two_qubit_error: number | null }

interface BackendData extends Device {
  kind: string;
  qubit_metrics: QubitMetric[];
  edge_metrics: EdgeMetric[];
  queue: number | null;
  time_left_seconds: number | null;
  note?: string;
}

type Metric = "t1_us" | "t2_us" | "readout_error" | "two_qubit_error";
const METRICS: Record<Metric, { label: string; unit: string; lowIsGood: boolean }> = {
  t1_us: { label: "T1", unit: "µs", lowIsGood: false },
  t2_us: { label: "T2", unit: "µs", lowIsGood: false },
  readout_error: { label: "Readout error", unit: "", lowIsGood: true },
  two_qubit_error: { label: "Two-qubit error", unit: "", lowIsGood: true },
};

export async function backendView(request: Json): Promise<void> {
  const view = shell("Device");
  view.message("Reading the device…");
  const data = (await callServer("view_data_backend", request)) as unknown as BackendData;
  view.message(data.note ?? "");
  let metric: Metric = "two_qubit_error";
  let chain: number[] = [];
  let picked = new Set<number>();
  const frame = el("div", { class: "frame" });
  const legend = el("div", { class: "legend", "aria-live": "polite" });
  const panel = el("section", { class: "panel", hidden: true });
  view.body.append(legend, frame, panel);

  const qubit = new Map(data.qubit_metrics.map((m) => [m.qubit, m]));
  const edge = new Map(data.edge_metrics.map((m) => [edgeKey(m.edge[0], m.edge[1]), m]));

  setFacts(view.facts, [
    ["Device", data.name], ["Qubits", data.num_qubits], ["Couplings", data.edges.length],
    ["Two-qubit gate", data.two_qubit_gate ?? "n/a"],
    ["Queue", data.queue ?? (data.kind === "fake" ? "none (simulated)" : "unknown")],
    ...(data.time_left_seconds !== null ? [["Time left", `${fmt(data.time_left_seconds, 1)} s`] as [string, string]] : []),
  ]);

  const metricSelect = el("select", { "aria-label": "Colour by" },
    ...Object.entries(METRICS).map(([k, m]) => el("option", { value: k, text: `Colour by ${m.label}` })));
  metricSelect.value = metric;
  metricSelect.addEventListener("change", () => { metric = metricSelect.value as Metric; draw(); });
  const lengthInput = el("input", { type: "number", min: 2, max: 20, value: 5, "aria-label": "Chain length", style: "width: 4em" });
  const chains = button("Find good chains", () => void busy(chains, async () => {
    const out = await callServer("find_optimal_qubit_chains", { backend_name: data.name, chain_length: Number(lengthInput.value), num_results: 5 });
    const found = out.chains as { rank: number; qubits: number[]; score: number }[];
    chain = found[0]?.qubits ?? [];
    draw();
    panel.hidden = false;
    panel.replaceChildren(
      el("h2", { text: `Best ${lengthInput.value}-qubit chains on ${data.name} (lower score is better)` }),
      el("table", {}, el("tr", {}, ...["Rank", "Qubits", "Score"].map((h) => el("th", { text: h }))),
        ...found.map((c) => {
          const row = el("tr", {}, el("td", { text: String(c.rank) }), el("td", { text: c.qubits.join(", ") }), el("td", { text: fmt(c.score, 5) }));
          row.style.cursor = "pointer";
          row.addEventListener("click", () => { chain = c.qubits; draw(); });
          return row;
        })),
      el("div", { class: "tools" },
        button("Open a layout on the highlighted chain", () => void openLayout()),
        button("Back to the map", () => { panel.hidden = true; chain = []; draw(); })),
    );
    report({ selection: { kind: "qubit_chain", qubits: chain } }, { selection: `The best chain found on ${data.name} is qubits ${chain.join(", ")}.` });
  }, (m) => view.message(m, true)));
  view.tools.append(metricSelect, el("label", { class: "inline", text: "Length" }), lengthInput, chains);

  async function openLayout() {
    if (!chain.length) return;
    // A layout needs a circuit, which the agent has; the request goes to the conversation.
    await app.sendMessage({
      role: "user",
      content: [{ type: "text", text: `Open the layout view for the current circuit on ${data.name} with initial_layout [${chain.join(", ")}].` }],
    });
    view.message("Asked the agent to open the layout on these qubits.");
  }

  function values(): (number | null)[] {
    return metric === "two_qubit_error"
      ? data.edge_metrics.map((m) => m.two_qubit_error)
      : data.qubit_metrics.map((m) => m[metric as keyof QubitMetric] as number | null);
  }

  function draw() {
    const meta = METRICS[metric];
    // Error rates of 0.5 and above mark a qubit or coupling out of service (fake devices use
    // 1); they get their own colour so the scale covers the ones that work.
    const outOfService = (v: number | null | undefined) => meta.lowIsGood && v !== null && v !== undefined && v >= 0.5;
    const vs = values()
      .filter((v): v is number => v !== null && Number.isFinite(v) && !outOfService(v))
      .sort((a, b) => a - b);
    const broken = values().filter((v) => outOfService(v)).length;
    // Clip to the 2nd–98th percentile so one bad qubit does not wash out the rest.
    const lo = vs[Math.floor(vs.length * 0.02)] ?? 0;
    const hi = vs[Math.min(vs.length - 1, Math.floor(vs.length * 0.98))] ?? 1;
    const color = (v: number | null | undefined) => (outOfService(v) ? "#6f6f6f" : ramp(v ?? null, lo, hi, meta.lowIsGood));
    const strong = new Set<number>([...chain, ...picked]);
    const strongEdges = new Set<string>();
    for (let i = 1; i < chain.length; i++) strongEdges.add(edgeKey(chain[i - 1], chain[i]));
    const map = drawDevice(data, {
      nodeFill: metric === "two_qubit_error" ? undefined : (q) => color(qubit.get(q)?.[metric as keyof QubitMetric] as number | null),
      edgeStroke: metric === "two_qubit_error" ? (a, b) => color(edge.get(edgeKey(a, b))?.two_qubit_error) : undefined,
      strong, strongEdges,
      describeQubit: (q) => describeQubit(q),
      describeEdge: (a, b) => describeEdge(a, b),
      onQubit: (q) => pickQubit(q),
      onEdge: (a, b) => pickEdge(a, b),
      maxHeight: 640,
    });
    frame.replaceChildren(map.node);
    const unit = meta.unit ? ` ${meta.unit}` : "";
    legend.replaceChildren(
      el("span", { text: `${meta.label}${metric === "two_qubit_error" ? " (couplings)" : " (qubits)"}:` }),
      el("span", { text: `${fmt(lo, meta.unit ? 1 : 4)}${unit}` }),
      Object.assign(el("span", { class: "ramp", role: "img", "aria-label": `${meta.lowIsGood ? "low is better" : "high is better"}, blue is better` }), {}),
      el("span", { text: `${fmt(hi, meta.unit ? 1 : 4)}${unit}` }),
      el("span", { text: `(blue is better; ${meta.lowIsGood ? "lower" : "higher"} is better)` }),
      ...(broken
        ? [el("span", { class: "key" }, el("span", { class: "swatch", style: "background:#6f6f6f" }),
            `${broken} out of service (error 0.5 or more)`)]
        : []),
    );
    (legend.querySelector(".ramp") as HTMLElement).style.background = rampCss(meta.lowIsGood);
    report({ backend: data.name, metric, legend: { metric, min: lo, max: hi, unit: meta.unit } },
      { view: `The backend view shows ${data.name} coloured by ${meta.label} from ${fmt(lo, 4)} to ${fmt(hi, 4)}${unit}.` });
  }

  function describeQubit(q: number): string {
    const m = qubit.get(q);
    return `Qubit ${q}: T1 ${fmt(m?.t1_us, 1)} µs, T2 ${fmt(m?.t2_us, 1)} µs, readout error ${fmt(m?.readout_error, 4)}`;
  }
  function describeEdge(a: number, b: number): string {
    const m = edge.get(edgeKey(a, b));
    return `Coupling ${a}–${b}: ${m?.gate ?? "two-qubit"} error ${fmt(m?.two_qubit_error, 4)}`;
  }
  function pickQubit(q: number) {
    picked = new Set([q]);
    draw();
    const m = qubit.get(q);
    view.selection.replaceChildren("Selected ", el("b", { text: describeQubit(q) }));
    report({ selection: { kind: "physical_qubit", qubit: q, t1_us: m?.t1_us ?? null, t2_us: m?.t2_us ?? null, readout_error: m?.readout_error ?? null } },
      { selection: `The person selected ${describeQubit(q)} in the backend view.` });
  }
  function pickEdge(a: number, b: number) {
    picked = new Set([a, b]);
    draw();
    const m = edge.get(edgeKey(a, b));
    view.selection.replaceChildren("Selected ", el("b", { text: describeEdge(a, b) }));
    report({ selection: { kind: "edge", qubits: [a, b], gate: m?.gate ?? null, two_qubit_error: m?.two_qubit_error ?? null } },
      { selection: `The person selected ${describeEdge(a, b)} in the backend view.` });
  }

  onThemeChange(() => draw());

  setAppTools([
    {
      name: "focus",
      description: "Highlight physical qubits (for example a chain).",
      inputSchema: { type: "object", properties: { qubits: { type: "array", items: { type: "integer" } } }, required: ["qubits"] },
      run: async (args) => {
        chain = ((args.qubits as number[]) ?? []).map(Number);
        draw();
        return textResult(`Highlighted qubits ${chain.join(", ")}.`);
      },
    },
    ...pictureTools(() => frame, (items) => {
      const lines: string[] = [];
      for (const i of items) {
        if (i.kind === "physical_qubit") lines.push(`- ${describeQubit(Number(i.qubit))}`);
        if (i.kind === "edge") {
          const [a, b] = i.qubits as [number, number];
          lines.push(`- ${describeEdge(a, b)}`);
        }
      }
      return lines.length ? `Under the mark on ${data.name}:\n${lines.join("\n")}` : "Nothing of the device lies under the mark.";
    }),
  ]);
  draw();
}
