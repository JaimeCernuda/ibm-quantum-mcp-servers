// The circuit view (QV10): wires and gates by layer, named blocks shaded.
import { blockLegend, describeGate, drawCircuit, type CircuitData, type Gate } from "../circuit-draw";
import { busy, button, el } from "../dom";
import { callServer, onThemeChange, report, setAppTools, textResult, type Json } from "../host";
import { highlightQasm } from "../qasm-highlight";
import { pictureTools, setFacts, shell, type Described } from "./common";

interface CircuitViewData extends CircuitData {
  qasm3: string;
  qpy: string;
  job_id: string | null;
  backend?: string;
}

export async function circuitView(request: Json): Promise<void> {
  const view = shell("Circuit");
  view.message("Reading the circuit…");
  const data = (await callServer("view_data_circuit", request)) as unknown as CircuitViewData;
  view.message("");
  let selected = new Set<number>();
  const frame = el("div", { class: "frame" });
  const panel = el("section", { class: "panel", hidden: true });
  const legend = blockLegend(data);
  if (legend) view.body.append(legend);
  view.body.append(frame, panel);

  const gateByIndex = new Map<number, { gate: Gate; layer: number }>();
  data.layers.forEach((layer, n) => layer.forEach((gate) => gateByIndex.set(gate.index, { gate, layer: n })));

  let drawn = draw();
  function draw() {
    const d = drawCircuit(data, { onSelect: (gate, layer) => choose(gate, layer) });
    frame.replaceChildren(d.node);
    d.select(selected);
    return d;
  }
  onThemeChange(() => { drawn = draw(); });

  const c = data.counts as Record<string, number>;
  setFacts(view.facts, [
    ["Qubits", c.num_qubits], ["Depth", c.depth], ["Operations", c.size],
    ["Two-qubit gates", c.two_qubit_gates],
    ...(c.num_parameters ? [["Parameters", c.num_parameters] as [string, number]] : []),
  ]);

  const showPanel = (title: string, content: Node) => {
    panel.hidden = false;
    panel.replaceChildren(el("h2", { text: title }), content, button("Back to the circuit", () => { panel.hidden = true; }));
  };
  const qasmButton = button("QASM 3", () =>
    void busy(qasmButton, async () => {
      const out = await callServer("export_circuit_to_qasm", { circuit_qpy: data.qpy, qasm_version: "3.0" });
      showPanel("OpenQASM 3", el("pre", { class: "qasm" }, ...highlightQasm(String(out.qasm ?? out.circuit ?? data.qasm3))));
    }, (m) => view.message(m, true)),
  );
  const analyzeButton = button("Analyze", () =>
    void busy(analyzeButton, async () => {
      const out = await callServer("analyze_circuit", { circuit: data.qasm3 });
      const info = (out.circuit_info ?? {}) as Record<string, unknown>;
      const cats = (out.gate_categories ?? {}) as Record<string, unknown>;
      const ops = (info.operation_counts ?? {}) as Record<string, number>;
      const rows: [string, unknown][] = [
        ["Depth", info.depth], ["Size", info.size], ["Width", info.width],
        ["Single-qubit gates", cats.single_qubit_gates], ["Two-qubit gates", cats.two_qubit_gates],
        ["Multi-qubit gates", cats.multi_qubit_gates],
        ...Object.entries(ops).map(([k, v]) => [`  ${k}`, v] as [string, unknown]),
      ];
      showPanel("Analysis", el("table", {}, ...rows.map(([k, v]) => el("tr", {}, el("th", { text: k }), el("td", { text: String(v ?? "n/a") })))));
    }, (m) => view.message(m, true)),
  );
  view.tools.append(qasmButton, analyzeButton);

  function choose(gate: Gate, layer: number) {
    selected = new Set([gate.index]);
    drawn.select(selected);
    const text = `Gate ${gate.index}: ${describeGate(gate, data.qubits)}, layer ${layer}`;
    view.selection.replaceChildren("Selected ", el("b", { text }));
    report(
      {
        selection: { kind: "gate", index: gate.index, name: gate.name, layer, qubits: gate.qubits, params: gate.params },
        selected_gate: { index: gate.index, name: gate.name, qubits: gate.qubits },
      },
      { selection: `The person selected ${text} in the circuit view.` },
    );
  }

  function focus(args: Json) {
    const indices = new Set<number>();
    const sg = args.selected_gate as { index?: number } | null | undefined;
    if (sg && typeof sg.index === "number") indices.add(sg.index);
    for (const i of (args.gates as number[] | undefined) ?? []) indices.add(Number(i));
    if (typeof args.block === "string") {
      const block = data.blocks.find((b) => b.name === args.block);
      if (!block) throw new Error(`No block ${args.block}; blocks: ${data.blocks.map((b) => b.name).join(", ") || "none"}.`);
      for (let i = block.start; i < block.end; i++) indices.add(i);
    }
    const qubits = new Set(((args.qubits as number[] | undefined) ?? []).map(Number));
    if (qubits.size) for (const { gate } of gateByIndex.values()) if (gate.qubits.some((q) => qubits.has(q))) indices.add(gate.index);
    selected = indices;
    drawn.select(selected);
    const first = [...indices].sort((a, b) => a - b)[0];
    const hit = first !== undefined ? gateByIndex.get(first) : undefined;
    if (hit) {
      const g = frame.querySelector(`[aria-label^="Gate ${hit.gate.index}:"]`);
      (g as Element | null)?.scrollIntoView?.({ block: "nearest", inline: "center" });
    }
    view.selection.replaceChildren(indices.size ? `Highlighted ${indices.size} gate${indices.size === 1 ? "" : "s"}.` : "Nothing highlighted.");
    return textResult(
      indices.size ? `Highlighted gates ${[...indices].sort((a, b) => a - b).join(", ")}.` : "Cleared the highlight.",
      { highlighted: [...indices] },
    );
  }

  setAppTools([
    {
      name: "focus",
      description: "Highlight gates (by index), a named block, or every gate on some qubits. selected_gate {index} highlights one gate.",
      inputSchema: {
        type: "object",
        properties: {
          gates: { type: "array", items: { type: "integer" } },
          block: { type: "string" },
          qubits: { type: "array", items: { type: "integer" } },
          selected_gate: { type: ["object", "null"] },
        },
      },
      run: async (args) => focus(args),
    },
    ...pictureTools(() => frame, (items) => describeItems(items, data)),
  ]);
  report(
    { job: data.job_id ? [data.job_id] : [], circuit: { qubits: c.num_qubits, depth: c.depth, two_qubit_gates: c.two_qubit_gates } },
    { view: `The circuit view shows ${c.num_qubits} qubits, depth ${c.depth}, ${c.two_qubit_gates} two-qubit gates.` },
  );
}

function describeItems(items: Json[], data: CircuitData): Described {
  const gates = items.filter((i) => i.kind === "gate");
  const qubits = items.filter((i) => i.kind === "qubit");
  const blocks = items.filter((i) => i.kind === "block");
  const lines: string[] = [];
  if (gates.length) {
    lines.push(`${gates.length} gate${gates.length === 1 ? "" : "s"} under the mark:`);
    for (const g of gates) {
      const gate = g as unknown as Gate & { layer: number };
      lines.push(`- gate ${gate.index}: ${describeGate(gate, data.qubits)}, layer ${gate.layer}`);
    }
  }
  if (qubits.length) lines.push("Qubit wires: " + qubits.map((q) => String(q.name)).join(", "));
  if (blocks.length) lines.push("Inside blocks: " + blocks.map((b) => String(b.name)).join(", "));
  return {
    text: lines.join("\n") || "Nothing of the circuit lies under the mark.",
    under: {
      gates: gates.map((g) => ({
        gate: g.index, name: g.name, qubits: (g.qubits as number[]).map((q) => data.qubits[q] ?? q), layer: g.layer,
        params: (g.params as unknown[]).length ? g.params : undefined,
      })),
      "qubit wires": qubits.map((q) => q.name),
      blocks: blocks.map((b) => b.name),
    },
  };
}
