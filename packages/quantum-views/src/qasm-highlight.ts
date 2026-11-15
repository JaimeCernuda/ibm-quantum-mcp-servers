// OpenQASM source coloured by token kind, for the circuit view's QASM panel. The view
// carries no highlighter library; each token is a span with a class style.css colours.

type Kind = "comment" | "string" | "keyword" | "type" | "gate" | "constant" | "number" | "qubit";

const words = (list: string): string => `(?:${list.split(" ").join("|")})`;

const KEYWORDS = words(
  "OPENQASM include defcalgrammar def defcal cal gate opaque extern box let input output const " +
    "readonly mutable if else for in while break continue return end switch case default " +
    "measure reset barrier delay stretch durationof nop ctrl negctrl inv pow",
);
const TYPES = words("qubit qreg bit creg bool int uint float angle complex duration array void");
const CONSTANTS = words("pi π tau τ euler ℇ true false im");

// One alternation, one capture group per kind, tried left to right at each position.
const TOKEN = new RegExp(
  [
    String.raw`(\/\/[^\n]*|\/\*[\s\S]*?\*\/)`,
    String.raw`("[^"\n]*"|'[^'\n]*')`,
    String.raw`(?<![\w$])(${KEYWORDS})(?![\w$])`,
    String.raw`(?<![\w$])(${TYPES})(?![\w$])`,
    String.raw`(?<![\w$])(${CONSTANTS})(?![\w$])`,
    String.raw`(\$\d+)`,
    String.raw`(?<![\w.])(\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d+)?(?:ns|us|µs|ms|s|dt|im)?)(?![\w.])`,
    // A name called with arguments or applied to qubits: `h q[0]`, `rz(0.5) q`.
    String.raw`(?<![\w$.])([A-Za-z_]\w*)(?=\s*\(|\s+(?:\$\d|[A-Za-z_]\w*\s*(?:\[|,|;)))`,
  ].join("|"),
  "gu",
);
const KINDS: Kind[] = ["comment", "string", "keyword", "type", "constant", "qubit", "number", "gate"];

/** The source as text and coloured spans, in order, so its text reads back unchanged. */
export function highlightQasm(source: string): Node[] {
  const nodes: Node[] = [];
  let last = 0;
  for (const match of source.matchAll(TOKEN)) {
    const at = match.index ?? 0;
    const group = match.slice(1).findIndex((part) => part !== undefined);
    if (group < 0) continue;
    if (at > last) nodes.push(document.createTextNode(source.slice(last, at)));
    const span = document.createElement("span");
    span.className = `qasm-${KINDS[group]}`;
    span.textContent = match[0];
    nodes.push(span);
    last = at + match[0].length;
  }
  if (last < source.length) nodes.push(document.createTextNode(source.slice(last)));
  return nodes;
}
