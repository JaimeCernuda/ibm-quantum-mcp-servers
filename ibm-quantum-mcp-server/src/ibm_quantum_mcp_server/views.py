# This code is part of Qiskit.
#
# (C) Copyright IBM 2025.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""The MCP Apps views: circuit, layout, results and backend.

One self-contained HTML bundle (``views/quantum-views.html``, built from
``packages/quantum-views``) is served four times, once per view. Each opening tool
is read-only, names its view in ``_meta.ui.resourceUri`` and returns a text summary a
host without apps can use. The view fetches what it draws through app-only data
tools (visible to the view, not to the model) and makes every operation it offers a
call to one of this server's tools.
"""

from __future__ import annotations

import asyncio
import functools
from importlib import resources
from typing import Annotated, Any

from fastmcp.apps import AppConfig, ResourceCSP
from fastmcp.tools import ToolResult
from mcp.types import TextContent
from pydantic import Field

from . import runs, view_data
from .server import _error, mcp


VIEWS = ("circuit", "layout", "results", "backend")
URI = {name: f"ui://ibm-quantum/{name}" for name in VIEWS}
MIME = "text/html;profile=mcp-app"
#: What each view reports with ui/update-model-context, for a host linking it to others.
STATE_META = "ai.iowarp.clio/state"
_SELECTION = {"key": "selection", "description": "What the person selected, with its kind."}
_JOB = {"key": "job", "description": "The job ids the view shows."}
_GATE = {
    "key": "selected_gate",
    "tool": "focus",
    "description": "The selected gate: its index, name and logical qubits.",
}
STATE = {
    "circuit": [_SELECTION, _GATE, _JOB],
    "layout": [_SELECTION, _GATE, _JOB],
    "results": [
        _SELECTION,
        {"key": "job_ids", "tool": "show_jobs", "description": "The jobs compared."},
        {"key": "parameter_set", "description": "The selected parameter set's index."},
    ],
    "backend": [_SELECTION, {"key": "backend", "description": "The device shown."}],
}


@functools.lru_cache(maxsize=1)
def _bundle() -> str:
    return (
        resources.files("ibm_quantum_mcp_server")
        .joinpath("views/quantum-views.html")
        .read_text("utf-8")
    )


def page(view: str) -> str:
    """The bundle with the view it should draw named before its script runs."""
    marker = "<head>"
    tag = f'<head><script>window.__QUANTUM_VIEW__ = "{view}";</script>'
    html = _bundle()
    return html.replace(marker, tag, 1) if marker in html else tag + html


def _register_resource(view: str) -> None:
    @mcp.resource(
        URI[view],
        name=f"ibm_quantum_{view}_view",
        description=f"The IBM Quantum {view} view.",
        mime_type=MIME,
        app=AppConfig(csp=ResourceCSP(), prefers_border=False),
    )
    def resource() -> str:
        return page(view)

    del resource


for _view in VIEWS:
    _register_resource(_view)


def _opened(view: str, text: str, request: dict[str, Any], summary: dict[str, Any]) -> ToolResult:
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content={"view": view, "request": request, "summary": summary},
    )


def _failed(exc: Exception) -> ToolResult:
    error = _error(exc)
    return ToolResult(
        content=[TextContent(type="text", text=error["message"])],
        structured_content=error,
    )


Blocks = Annotated[
    list[dict[str, Any]] | None,
    Field(
        description="Named gate ranges to shade, as {name, start, end}: gate indices in the "
        "circuit's instruction order, end excluded (e.g. the feature map and the ansatz)."
    ),
]


@mcp.tool(
    app=AppConfig(resource_uri=URI["circuit"]),
    annotations={"title": "Show a circuit", "readOnlyHint": True},
    meta={STATE_META: STATE["circuit"]},
)
async def view_circuit(
    circuit: Annotated[str | None, Field(description="OpenQASM 3 (or 2), or base64 QPY.")] = None,
    job_id: Annotated[str | None, Field(description="Show the circuit a job ran.")] = None,
    blocks: Blocks = None,
) -> ToolResult:
    """Show a circuit's wires and gates by layer, with named blocks shaded. The person can
    select a gate (it is in your next turn's context), read it as QASM 3 and analyze it."""
    try:
        data = await asyncio.to_thread(view_data.circuit_view, circuit, job_id, blocks)
    except Exception as exc:
        return _failed(exc)
    counts = data["counts"]
    text = (
        f"Opened the circuit view: {counts['num_qubits']} qubits, depth {counts['depth']}, "
        f"{counts['size']} operations, {counts['two_qubit_gates']} two-qubit gates"
        + (f", {counts['num_parameters']} parameters" if counts["num_parameters"] else "")
        + (", blocks " + ", ".join(b["name"] for b in data["blocks"]) if data["blocks"] else "")
        + "."
    )
    return _opened(
        "circuit",
        text,
        {"circuit": circuit, "job_id": job_id, "blocks": blocks},
        {"counts": counts},
    )


@mcp.tool(
    app=AppConfig(resource_uri=URI["layout"]),
    annotations={"title": "Show a circuit on a device", "readOnlyHint": True},
    meta={STATE_META: STATE["layout"]},
)
async def view_layout(
    circuit: Annotated[str | None, Field(description="OpenQASM 3 (or 2), or base64 QPY.")] = None,
    backend_name: Annotated[
        str | None, Field(description="fake_<device> (default fake_fez) or a real device.")
    ] = None,
    job_id: Annotated[str | None, Field(description="Show how a job's circuit landed.")] = None,
    optimization_level: Annotated[int, Field(ge=0, le=3)] = 1,
    initial_layout: Annotated[
        list[int] | None, Field(description="Physical qubits to place the circuit's qubits on.")
    ] = None,
    seed_transpiler: int | None = None,
) -> ToolResult:
    """Show how a circuit lands on a device: the logical circuit beside the device map with
    the physical qubits it uses and the swaps routing inserted, stepped layer by layer,
    with depth and two-qubit gates before and after."""
    try:
        data = await asyncio.to_thread(
            view_data.layout_view,
            circuit,
            job_id,
            backend_name,
            optimization_level,
            initial_layout,
            seed_transpiler,
        )
    except Exception as exc:
        return _failed(exc)
    before, after = data["before"], data["after"]
    layout = data["layout"] or {}
    text = (
        f"Opened the layout view on {data['backend']} at optimization level "
        f"{data['optimization_level']}: qubits {layout.get('initial')} at the start, "
        f"{len(data['swaps'])} swaps, depth {before['depth']} -> {after['depth']}, "
        f"two-qubit gates {before['two_qubit_gates']} -> {after['two_qubit_gates']}."
    )
    return _opened(
        "layout",
        text,
        {
            "circuit": circuit,
            "job_id": job_id,
            "backend_name": data["backend"],
            "optimization_level": data["optimization_level"],
            "initial_layout": initial_layout,
            "seed_transpiler": seed_transpiler,
        },
        {"before": before, "after": after, "layout": layout, "swaps": len(data["swaps"])},
    )


@mcp.tool(
    app=AppConfig(resource_uri=URI["results"]),
    annotations={"title": "Show job results", "readOnlyHint": True},
    meta={STATE_META: STATE["results"]},
)
async def view_results(
    job_ids: Annotated[list[str], Field(min_length=1, description="The first is the reference.")],
    labels: Annotated[list[str] | None, Field(description="A name per job.")] = None,
    expected: Annotated[
        list[int] | None, Field(description="True class labels per parameter set (0/1).")
    ] = None,
) -> ToolResult:
    """Show jobs side by side: bars per bitstring for Sampler jobs with their total variation
    distance, or a point per parameter set for Estimator jobs with the expected labels and
    each job's accuracy. The person can run the circuit on another backend from it."""
    try:
        data = await asyncio.to_thread(view_data.results_view, job_ids, labels, expected)
    except Exception as exc:
        return _failed(exc)
    text = "Opened the results view for " + ", ".join(
        f"{j['label']} ({j['job_id']})" for j in data["jobs"]
    )
    text += "." + _results_summary(data)
    return _opened(
        "results",
        text,
        {"job_ids": job_ids, "labels": labels, "expected": expected},
        {
            "jobs": [
                {k: j[k] for k in ("job_id", "label", "backend", "job_status")}
                for j in data["jobs"]
            ]
        },
    )


def _results_summary(data: dict[str, Any]) -> str:
    comparison = data.get("comparison")
    pending = [j["job_id"] for j in data["jobs"] if not j["done"]]
    lines = []
    if pending:
        lines.append(f" Not finished yet: {', '.join(pending)}.")
    if not comparison:
        return "".join(lines)
    labels = {j["job_id"]: j["label"] for j in comparison["jobs"]}
    if comparison["primitive"] == "sampler":
        first = comparison["sets"][0] if comparison["sets"] else None
        if first:
            for job_id, tvd in first["total_variation_distance"].items():
                lines.append(
                    f" {labels[job_id]}: total variation distance {tvd:.3f} from the reference."
                )
            top = max(first["probabilities"], key=lambda r: r[comparison["jobs"][0]["job_id"]])
            lines.append(f" Most likely bitstring: {top['bitstring']}.")
        return "".join(lines)
    sets = comparison["sets"]
    ref = comparison["jobs"][0]["job_id"]
    if sets:
        values = [(s["index"], s["jobs"][ref]["values"][0]) for s in sets]
        values = [v for v in values if v[1] is not None]
        if values:
            hi = max(values, key=lambda v: v[1])
            lo = min(values, key=lambda v: v[1])
            lines.append(
                f" {comparison['num_sets']} parameter sets; highest value {hi[1]:.3f} (set {hi[0]}), "
                f"lowest {lo[1]:.3f} (set {lo[0]})."
            )
    cls = comparison.get("classification")
    if cls:
        lines.append(
            " Accuracy: "
            + ", ".join(
                f"{labels[j]} {a:.0%}" if a is not None else f"{labels[j]} n/a"
                for j, a in cls["accuracy"].items()
            )
            + "."
        )
        for job_id, flips in cls["flipped_from_reference"].items():
            if flips:
                lines.append(f" {labels[job_id]} flipped sets {flips} from the reference.")
    return "".join(lines)


@mcp.tool(
    app=AppConfig(resource_uri=URI["backend"]),
    annotations={"title": "Show a device", "readOnlyHint": True},
    meta={STATE_META: STATE["backend"]},
)
async def view_backend(
    backend_name: Annotated[str, Field(description="fake_<device> or a real device.")],
) -> ToolResult:
    """Show a device's qubit map colored by T1, T2, readout error or two-qubit error, with its
    queue and, with an API key, the instance's time left."""
    try:
        data = await asyncio.to_thread(view_data.backend_view, backend_name)
    except Exception as exc:
        return _failed(exc)
    edges = [m["two_qubit_error"] for m in data["edge_metrics"] if m["two_qubit_error"] is not None]
    t1 = [q["t1_us"] for q in data["qubit_metrics"] if q["t1_us"] is not None]
    text = (
        f"Opened the backend view for {data['name']}: {data['num_qubits']} qubits, "
        f"{len(data['edges'])} couplings ({data['two_qubit_gate']})"
        + (f", median two-qubit error {sorted(edges)[len(edges) // 2]:.4f}" if edges else "")
        + (f", median T1 {sorted(t1)[len(t1) // 2]:.0f} µs" if t1 else "")
        + (f", queue {data['queue']}" if data.get("queue") is not None else "")
        + (
            f", {data['time_left_seconds']:g} s of quantum time left"
            if data.get("time_left_seconds") is not None
            else ""
        )
        + "."
    )
    return _opened(
        "backend", text, {"backend_name": backend_name}, {"num_qubits": data["num_qubits"]}
    )


# --- App-only data tools ------------------------------------------------------------

#: App-only tools: visible to the view, not to the model.
_APP_ONLY = AppConfig(visibility=["app"])


def _data(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(
        content=[TextContent(type="text", text="View data.")], structured_content=payload
    )


@mcp.tool(app=_APP_ONLY, annotations={"readOnlyHint": True})
async def view_data_circuit(
    circuit: str | None = None, job_id: str | None = None, blocks: Blocks = None
) -> ToolResult:
    """The circuit view's data: qubits, clbits, gate layers with parameters, and blocks."""
    try:
        return _data(await asyncio.to_thread(view_data.circuit_view, circuit, job_id, blocks))
    except Exception as exc:
        return _failed(exc)


@mcp.tool(app=_APP_ONLY, annotations={"readOnlyHint": True})
async def view_data_layout(
    circuit: str | None = None,
    job_id: str | None = None,
    backend_name: str | None = None,
    optimization_level: int = 1,
    initial_layout: list[int] | None = None,
    seed_transpiler: int | None = None,
) -> ToolResult:
    """The layout view's data: logical and routed circuits, layout, swaps, device map."""
    try:
        return _data(
            await asyncio.to_thread(
                view_data.layout_view,
                circuit,
                job_id,
                backend_name,
                optimization_level,
                initial_layout,
                seed_transpiler,
            )
        )
    except Exception as exc:
        return _failed(exc)


@mcp.tool(app=_APP_ONLY, annotations={"readOnlyHint": True})
async def view_data_results(
    job_ids: list[str], labels: list[str] | None = None, expected: list[int] | None = None
) -> ToolResult:
    """The results view's data: the jobs' aligned results from compare_jobs."""
    try:
        return _data(await asyncio.to_thread(view_data.results_view, job_ids, labels, expected))
    except Exception as exc:
        return _failed(exc)


@mcp.tool(app=_APP_ONLY, annotations={"readOnlyHint": True})
async def view_data_backend(backend_name: str) -> ToolResult:
    """The backend view's data: the coupling map with positions and calibration metrics."""
    try:
        return _data(await asyncio.to_thread(view_data.backend_view, backend_name))
    except Exception as exc:
        return _failed(exc)


del runs
