// Drawing a circuit from the server's JSON: wires, gates by layer, named blocks shaded.
import { cssVar } from "./host";
import { fmt, svg } from "./dom";

export interface Gate {
  index: number;
  name: string;
  qubits: number[];
  clbits: number[];
  params: (number | string)[];
  directive: boolean;
}

export interface Block {
  name: string;
  start: number;
  end: number;
  first_layer: number | null;
  last_layer: number | null;
  qubits: number[];
}

export interface CircuitData {
  num_qubits: number;
  num_clbits: number;
  qubits: string[];
  clbits: string[];
  layers: Gate[][];
  used_qubits: number[];
  blocks: Block[];
  counts: Record<string, unknown>;
}

export interface DrawOptions {
  /** The qubits to draw, in order; default every qubit. */
  wires?: number[];
  /** A label per drawn qubit index; default the circuit's names. */
  labels?: (q: number) => string;
  onSelect?: (gate: Gate, layer: number) => void;
  /** Layers past this one are drawn faded (the layout view's step). */
  upTo?: number;
  compact?: boolean;
}

const ROW = 40;
const LEFT = 64;
const TOP = 28;

const CONTROLLED: Record<string, string> = {
  cx: "X", cy: "Y", cz: "Z", ch: "H", crx: "RX", cry: "RY", crz: "RZ", cp: "P", cu: "U",
  ccx: "X", ccz: "Z", cswap: "SWAP", csx: "SX", cs: "S", csdg: "S†",
};

/** The tints blocks take in turn, readable in both themes. */
export function blockTints(): string[] {
  return [cssVar("--q-block-1"), cssVar("--q-block-2"), cssVar("--q-block-3")];
}

/** The names of a circuit's blocks beside their tints, for the view's header. */
export function blockLegend(data: CircuitData): HTMLElement | null {
  if (!data.blocks.length) return null;
  const tints = blockTints();
  const legend = document.createElement("div");
  legend.className = "legend";
  data.blocks.forEach((block, b) => {
    const key = document.createElement("span");
    key.className = "key";
    const swatch = document.createElement("span");
    swatch.className = "block-swatch";
    swatch.style.background = tints[b % tints.length];
    key.append(swatch, `${block.name} (gates ${block.start} to ${block.end - 1})`);
    legend.append(key);
  });
  return legend;
}

export function gateLabel(gate: Gate): string {
  const base = gate.name === "measure" ? "M" : gate.name.length <= 4 ? gate.name.toUpperCase() : gate.name;
  return base.replace("DG", "†");
}

export function paramText(gate: Gate): string {
  return gate.params.map((p) => (typeof p === "number" ? fmt(p, 3) : String(p))).join(", ");
}

export function describeGate(gate: Gate, names: string[]): string {
  const params = paramText(gate);
  const on = gate.qubits.map((q) => names[q] ?? `q${q}`).join(", ");
  return `${gate.name}${params ? `(${params})` : ""} on ${on}`;
}

interface Palette {
  wire: string; text: string; muted: string; gate: string; border: string; accent: string;
  select: string; block: string; bg: string;
}

function palette(): Palette {
  return {
    wire: cssVar("--q-wire"),
    text: cssVar("--color-text-primary"),
    muted: cssVar("--color-text-secondary"),
    gate: cssVar("--q-gate"),
    border: cssVar("--q-gate-border"),
    accent: cssVar("--q-accent"),
    select: cssVar("--q-select"),
    block: cssVar("--q-accent-soft"),
    bg: cssVar("--color-background-primary"),
  };
}

/** Column widths per layer: wide enough for the widest gate's label and parameters. */
function columnWidths(data: CircuitData, compact: boolean): number[] {
  return data.layers.map((layer) => {
    let w = compact ? 34 : 42;
    for (const g of layer) {
      const label = gateLabel(g).length * 7 + 16;
      const params = g.params.length && !compact ? paramText(g).length * 5.6 + 12 : 0;
      w = Math.max(w, label, params);
    }
    return Math.min(w, 160);
  });
}

export interface DrawnCircuit {
  node: SVGSVGElement;
  select: (indices: Set<number>) => void;
  layerX: number[];
}

export function drawCircuit(data: CircuitData, options: DrawOptions = {}): DrawnCircuit {
  const p = palette();
  const wires = options.wires ?? data.qubits.map((_, i) => i);
  const row = new Map(wires.map((q, i) => [q, i]));
  const widths = columnWidths(data, !!options.compact);
  const layerX: number[] = [];
  let x = LEFT + 8;
  for (const w of widths) {
    layerX.push(x + w / 2);
    x += w + 6;
  }
  const hasClbits = data.num_clbits > 0;
  const width = x + 16;
  const classicalY = TOP + wires.length * ROW + (hasClbits ? 8 : 0);
  const height = classicalY + (hasClbits ? 30 : 4);
  const root = svg("svg", {
    width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": `Circuit with ${data.num_qubits} qubits in ${data.layers.length} layers`,
    "font-family": "system-ui, sans-serif", "font-size": 12,
  });
  root.append(svg("rect", { x: 0, y: 0, width, height, fill: p.bg }));
  const y = (q: number) => TOP + (row.get(q) ?? 0) * ROW + ROW / 2;

  // Blocks behind everything, each in its own tint; their names are in blockLegend().
  const tints = blockTints();
  data.blocks.forEach((block, b) => {
    if (block.first_layer === null || block.last_layer === null) return;
    const rows = block.qubits.filter((q) => row.has(q)).map((q) => row.get(q) as number);
    if (!rows.length) return;
    const x0 = layerX[block.first_layer] - widths[block.first_layer] / 2 - 3;
    const x1 = layerX[block.last_layer] + widths[block.last_layer] / 2 + 3;
    const y0 = TOP + Math.min(...rows) * ROW + 2;
    const y1 = TOP + (Math.max(...rows) + 1) * ROW - 2;
    const title = svg("title");
    title.textContent = `${block.name}: gates ${block.start} to ${block.end - 1}`;
    root.append(
      svg("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, rx: 6, fill: tints[b % tints.length], opacity: 0.6,
        "data-item": JSON.stringify({ kind: "block", name: block.name, gates: [block.start, block.end] }) }, title),
    );
  });

  // Wires and their names.
  for (const q of wires) {
    const label = options.labels ? options.labels(q) : data.qubits[q] ?? `q${q}`;
    root.append(
      svg("line", { x1: LEFT, x2: width - 8, y1: y(q), y2: y(q), stroke: p.wire, "stroke-width": 1 }),
      svg("text", { x: LEFT - 8, y: y(q) + 4, "text-anchor": "end", fill: p.text, text: label,
        "data-item": JSON.stringify({ kind: "qubit", qubit: q, name: label }) }),
    );
  }
  if (hasClbits) {
    root.append(
      svg("line", { x1: LEFT, x2: width - 8, y1: classicalY - 1.5, y2: classicalY - 1.5, stroke: p.wire }),
      svg("line", { x1: LEFT, x2: width - 8, y1: classicalY + 1.5, y2: classicalY + 1.5, stroke: p.wire }),
      svg("text", { x: LEFT - 8, y: classicalY + 4, "text-anchor": "end", fill: p.muted,
        text: `c (${data.num_clbits})` }),
    );
  }

  // Measurement arrows run down to the classical wire behind the gates they pass.
  const behind = svg("g");
  root.append(behind);
  const gates = new Map<number, SVGGElement>();
  data.layers.forEach((layer, n) => {
    for (const gate of layer) {
      const shown = gate.qubits.filter((q) => row.has(q));
      if (!shown.length) continue;
      const g = drawGate(gate, n, layerX[n], widths[n], y, classicalY, shown, p, !!options.compact, behind);
      if (options.upTo !== undefined && n > options.upTo) g.setAttribute("opacity", "0.28");
      g.setAttribute("data-item", JSON.stringify({ kind: "gate", index: gate.index, name: gate.name,
        layer: n, qubits: gate.qubits, params: gate.params }));
      g.setAttribute("tabindex", "0");
      g.setAttribute("role", "button");
      g.setAttribute("aria-label", `Gate ${gate.index}: ${describeGate(gate, data.qubits)}, layer ${n}`);
      g.style.cursor = "pointer";
      const title = svg("title");
      title.textContent = `${describeGate(gate, data.qubits)} — layer ${n}, gate ${gate.index}`;
      g.prepend(title);
      const pick = () => options.onSelect?.(gate, n);
      g.addEventListener("click", pick);
      g.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
      });
      gates.set(gate.index, g);
      root.append(g);
    }
  });
  // Layer numbers along the top.
  layerX.forEach((lx, n) => {
    if (data.layers.length > 40 && n % 5) return;
    root.append(svg("text", { x: lx, y: 14, "text-anchor": "middle", fill: p.muted, "font-size": 10, text: String(n) }));
  });

  const select = (indices: Set<number>) => {
    for (const [index, g] of gates) {
      const on = indices.has(index);
      g.querySelectorAll<SVGElement>("[data-shape]").forEach((s) => {
        s.setAttribute("stroke", on ? p.select : s.dataset.stroke ?? p.border);
        s.setAttribute("stroke-width", on ? "2.5" : s.dataset.strokeWidth ?? "1");
      });
    }
  };
  return { node: root, select, layerX };
}

function shape<T extends SVGElement>(node: T, stroke: string, width = 1): T {
  node.dataset.shape = "1";
  node.dataset.stroke = stroke;
  node.dataset.strokeWidth = String(width);
  node.setAttribute("stroke", stroke);
  node.setAttribute("stroke-width", String(width));
  return node;
}

function drawGate(
  gate: Gate, _layer: number, cx: number, w: number, y: (q: number) => number,
  classicalY: number, shown: number[], p: Palette, compact: boolean, behind: SVGGElement,
): SVGGElement {
  const g = svg("g", { class: "gate" });
  const ys = shown.map(y);
  const top = Math.min(...ys);
  const bottom = Math.max(...ys);
  const boxW = w - 6;
  if (gate.directive) {
    g.append(shape(svg("rect", { x: cx - 5, y: top - 16, width: 10, height: bottom - top + 32, fill: p.muted,
      opacity: 0.18 }), p.muted, 1));
    g.append(svg("line", { x1: cx, x2: cx, y1: top - 16, y2: bottom + 16, stroke: p.muted, "stroke-dasharray": "3 3" }));
    return g;
  }
  if (gate.name === "measure") {
    const yq = ys[0];
    behind.append(
      svg("line", { x1: cx - 1.5, x2: cx - 1.5, y1: yq + 13, y2: classicalY - 6, stroke: p.wire, "stroke-width": 1 }),
      svg("line", { x1: cx + 1.5, x2: cx + 1.5, y1: yq + 13, y2: classicalY - 6, stroke: p.wire, "stroke-width": 1 }),
      svg("path", { d: `M ${cx - 4} ${classicalY - 9} L ${cx} ${classicalY - 3} L ${cx + 4} ${classicalY - 9} Z`, fill: p.wire }),
    );
    g.append(
      shape(svg("rect", { x: cx - 13, y: yq - 13, width: 26, height: 26, rx: 4, fill: p.gate }), p.border),
      svg("path", { d: `M ${cx - 8} ${yq + 5} A 8 8 0 0 1 ${cx + 8} ${yq + 5}`, fill: "none", stroke: p.text }),
      svg("line", { x1: cx, x2: cx + 6, y1: yq + 5, y2: yq - 6, stroke: p.text }),
    );
    if (gate.clbits.length) {
      g.append(svg("text", { x: cx + 6, y: classicalY + 14, fill: p.muted, "font-size": 10, text: String(gate.clbits[0]) }));
    }
    return g;
  }
  if (gate.name === "swap" && gate.qubits.length === 2) {
    g.append(svg("line", { x1: cx, x2: cx, y1: top, y2: bottom, stroke: p.text, "stroke-width": 1.5 }));
    for (const yy of ys) {
      g.append(shape(svg("path", { d: `M ${cx - 7} ${yy - 7} L ${cx + 7} ${yy + 7} M ${cx - 7} ${yy + 7} L ${cx + 7} ${yy - 7}`,
        fill: "none" }), p.text, 2));
    }
    return g;
  }
  const target = CONTROLLED[gate.name];
  const nControls = gate.name.startsWith("cc") ? 2 : target ? 1 : 0;
  if (target && gate.qubits.length >= 2 && shown.length === gate.qubits.length) {
    const controls = gate.qubits.slice(0, nControls).map(y);
    const targets = gate.qubits.slice(nControls).map(y);
    g.append(svg("line", { x1: cx, x2: cx, y1: top, y2: bottom, stroke: p.text, "stroke-width": 1.5 }));
    for (const yy of controls) g.append(shape(svg("circle", { cx, cy: yy, r: 5, fill: p.text }), p.text));
    if (gate.name === "cz" || gate.name === "ccz" || gate.name === "cp") {
      for (const yy of targets) g.append(shape(svg("circle", { cx, cy: yy, r: 5, fill: p.text }), p.text));
      if (gate.name === "cp" && !compact) {
        g.append(svg("text", { x: cx + 8, y: (top + bottom) / 2 + 4, fill: p.text, "font-size": 10, text: `P(${paramText(gate)})` }));
      }
    } else if (target === "X") {
      for (const yy of targets) {
        g.append(
          shape(svg("circle", { cx, cy: yy, r: 11, fill: p.gate }), p.border, 1.5),
          svg("path", { d: `M ${cx - 11} ${yy} L ${cx + 11} ${yy} M ${cx} ${yy - 11} L ${cx} ${yy + 11}`, stroke: p.text, "stroke-width": 1.5 }),
        );
      }
    } else {
      const yt0 = Math.min(...targets) - 14;
      const yt1 = Math.max(...targets) + 14;
      g.append(shape(svg("rect", { x: cx - boxW / 2, y: yt0, width: boxW, height: yt1 - yt0, rx: 4, fill: p.gate }), p.border));
      g.append(svg("text", { x: cx, y: (yt0 + yt1) / 2 + 4, "text-anchor": "middle", fill: p.text, text: target }));
    }
    return g;
  }
  // A box over its qubits, with its parameters beneath the name.
  const y0 = top - 14;
  const y1 = bottom + 14;
  g.append(shape(svg("rect", { x: cx - boxW / 2, y: y0, width: boxW, height: y1 - y0, rx: 4, fill: p.gate }), p.border));
  const params = paramText(gate);
  const label = gateLabel(gate);
  if (params && !compact) {
    g.append(
      svg("text", { x: cx, y: (y0 + y1) / 2 - 1, "text-anchor": "middle", fill: p.text, "font-weight": 600, text: label }),
      svg("text", { x: cx, y: (y0 + y1) / 2 + 10, "text-anchor": "middle", fill: p.muted, "font-size": 9.5,
        text: params.length > 24 ? params.slice(0, 23) + "…" : params }),
    );
  } else {
    g.append(svg("text", { x: cx, y: (y0 + y1) / 2 + 4, "text-anchor": "middle", fill: p.text, "font-weight": 600, text: label }));
  }
  return g;
}
