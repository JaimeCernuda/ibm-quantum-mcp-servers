// Small DOM and SVG helpers, and the picture of a view (capture and regions).

export const SVG_NS = "http://www.w3.org/2000/svg";

type Attrs = Record<string, string | number | boolean | null | undefined>;

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Attrs = {},
  ...children: (Node | string | null | undefined)[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") node.textContent = String(v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const child of children) if (child !== null && child !== undefined) node.append(child);
  return node;
}

export function svg<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Attrs = {},
  ...children: (Node | null | undefined)[]
): SVGElementTagNameMap[K] {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") node.textContent = String(v);
    else node.setAttribute(k, String(v));
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

export function button(label: string, onClick: () => void, attrs: Attrs = {}): HTMLButtonElement {
  const b = el("button", { type: "button", ...attrs }, label);
  b.addEventListener("click", onClick);
  return b;
}

/** Run an operation with its button disabled and its failure shown in words. */
export async function busy(b: HTMLButtonElement, work: () => Promise<void>, onError: (m: string) => void): Promise<void> {
  b.disabled = true;
  try {
    await work();
  } catch (error) {
    onError(String((error as Error)?.message ?? error));
  } finally {
    b.disabled = false;
  }
}

export function fmt(value: unknown, digits = 3): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "n/a";
  if (value !== 0 && Math.abs(value) < 10 ** -digits) return value.toExponential(1);
  return value.toFixed(digits).replace(/\.?0+$/, "") || "0";
}

// --- Pictures ------------------------------------------------------------------------

async function svgImage(node: SVGSVGElement): Promise<HTMLImageElement> {
  const clone = node.cloneNode(true) as SVGSVGElement;
  const rect = node.getBoundingClientRect();
  clone.setAttribute("xmlns", SVG_NS);
  clone.setAttribute("width", String(rect.width));
  clone.setAttribute("height", String(rect.height));
  const blob = new Blob([new XMLSerializer().serializeToString(clone)], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.decoding = "sync";
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("The view's drawing could not be read."));
      image.src = url;
    });
    return image;
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

/** A PNG of what a root element shows: its SVGs and canvases at their places, on the
 *  page background, optionally cropped to a rectangle in the page's CSS pixels. */
export async function picture(
  root: HTMLElement,
  crop?: { x: number; y: number; width: number; height: number },
  scale = 2,
): Promise<{ data: string; width: number; height: number }> {
  const bounds = crop ?? root.getBoundingClientRect();
  const width = Math.max(1, Math.round(bounds.width));
  const height = Math.max(1, Math.round(bounds.height));
  const canvas = document.createElement("canvas");
  canvas.width = width * scale;
  canvas.height = height * scale;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("No canvas to draw the picture on.");
  ctx.scale(scale, scale);
  ctx.fillStyle = getComputedStyle(document.body).backgroundColor || "#fff";
  ctx.fillRect(0, 0, width, height);
  const items = root.querySelectorAll<SVGSVGElement | HTMLCanvasElement>("svg, canvas");
  for (const item of items) {
    if (item instanceof SVGSVGElement && item.ownerSVGElement) continue; // nested svg
    const r = item.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const dx = r.left - bounds.x;
    const dy = r.top - bounds.y;
    if (item instanceof HTMLCanvasElement) ctx.drawImage(item, dx, dy, r.width, r.height);
    else ctx.drawImage(await svgImage(item), dx, dy, r.width, r.height);
  }
  const url = canvas.toDataURL("image/png");
  return { data: url.slice(url.indexOf(",") + 1), width, height };
}

export interface Region {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function regionOf(args: Record<string, unknown>): Region {
  const source = (args.rect ?? args.region ?? args) as Record<string, unknown>;
  const n = (k: string) => Number(source[k]);
  const region = { x: n("x"), y: n("y"), width: n("width"), height: n("height") };
  if (![region.x, region.y, region.width, region.height].every(Number.isFinite)) {
    throw new Error("describe_region takes the mark's rectangle as x, y, width and height in CSS pixels.");
  }
  return region;
}

/** The elements marked with data-item whose boxes meet a region. */
export function itemsIn(root: HTMLElement, region: Region): Element[] {
  const out: Element[] = [];
  for (const node of root.querySelectorAll("[data-item]")) {
    const r = node.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const meets =
      r.left < region.x + region.width && r.right > region.x && r.top < region.y + region.height && r.bottom > region.y;
    if (meets) out.push(node);
  }
  return out;
}
