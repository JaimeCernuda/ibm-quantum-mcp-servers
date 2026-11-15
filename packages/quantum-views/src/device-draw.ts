// Drawing a device's coupling map from the server's positions.
import { cssVar } from "./host";
import { svg } from "./dom";

export interface Device {
  name: string;
  num_qubits: number;
  edges: [number, number][];
  positions: [number, number][];
  two_qubit_gate: string | null;
}

export interface MapOptions {
  /** Fill per qubit (colour), or undefined for the idle colour. */
  nodeFill?: (q: number) => string | undefined;
  /** Stroke per edge, or undefined for the idle colour. */
  edgeStroke?: (a: number, b: number) => string | undefined;
  /** Small text inside a qubit (a logical index), else the physical index when room. */
  nodeLabel?: (q: number) => string | undefined;
  /** Qubits and edges that stand out (used, active at this step, selected). */
  strong?: Set<number>;
  strongEdges?: Set<string>;
  /** Marks on edges: a count of swaps. */
  edgeMarks?: Map<string, string>;
  /** Restrict the view box to these qubits and their surroundings. */
  zoomTo?: number[];
  onQubit?: (q: number) => void;
  onEdge?: (a: number, b: number) => void;
  describeQubit?: (q: number) => string;
  describeEdge?: (a: number, b: number) => string;
  maxHeight?: number;
}

export const edgeKey = (a: number, b: number) => (a < b ? `${a}-${b}` : `${b}-${a}`);

export interface DrawnMap {
  node: SVGSVGElement;
}

const SCALE = 34;

export function drawDevice(device: Device, options: MapOptions = {}): DrawnMap {
  const idle = cssVar("--q-idle");
  const text = cssVar("--color-text-primary");
  const muted = cssVar("--color-text-secondary");
  const select = cssVar("--q-select");
  const bg = cssVar("--color-background-primary");
  const pos = device.positions.map(([px, py]) => [px * SCALE, py * SCALE] as [number, number]);
  const shown = options.zoomTo?.length ? options.zoomTo : pos.map((_, i) => i);
  const xs = shown.map((q) => pos[q][0]);
  const ys = shown.map((q) => pos[q][1]);
  const pad = options.zoomTo?.length ? SCALE * 1.6 : SCALE * 0.8;
  const minX = Math.min(...xs) - pad;
  const minY = Math.min(...ys) - pad;
  const w = Math.max(...xs) - Math.min(...xs) + 2 * pad;
  const h = Math.max(...ys) - Math.min(...ys) + 2 * pad;
  const root = svg("svg", {
    viewBox: `${minX} ${minY} ${w} ${h}`, width: "100%", role: "img",
    "aria-label": `${device.name}: ${device.num_qubits} qubits, ${device.edges.length} couplings`,
    "font-family": "system-ui, sans-serif", preserveAspectRatio: "xMidYMid meet",
  });
  root.style.maxHeight = `${options.maxHeight ?? 520}px`;
  root.style.height = "auto";
  root.style.aspectRatio = `${w} / ${h}`;
  root.append(svg("rect", { x: minX, y: minY, width: w, height: h, fill: bg }));
  const r = SCALE * 0.3;
  for (const [a, b] of device.edges) {
    const key = edgeKey(a, b);
    const strong = options.strongEdges?.has(key);
    const stroke = options.edgeStroke?.(a, b) ?? idle;
    const line = svg("line", {
      x1: pos[a][0], y1: pos[a][1], x2: pos[b][0], y2: pos[b][1],
      stroke: strong ? select : stroke, "stroke-width": strong ? 5 : options.edgeStroke ? 4 : 2.5,
      "stroke-linecap": "round",
      "data-item": JSON.stringify({ kind: "edge", qubits: [a, b] }),
    });
    const title = svg("title");
    title.textContent = options.describeEdge?.(a, b) ?? `Coupling ${a}–${b}`;
    line.append(title);
    if (options.onEdge) {
      line.style.cursor = "pointer";
      line.addEventListener("click", () => options.onEdge?.(a, b));
    }
    root.append(line);
    const mark = options.edgeMarks?.get(key);
    if (mark) {
      const mx = (pos[a][0] + pos[b][0]) / 2;
      const my = (pos[a][1] + pos[b][1]) / 2;
      const swap = cssVar("--q-swap");
      root.append(
        svg("rect", { x: mx - 15, y: my - 8, width: 30, height: 16, rx: 8, fill: bg, stroke: swap, "stroke-width": 1.5 }),
        svg("text", { x: mx, y: my + 4, "text-anchor": "middle", "font-size": 10, "font-weight": 700, fill: swap, text: mark }),
      );
    }
  }
  for (let q = 0; q < device.num_qubits; q++) {
    const strong = options.strong?.has(q);
    const fill = options.nodeFill?.(q) ?? idle;
    const node = svg("g", { "data-item": JSON.stringify({ kind: "physical_qubit", qubit: q }), tabindex: options.onQubit ? 0 : null });
    node.append(svg("circle", { cx: pos[q][0], cy: pos[q][1], r: strong ? r * 1.15 : r, fill,
      stroke: strong ? select : bg, "stroke-width": strong ? 3 : 1.5 }));
    const label = options.nodeLabel?.(q);
    const showIndex = label === undefined && (device.num_qubits <= 40 || options.zoomTo?.length);
    if (label !== undefined || showIndex) {
      node.append(svg("text", { x: pos[q][0], y: pos[q][1] + 3.5, "text-anchor": "middle",
        "font-size": label !== undefined ? 11 : 9, "font-weight": label !== undefined ? 700 : 400,
        fill: label !== undefined ? text : muted, text: label ?? String(q) }));
    }
    if (label !== undefined) {
      node.append(svg("text", { x: pos[q][0], y: pos[q][1] + r + 11, "text-anchor": "middle", "font-size": 8.5,
        fill: muted, text: String(q) }));
    }
    const title = svg("title");
    title.textContent = options.describeQubit?.(q) ?? `Qubit ${q}`;
    node.prepend(title);
    if (options.onQubit) {
      node.style.cursor = "pointer";
      node.addEventListener("click", () => options.onQubit?.(q));
      node.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); options.onQubit?.(q); }
      });
    }
    root.append(node);
  }
  return { node: root };
}

/** A sequential colour for a value in [lo, hi]; ``better`` says which end is good. */
export function ramp(value: number | null | undefined, lo: number, hi: number, lowIsGood: boolean): string | undefined {
  if (value === null || value === undefined || !Number.isFinite(value)) return undefined;
  let t = hi > lo ? (value - lo) / (hi - lo) : 0.5;
  t = Math.min(1, Math.max(0, t));
  if (!lowIsGood) t = 1 - t;
  // Good is blue, bad is orange-red; lightness keeps it readable for colour-blind readers.
  const hue = 215 - t * 195;
  const light = 52 - t * 8;
  return `hsl(${hue} 72% ${light}%)`;
}

export function rampCss(lowIsGood: boolean): string {
  const stops = [0, 0.25, 0.5, 0.75, 1].map((t) => ramp(t, 0, 1, true) as string);
  return `linear-gradient(to right, ${(lowIsGood ? stops : stops.reverse()).join(", ")})`;
}
