// What every view shares: its frame, the capture and describe_region app tools.
import { el, itemsIn, picture, regionOf } from "../dom";
import type { AppTool, Json, ToolResultLike } from "../host";

export interface ViewShell {
  root: HTMLElement;
  header: HTMLElement;
  facts: HTMLElement;
  tools: HTMLElement;
  selection: HTMLElement;
  body: HTMLElement;
  message: (text: string, error?: boolean) => void;
}

export function shell(title: string): ViewShell {
  const root = document.getElementById("app") as HTMLElement;
  root.replaceChildren();
  const facts = el("div", { class: "facts" });
  const tools = el("div", { class: "tools", role: "toolbar", "aria-label": `${title} operations` });
  const header = el("header", { class: "bar" }, el("h1", { text: title }), tools);
  const selection = el("div", { class: "selection", "aria-live": "polite" });
  const note = el("div", { class: "selection", "aria-live": "polite" });
  const body = el("div", { class: "body" });
  root.append(header, facts, selection, body, note);
  return {
    root, header, facts, tools, selection, body,
    message: (text, error = false) => {
      note.textContent = text;
      note.style.color = error ? "var(--q-swap)" : "";
    },
  };
}

export function setFacts(target: HTMLElement, facts: [string, string | number][]): void {
  target.replaceChildren(
    ...facts.map(([label, value]) => el("span", {}, `${label} `, el("b", { text: String(value) }))),
  );
}

/** capture and describe_region over the view's drawing. */
export interface Described {
  /** What the agent reads. */
  text: string;
  /** What lies under the mark, one list per kind (gates, qubits, couplings…); a host lists
   *  each key on its own line. */
  under: Json;
}

export function pictureTools(
  root: () => HTMLElement,
  describe: (items: Json[]) => Described,
): AppTool[] {
  return [
    {
      name: "capture",
      description: "A PNG of what the view shows now.",
      inputSchema: { type: "object", properties: {} },
      run: async (): Promise<ToolResultLike> => {
        const shot = await picture(root());
        return {
          content: [
            { type: "image", data: shot.data, mimeType: "image/png" },
            { type: "text", text: `The view as shown, ${shot.width}×${shot.height} CSS pixels.` },
          ],
        };
      },
    },
    {
      name: "describe_region",
      description:
        "What lies under a mark on the view: the rectangle in the view's CSS pixels (x, y, width, height). " +
        "Returns a PNG of the region and a list of the items under it.",
      inputSchema: {
        type: "object",
        properties: {
          x: { type: "number" }, y: { type: "number" }, width: { type: "number" }, height: { type: "number" },
        },
        required: ["x", "y", "width", "height"],
      },
      run: async (args): Promise<ToolResultLike> => {
        const region = regionOf(args);
        const items = itemsIn(root(), region).map((node) => JSON.parse(node.getAttribute("data-item") ?? "{}") as Json);
        const described = describe(dedupe(items));
        const shot = await picture(root(), region);
        return {
          content: [
            { type: "image", data: shot.data, mimeType: "image/png" },
            { type: "text", text: described.text },
          ],
          structuredContent: described.under,
        };
      },
    },
  ];
}

function dedupe(items: Json[]): Json[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = JSON.stringify(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
