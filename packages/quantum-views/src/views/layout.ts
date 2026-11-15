// The layout view (QV11): the logical circuit beside the device map, stepped layer by layer.
import { describeGate, drawCircuit, type CircuitData, type Gate } from "../circuit-draw";
import { drawDevice, edgeKey, type Device } from "../device-draw";
import { busy, button, el } from "../dom";
import { callServer, onThemeChange, report, setAppTools, textResult, type Json } from "../host";
import { pictureTools, setFacts, shell, type Described } from "./common";

interface LayoutData {
  backend: string;
  job_id: string | null;
  logical: CircuitData;
  logical_qasm3: string;
  routed: CircuitData;
  routed_is_final: boolean;
  device: Device;
  optimization_level: number;
  before: Record<string, number>;
  after: Record<string, number>;
  layout: { initial: number[]; final: number[]; physical_qubits: number[] } | null;
  swaps: { layer: number; qubits: [number, number] }[];
}

interface Step {
  /** Which logical qubit each physical qubit holds after this layer's swaps. */
  holder: Map<number, number>;
  gates: Gate[];
}

/** Follow the logical qubits through the routed circuit's swaps, layer by layer. */
function steps(data: LayoutData): { states: Step[]; placed: Map<number, { layer: number; physical: number[] }> } {
  const holder = new Map<number, number>();
  (data.layout?.initial ?? []).forEach((physical, logical) => holder.set(physical, logical));
  const states: Step[] = [];
  const placed = new Map<number, { layer: number; physical: number[] }>();
  const pending = data.logical.layers.flat().sort((a, b) => a.index - b.index);
  data.routed.layers.forEach((layer, n) => {
    for (const gate of [...layer].sort((a, b) => a.index - b.index)) {
      if (gate.name === "swap") continue;
      const logical = gate.qubits.map((p) => holder.get(p));
      if (logical.some((l) => l === undefined)) continue;
      const match = pending.findIndex(
        (g) => !placed.has(g.index) && g.name === gate.name && g.qubits.length === logical.length && g.qubits.every((q, i) => q === logical[i]),
      );
      if (match >= 0) placed.set(pending[match].index, { layer: n, physical: gate.qubits });
    }
    for (const gate of layer) {
      if (gate.name !== "swap") continue;
      const [a, b] = gate.qubits;
      const la = holder.get(a);
      const lb = holder.get(b);
      holder.delete(a);
      holder.delete(b);
      if (la !== undefined) holder.set(b, la);
      if (lb !== undefined) holder.set(a, lb);
    }
    states.push({ holder: new Map(holder), gates: layer });
  });
  return { states, placed };
}

export async function layoutView(request: Json): Promise<void> {
  const view = shell("Circuit on a device");
  let current: Json = { ...request };
  view.message("Transpiling for the device…");
  let data = (await callServer("view_data_layout", current)) as unknown as LayoutData;
  view.message("");
  let wholeDevice = false;
  let step = 0;
  let selectedGate: number | null = null;
  let selectedPhysical: Set<number> = new Set();
  let model = steps(data);

  const left = el("div", { class: "frame" });
  const right = el("div", { class: "frame" });
  const slider = el("input", { type: "range", min: 0, max: 0, value: 0, "aria-label": "Layer of the transpiled circuit" });
  const stepText = el("span", { class: "selection" });
  const panel = el("section", { class: "panel", hidden: true });
  view.body.append(
    el("div", { class: "panes" },
      el("div", {}, el("p", { class: "pane-title", text: "Logical circuit" }), left),
      el("div", {}, el("p", { class: "pane-title", text: "On the device (logical qubit in each circle, physical index below; a red badge counts swaps on a coupling)" }), right)),
    el("div", { class: "tools" }, el("label", { class: "inline", text: "Step" }), slider, stepText),
    panel,
  );

  const levelSelect = el("select", { "aria-label": "Optimization level" },
    ...[0, 1, 2, 3].map((n) => el("option", { value: n, text: `Level ${n}` })));
  const backendSelect = el("select", { "aria-label": "Device" });
  const retranspile = button("Re-transpile", () => void busy(retranspile, async () => {
    const level = Number(levelSelect.value);
    const out = await callServer("transpile_circuit", {
      circuit: data.logical_qasm3, backend_name: data.backend, optimization_level: level,
      ...(current.initial_layout ? { initial_layout: current.initial_layout } : {}),
      ...(current.seed_transpiler != null ? { seed_transpiler: current.seed_transpiler } : {}),
    });
    await reload({ optimization_level: level });
    const after = (out.after ?? {}) as Record<string, number>;
    view.message(`Re-transpiled at level ${level}: ${after.two_qubit_gates} two-qubit gates, depth ${after.depth}.`);
  }, (m) => view.message(m, true)));
  const compare = button("Compare levels", () => void busy(compare, async () => {
    const out = await callServer("compare_optimization_levels", { circuit: data.logical_qasm3, backend_name: data.backend });
    const levels = out.levels as Record<string, Record<string, unknown>>;
    panel.hidden = false;
    panel.replaceChildren(
      el("h2", { text: `Optimization levels on ${data.backend}` }),
      el("table", {},
        el("tr", {}, ...["Level", "Depth", "Two-qubit gates", "Swaps"].map((h) => el("th", { text: h }))),
        ...Object.entries(levels).map(([level, v]) => el("tr", {},
          el("td", { text: level }), el("td", { text: String(v.depth) }), el("td", { text: String(v.two_qubit_gates) }), el("td", { text: String(v.swaps) })))),
      el("p", { class: "selection", text: `Fewest two-qubit gates at level ${out.fewest_two_qubit_gates}.` }),
      button("Back to the layout", () => { panel.hidden = true; }),
    );
  }, (m) => view.message(m, true)));
  const useBackend = button("Use this device", () => void busy(useBackend, async () => {
    await reload({ backend_name: backendSelect.value });
  }, (m) => view.message(m, true)));
  const zoom = button("Whole device", () => { wholeDevice = !wholeDevice; zoom.setAttribute("aria-pressed", String(wholeDevice)); drawMap(); }, { "aria-pressed": "false" });
  view.tools.append(levelSelect, retranspile, compare, backendSelect, useBackend, zoom);

  void (async () => {
    try {
      const out = await callServer("list_backends", { include_fake: true });
      const fakes = ((out.fake ?? []) as { name: string; num_qubits: number }[]).filter((b) => b.num_qubits >= 5);
      const real = ((out.hardware ?? []) as { name: string }[]).map((b) => b.name);
      backendSelect.replaceChildren(
        ...real.map((n) => el("option", { value: n, text: `${n} (real device)` })),
        ...fakes.map((b) => el("option", { value: b.name, text: `${b.name} (${b.num_qubits} qubits, simulated)` })),
      );
      backendSelect.value = data.backend;
    } catch (error) {
      view.message(String((error as Error).message), true);
    }
  })();

  async function reload(change: Json) {
    current = { ...current, ...change };
    view.message("Transpiling for the device…");
    data = (await callServer("view_data_layout", current)) as unknown as LayoutData;
    view.message("");
    model = steps(data);
    step = Math.max(0, data.routed.layers.length - 1);
    render();
  }

  function render() {
    levelSelect.value = String(data.optimization_level);
    if (backendSelect.options.length) backendSelect.value = data.backend;
    setFacts(view.facts, [
      ["Device", data.backend], ["Level", data.optimization_level],
      ["Depth", `${data.before.depth} → ${data.after.depth}`],
      ["Two-qubit gates", `${data.before.two_qubit_gates} → ${data.after.two_qubit_gates}`],
      ["Swaps", data.swaps.length],
      ["Physical qubits", (data.layout?.physical_qubits ?? []).join(", ")],
    ]);
    slider.max = String(Math.max(0, data.routed.layers.length - 1));
    slider.value = String(step);
    drawLogical();
    drawMap();
    report(
      {
        job: data.job_id ? [data.job_id] : [],
        layout: { backend: data.backend, optimization_level: data.optimization_level, initial: data.layout?.initial, swaps: data.swaps.length,
          depth: data.after.depth, two_qubit_gates: data.after.two_qubit_gates },
      },
      { view: `The layout view shows the circuit on ${data.backend} at level ${data.optimization_level}: qubits ${JSON.stringify(data.layout?.initial)}, ${data.swaps.length} swaps, ${data.after.two_qubit_gates} two-qubit gates, depth ${data.after.depth}.` },
    );
  }

  function drawLogical() {
    const d = drawCircuit(data.logical, { compact: true, onSelect: (gate, layer) => chooseGate(gate, layer) });
    left.replaceChildren(d.node);
    d.select(selectedGate === null ? new Set() : new Set([selectedGate]));
  }

  function drawMap() {
    const state = model.states[step];
    const holder = state?.holder ?? new Map<number, number>();
    const active = new Set<number>();
    const activeEdges = new Set<string>();
    for (const gate of state?.gates ?? []) {
      if (gate.directive || gate.name === "measure") continue;
      gate.qubits.forEach((q) => active.add(q));
      if (gate.qubits.length === 2) activeEdges.add(edgeKey(gate.qubits[0], gate.qubits[1]));
    }
    for (const q of selectedPhysical) active.add(q);
    const swapCount = new Map<string, number>();
    for (const s of data.swaps) {
      const key = edgeKey(s.qubits[0], s.qubits[1]);
      swapCount.set(key, (swapCount.get(key) ?? 0) + 1);
    }
    const used = new Set(data.layout?.physical_qubits ?? []);
    const map = drawDevice(data.device, {
      zoomTo: wholeDevice ? undefined : [...used],
      nodeLabel: (q) => (holder.has(q) ? `q${holder.get(q)}` : undefined),
      strong: active,
      strongEdges: activeEdges,
      edgeMarks: new Map([...swapCount].map(([k, n]) => [k, `swap${n > 1 ? `×${n}` : ""}`])),
      describeQubit: (q) => `Physical qubit ${q}${holder.has(q) ? `, holds logical q${holder.get(q)} after layer ${step}` : ", not used"}`,
      describeEdge: (a, b) => `Coupling ${a}–${b}${swapCount.get(edgeKey(a, b)) ? `, ${swapCount.get(edgeKey(a, b))} swaps` : ""}`,
      onQubit: (q) => chooseQubit(q, holder),
      onEdge: (a, b) => chooseEdge(a, b),
    });
    right.replaceChildren(map.node);
    const gates = (state?.gates ?? []).filter((g) => !g.directive);
    stepText.textContent = data.routed.layers.length
      ? `Layer ${step} of ${data.routed.layers.length - 1}${data.routed_is_final ? "" : " (routed, before translation to native gates)"}: ` +
        (gates.map((g) => `${g.name} on ${g.qubits.join(",")}`).join("; ") || "nothing")
      : "No layers.";
  }

  slider.addEventListener("input", () => { step = Number(slider.value); drawMap(); });

  function chooseGate(gate: Gate, layer: number) {
    selectedGate = gate.index;
    const where = model.placed.get(gate.index);
    const physical = where?.physical ?? gate.qubits.map((q) => data.layout?.initial[q] ?? q);
    selectedPhysical = new Set(physical);
    if (where) step = where.layer;
    slider.value = String(step);
    drawLogical();
    drawMap();
    const text = `gate ${gate.index}: ${describeGate(gate, data.logical.qubits)}, layer ${layer}, on physical qubits ${physical.join(", ")}` +
      (where ? ` at routed layer ${where.layer}` : " (placed by the initial layout)");
    view.selection.replaceChildren("Selected ", el("b", { text }));
    report(
      {
        selection: { kind: "gate", index: gate.index, name: gate.name, layer, qubits: gate.qubits, physical_qubits: physical, routed_layer: where?.layer ?? null },
        selected_gate: { index: gate.index, name: gate.name, qubits: gate.qubits },
      },
      { selection: `The person selected ${text} in the layout view.` },
    );
  }

  function chooseQubit(q: number, holder: Map<number, number>) {
    selectedPhysical = new Set([q]);
    drawMap();
    const logical = holder.get(q);
    const text = `physical qubit ${q}${logical !== undefined ? `, holding logical q${logical} after layer ${step}` : ", not used by this circuit"}`;
    view.selection.replaceChildren("Selected ", el("b", { text }));
    report({ selection: { kind: "physical_qubit", qubit: q, logical: logical ?? null, layer: step } },
      { selection: `The person selected ${text} in the layout view.` });
  }

  function chooseEdge(a: number, b: number) {
    selectedPhysical = new Set([a, b]);
    drawMap();
    const swaps = data.swaps.filter((s) => edgeKey(s.qubits[0], s.qubits[1]) === edgeKey(a, b)).map((s) => s.layer);
    const text = `coupling ${a}–${b}` + (swaps.length ? `, where routing put ${swaps.length} swap${swaps.length > 1 ? "s" : ""} (routed layers ${swaps.join(", ")})` : ", no swaps");
    view.selection.replaceChildren("Selected ", el("b", { text }));
    report({ selection: { kind: "edge", qubits: [a, b], swaps_at_layers: swaps, layer: step } },
      { selection: `The person selected ${text} in the layout view, at layer ${step}.` });
  }

  onThemeChange(() => { drawLogical(); drawMap(); });

  setAppTools([
    {
      name: "focus",
      description: "Highlight a logical gate (selected_gate {index} or gates [index]) on the physical qubits that run it, physical qubits, or a layer of the transpiled circuit.",
      inputSchema: {
        type: "object",
        properties: {
          selected_gate: { type: ["object", "null"] },
          gates: { type: "array", items: { type: "integer" } },
          qubits: { type: "array", items: { type: "integer" }, description: "Physical qubits." },
          layer: { type: "integer" },
        },
      },
      run: async (args) => {
        const sg = args.selected_gate as { index?: number } | null | undefined;
        const index = typeof sg?.index === "number" ? sg.index : (args.gates as number[] | undefined)?.[0];
        if (index !== undefined) {
          const found = data.logical.layers.flatMap((l, n) => l.map((g) => [g, n] as const)).find(([g]) => g.index === index);
          if (!found) return textResult(`No gate ${index} in the logical circuit.`);
          chooseGate(found[0], found[1]);
          return textResult(`Highlighted gate ${index} on physical qubits ${[...selectedPhysical].join(", ")}.`);
        }
        if (Array.isArray(args.qubits)) { selectedPhysical = new Set((args.qubits as number[]).map(Number)); }
        if (typeof args.layer === "number") step = Math.min(Math.max(0, args.layer), data.routed.layers.length - 1);
        slider.value = String(step);
        drawMap();
        return textResult(`Showing layer ${step}${selectedPhysical.size ? `, qubits ${[...selectedPhysical].join(", ")} highlighted` : ""}.`);
      },
    },
    ...pictureTools(() => view.body, (items) => describeItems(items, data, model.states[step]?.holder)),
  ]);

  step = Math.max(0, data.routed.layers.length - 1);
  render();
}

function describeItems(items: Json[], data: LayoutData, holder?: Map<number, number>): Described {
  const lines: string[] = [];
  const gates = items.filter((i) => i.kind === "gate");
  const qubits = items.filter((i) => i.kind === "physical_qubit").map((i) => Number(i.qubit));
  const edges = items.filter((i) => i.kind === "edge").map((i) => i.qubits as [number, number]);
  for (const g of gates) lines.push(`- logical gate ${g.index}: ${describeGate(g as unknown as Gate, data.logical.qubits)}, layer ${g.layer}`);
  if (qubits.length) {
    lines.push("Physical qubits: " + qubits.map((q) => (holder?.has(q) ? `${q} (holds q${holder.get(q)})` : `${q} (unused)`)).join(", "));
  }
  for (const [a, b] of edges) {
    const swaps = data.swaps.filter((s) => edgeKey(s.qubits[0], s.qubits[1]) === edgeKey(a, b));
    lines.push(`Coupling ${a}–${b}: ${swaps.length ? `${swaps.length} swaps at routed layers ${swaps.map((s) => s.layer).join(", ")}` : "no swaps"}`);
  }
  const swapsOn = (a: number, b: number) =>
    data.swaps.filter((s) => edgeKey(s.qubits[0], s.qubits[1]) === edgeKey(a, b)).map((s) => s.layer);
  return {
    text: lines.join("\n") || "Nothing of the layout lies under the mark.",
    under: {
      "logical gates": gates.map((g) => ({ gate: g.index, name: g.name, qubits: g.qubits, layer: g.layer })),
      "physical qubits": qubits.map((q) => ({ qubit: q, holds: holder?.has(q) ? `q${holder.get(q)}` : "unused" })),
      couplings: edges.map(([a, b]) => ({ coupling: `${a}-${b}`, swaps_at_layers: swapsOn(a, b) })),
    },
  };
}
