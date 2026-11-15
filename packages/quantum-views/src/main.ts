// The IBM Quantum views: one page, served as the circuit, layout, results and backend views.
import "./style.css";
import { showStatus, start, type Json, type ToolResultLike } from "./host";
import { backendView } from "./views/backend";
import { circuitView } from "./views/circuit";
import { layoutView } from "./views/layout";
import { resultsView } from "./views/results";

declare global {
  interface Window {
    __QUANTUM_VIEW__?: string;
  }
}

const VIEWS: Record<string, (request: Json) => Promise<void>> = {
  circuit: circuitView,
  layout: layoutView,
  results: resultsView,
  backend: backendView,
};

async function open(params: ToolResultLike): Promise<void> {
  const data = (params.structuredContent ?? {}) as Json;
  if (params.isError || data.status === "error") {
    const text = (params.content ?? []).map((c) => c.text ?? "").join("\n");
    showStatus((data.message as string) || text || "The view could not be opened.", true);
    return;
  }
  const name = String(data.view ?? window.__QUANTUM_VIEW__ ?? "");
  const view = VIEWS[name];
  if (!view) {
    showStatus(`This page has no view named ${name || "(none)"}.`, true);
    return;
  }
  try {
    await view((data.request ?? {}) as Json);
  } catch (error) {
    showStatus(String((error as Error)?.message ?? error), true);
  }
}

start((params) => void open(params)).catch((error) => {
  showStatus(`The host did not start the view: ${String((error as Error)?.message ?? error)}`, true);
});
