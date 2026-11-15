// The page's side of MCP Apps: connecting to the host, theming, calling the server's
// tools, reporting context (at most ten times a second) and answering app tools.
import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
} from "@modelcontextprotocol/ext-apps/app-with-deps";

export type Json = Record<string, unknown>;

export interface ToolResultLike {
  content?: { type: string; text?: string; data?: string; mimeType?: string }[];
  structuredContent?: Json;
  isError?: boolean;
}

export interface AppTool {
  name: string;
  description: string;
  inputSchema: Json;
  run: (args: Json) => Promise<ToolResultLike>;
}

export const app = new App(
  { name: "ibm-quantum-views", version: "0.1.0" },
  { tools: { listChanged: true }, availableDisplayModes: ["inline", "fullscreen"] },
);

let tools: AppTool[] = [];

/** Set the app tools this view offers the agent (focus, capture, describe_region…). */
export function setAppTools(next: AppTool[]): void {
  tools = next;
  void app.sendToolListChanged().catch(() => undefined);
}

app.onlisttools = async () => ({
  tools: tools.map((t) => ({ name: t.name, description: t.description, inputSchema: t.inputSchema as never })),
});

app.oncalltool = async (params) => {
  const tool = tools.find((t) => t.name === params.name);
  if (!tool) return errorResult(`This view has no tool ${params.name}.`) as never;
  try {
    return (await tool.run((params.arguments ?? {}) as Json)) as never;
  } catch (error) {
    return errorResult(String((error as Error)?.message ?? error)) as never;
  }
};

export function textResult(text: string, structuredContent?: Json): ToolResultLike {
  return { content: [{ type: "text", text }], ...(structuredContent ? { structuredContent } : {}) };
}

export function errorResult(text: string): ToolResultLike {
  return { content: [{ type: "text", text }], isError: true };
}

/** Call one of the server's tools. A failed call throws with the server's own words. */
export async function callServer(name: string, args: Json): Promise<Json> {
  const result = (await app.callServerTool({ name, arguments: args })) as ToolResultLike;
  const data = (result.structuredContent ?? {}) as Json;
  const text = (result.content ?? []).map((c) => c.text ?? "").join("\n");
  if (result.isError || data.status === "error") {
    throw new Error((data.message as string) || text || `${name} failed.`);
  }
  return data;
}

// --- Theme ---------------------------------------------------------------------------

export function applyTheme(): void {
  const context = app.getHostContext();
  if (context?.theme) {
    applyDocumentTheme(context.theme);
    document.documentElement.dataset.theme = context.theme;
  }
  const variables = context?.styles?.variables;
  if (variables) applyHostStyleVariables(variables);
  for (const listener of themeListeners) listener();
}

const themeListeners: (() => void)[] = [];
export function onThemeChange(listener: () => void): void {
  themeListeners.push(listener);
}

/** A CSS variable's current value, for drawing code that writes colours inline. */
export function cssVar(name: string): string {
  const probe = document.createElement("span");
  probe.style.color = `var(${name})`;
  probe.style.display = "none";
  document.body.appendChild(probe);
  const value = getComputedStyle(probe).color;
  probe.remove();
  return value || "#888";
}

// --- Reported context ----------------------------------------------------------------

const reported: Json = {};
const reportedText: Record<string, string> = {};
let timer: number | undefined;
let lastSent = 0;

/** Report what the view shows and what the person selected. Keys merge; the last value
 *  of each wins; sends are at most ten a second. */
export function report(values: Json, text?: Record<string, string | null>): void {
  Object.assign(reported, values);
  for (const [key, line] of Object.entries(text ?? {})) {
    if (line === null) delete reportedText[key];
    else reportedText[key] = line;
  }
  if (timer !== undefined) return;
  const wait = Math.max(0, 100 - (Date.now() - lastSent));
  timer = window.setTimeout(() => {
    timer = undefined;
    lastSent = Date.now();
    const lines = Object.values(reportedText);
    void app
      .updateModelContext({
        structuredContent: { ...reported },
        ...(lines.length ? { content: [{ type: "text", text: lines.join("\n") }] } : {}),
      })
      .catch(() => undefined);
  }, wait);
}

export function reportedValue(key: string): unknown {
  return reported[key];
}

// --- Start ----------------------------------------------------------------------------

export async function start(onResult: (params: ToolResultLike) => void, onInput?: (args: Json) => void): Promise<void> {
  app.ontoolresult = (params) => onResult(params as ToolResultLike);
  app.ontoolinput = (params) => onInput?.((params.arguments ?? {}) as Json);
  app.ontoolcancelled = () => showStatus("The call that opens this view was cancelled.", true);
  app.onhostcontextchanged = () => applyTheme();
  app.onteardown = async () => ({});
  await app.connect();
  applyTheme();
  app.setupSizeChangedNotifications();
}

export function showStatus(text: string, error = false): void {
  const root = document.getElementById("app");
  if (!root) return;
  root.replaceChildren();
  const p = document.createElement("p");
  p.className = error ? "status error" : "status";
  p.textContent = text;
  root.appendChild(p);
}
