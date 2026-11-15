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

"""IBM Quantum MCP server: circuits, runtime and docs in one process.

It mounts ``qiskit-mcp-server``, ``qiskit-ibm-runtime-mcp-server`` and
``qiskit-docs-mcp-server`` (which keep running standalone too), serves their tools
without the ``_tool`` suffix, and replaces the run and job tools with ones that also
run on this machine: exactly, with ideal shots, or with a real device's noise.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.server.providers.fastmcp_provider import FastMCPProvider
from fastmcp.server.transforms import ToolTransform
from fastmcp.tools.tool_transform import ToolTransformConfig
from pydantic import Field

from . import backends, devices, hardware, routing, runs


logger = logging.getLogger(__name__)

VERSION = "0.1.0"

INSTRUCTIONS = """\
Write Qiskit circuits, simulate them on this machine, see them, and run them on IBM \
quantum computers.

Where a circuit runs is backend_name:
- local_exact: exact results (statevector). No shots for the Estimator.
- local_aer: ideal shots on Aer. The default.
- fake_<device> (fake_fez, a Heron r2): shots with that device's noise, transpiled as \
the device would be.
- any other name: the real device. Needs the IBM Quantum API key, and reads the time \
left before submitting.

Every run returns a job_id (local- for local jobs). Local jobs usually finish within \
the call; otherwise read them with get_job_results. compare_jobs lines up jobs that \
ran the same circuit on different backends. A batch of inputs is one call: give \
parameter_values as a list of parameter sets.\
"""

mcp: FastMCP = FastMCP("ibm-quantum", version=VERSION, instructions=INSTRUCTIONS)

#: Mounted tools this server replaces with local-aware versions of the same name.
REPLACED_RUNTIME = {
    "run_sampler_tool",
    "run_estimator_tool",
    "get_job_status_tool",
    "get_job_results_tool",
    "list_my_jobs_tool",
    "cancel_job_tool",
    "usage_info_tool",
    "list_backends_tool",
    "least_busy_backend_tool",
    "get_backend_properties_tool",
    "get_backend_calibration_tool",
    "find_optimal_qubit_chains_tool",
}
REPLACED_QISKIT = {"transpile_circuit_tool", "compare_optimization_levels_tool"}
#: Account tools that read or write ~/.qiskit; not served while saved accounts are off.
SAVED_ACCOUNT_TOOLS = {
    "setup_ibm_quantum_account_tool",
    "delete_saved_account_tool",
    "list_saved_accounts_tool",
}


def _mount(server: FastMCP, hidden: set[str]) -> None:
    """Mount a server with its tools renamed from ``name_tool`` to ``name``."""
    names = _tool_names(server)
    renames = {n: n[: -len("_tool")] for n in names if n.endswith("_tool") and n not in hidden}
    provider: Any = FastMCPProvider(server)
    if renames:
        provider = provider.wrap_transform(
            ToolTransform({old: ToolTransformConfig(name=new) for old, new in renames.items()})
        )
    if hidden:
        provider.disable(names=hidden & set(names))
    mcp.add_provider(provider)


def _tool_names(server: FastMCP) -> list[str]:
    """The names of the tools a server defines, read without running an event loop."""
    from fastmcp.tools import Tool

    components = server._local_provider._components.values()
    return [c.name for c in components if isinstance(c, Tool)]


def _mount_all() -> None:
    from qiskit_docs_mcp_server.server import mcp as docs_server
    from qiskit_mcp_server.server import mcp as qiskit_server

    from qiskit_ibm_runtime_mcp_server import ibm_runtime
    from qiskit_ibm_runtime_mcp_server.server import mcp as runtime_server

    hidden_runtime = set(REPLACED_RUNTIME)
    if ibm_runtime.saved_accounts_disabled():
        hidden_runtime |= SAVED_ACCOUNT_TOOLS
    _mount(qiskit_server, set(REPLACED_QISKIT))
    _mount(runtime_server, hidden_runtime)
    _mount(docs_server, set())


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, (runs.RunError, hardware.HardwareRefusal)):
        return exc.as_result()
    logger.exception("tool failed")
    return {"status": "error", "reason": "failed", "message": f"{type(exc).__name__}: {exc}"}


BackendName = Annotated[
    str | None,
    Field(
        description="local_exact, local_aer (the default), fake_<device> such as fake_fez, "
        "or a real device's name (needs the API key)."
    ),
]
CircuitArg = Annotated[
    str,
    Field(description="The circuit as OpenQASM 3 (or 2), or base64 QPY."),
]
ParameterValues = Annotated[
    list[float] | list[list[float]] | None,
    Field(
        description="Values for the circuit's parameters in the order of circuit.parameters "
        "(sorted by name): one set as a list of numbers, or many sets as a list of lists. "
        "Many sets run as one job."
    ),
]
WaitSeconds = Annotated[
    float,
    Field(
        ge=0,
        le=600,
        description="For a local job, how long to wait for its results within this call.",
    ),
]


@mcp.tool(annotations={"title": "Run the Sampler"})
async def run_sampler(
    circuit: CircuitArg,
    backend_name: BackendName = None,
    shots: Annotated[int, Field(ge=1, description="Shots per parameter set.")] = 1000,
    parameter_values: ParameterValues = None,
    circuit_format: Literal["auto", "qasm3", "qpy"] = "auto",
    optimization_level: Annotated[int, Field(ge=0, le=3)] = 1,
    seed: Annotated[int | None, Field(description="Simulator seed, for a local run.")] = None,
    wait_seconds: WaitSeconds = 60,
    label: Annotated[str | None, Field(description="A name for this job in comparisons.")] = None,
    dynamical_decoupling: bool = True,
    dd_sequence: Literal["XX", "XpXm", "XY4"] = "XY4",
    twirling: bool = True,
    measure_twirling: bool = True,
) -> dict[str, Any]:
    """Sample a measured circuit and count its bitstrings, per parameter set.

    Runs locally unless backend_name names a real device. A local job returns its
    counts in this call when it finishes within wait_seconds. A fake_<device> run is
    transpiled for that device first; the result carries the transpiled circuit's
    two-qubit gate count and layout. Error-suppression options apply on hardware only.
    """
    try:
        return await runs.run_sampler(
            circuit,
            backend_name,
            shots,
            parameter_values,
            circuit_format,
            optimization_level,
            seed,
            wait_seconds,
            label,
            {
                "dynamical_decoupling": dynamical_decoupling,
                "dd_sequence": dd_sequence,
                "twirling": twirling,
                "measure_twirling": measure_twirling,
            },
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "Run the Estimator"})
async def run_estimator(
    circuit: CircuitArg,
    observables: Annotated[
        str | list[Any],
        Field(
            description="A Pauli string ('ZZ'; the rightmost letter is qubit 0), a list of "
            "them (one value each), or a weighted sum as [[pauli, coefficient], ...]."
        ),
    ],
    backend_name: BackendName = None,
    parameter_values: ParameterValues = None,
    circuit_format: Literal["auto", "qasm3", "qpy"] = "auto",
    optimization_level: Annotated[int, Field(ge=0, le=3)] = 1,
    precision: Annotated[
        float | None,
        Field(gt=0, description="Target standard error for shot-based runs (default 1/64)."),
    ] = None,
    seed: Annotated[int | None, Field(description="Simulator seed, for a local run.")] = None,
    wait_seconds: WaitSeconds = 60,
    label: Annotated[str | None, Field(description="A name for this job in comparisons.")] = None,
    resilience_level: Annotated[int, Field(ge=0, le=2, description="Hardware only.")] = 1,
    zne_mitigation: Annotated[
        bool, Field(description="Zero-noise extrapolation, hardware only; triples the shots.")
    ] = False,
    zne_noise_factors: list[float] | None = None,
) -> dict[str, Any]:
    """Estimate expectation values of observables, per parameter set and observable.

    Every parameter set is measured against every observable, in one job. Final
    measurements in the circuit are removed. local_exact gives exact values with zero
    standard error; shot-based backends give standard errors.
    """
    try:
        return await runs.run_estimator(
            circuit,
            observables,
            backend_name,
            parameter_values,
            circuit_format,
            optimization_level,
            precision,
            seed,
            wait_seconds,
            label,
            {
                "resilience_level": resilience_level,
                "zne_mitigation": zne_mitigation,
                "zne_noise_factors": zne_noise_factors,
            },
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "Job status", "readOnlyHint": True})
async def get_job_status(job_id: str) -> dict[str, Any]:
    """The status of a local or hardware job (QUEUED, RUNNING, DONE, ERROR, CANCELLED)."""
    try:
        out = await runs.get_job(job_id)
        out.pop("result", None)
        return out
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "Job results", "readOnlyHint": True})
async def get_job_results(job_id: str) -> dict[str, Any]:
    """A job's results: counts and quasi-probabilities per parameter set for a Sampler job,
    expectation values with standard errors per set for an Estimator job. A hardware job
    also says the quantum seconds it used and the seconds left."""
    try:
        return await runs.get_job(job_id)
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "List jobs", "readOnlyHint": True})
async def list_my_jobs(limit: Annotated[int, Field(ge=1, le=100)] = 10) -> dict[str, Any]:
    """Recent jobs: local jobs first, then hardware jobs when an API key is set."""
    try:
        return await runs.list_jobs(limit)
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "Cancel a job"})
async def cancel_job(job_id: str) -> dict[str, Any]:
    """Cancel a queued or running job, local or hardware."""
    try:
        return await runs.cancel(job_id)
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "Compare jobs", "readOnlyHint": True})
async def compare_jobs(
    job_ids: Annotated[list[str], Field(min_length=1, description="The first is the reference.")],
    labels: Annotated[list[str] | None, Field(description="A name per job.")] = None,
    expected: Annotated[
        list[int] | None,
        Field(description="True class labels (0/1 or -1/+1) per parameter set, for a classifier."),
    ] = None,
    threshold: Annotated[
        float, Field(description="A value at or above it predicts class 1.")
    ] = 0.0,
    positive_above: bool = True,
) -> dict[str, Any]:
    """Line up jobs that ran the same circuit on different backends.

    Sampler jobs: each bitstring's probability per job, and the total variation
    distance from the reference. Estimator jobs: values per parameter set per job and
    their differences from the reference; with expected labels, accuracy per job and
    the sets whose predicted class flipped from the reference.
    """
    try:
        return await runs.compare(job_ids, labels, expected, threshold, positive_above)
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "Record a job run elsewhere"})
async def record_job(
    circuit: CircuitArg,
    primitive: Literal["sampler", "estimator"],
    backend_name: Annotated[
        str, Field(description="Where it ran, such as local_aer or fake_fez.")
    ] = "local_aer",
    counts: Annotated[
        dict[str, int] | list[dict[str, int]] | None,
        Field(description="Sampler: counts, or one counts dict per parameter set."),
    ] = None,
    values: Annotated[
        list[float] | list[list[float]] | None,
        Field(description="Estimator: one value per set, or a list per set (one per observable)."),
    ] = None,
    standard_errors: list[float] | list[list[float]] | None = None,
    parameter_values: ParameterValues = None,
    observables: str | list[str] | None = None,
    label: str | None = None,
    circuit_format: Literal["auto", "qasm3", "qpy"] = "auto",
) -> dict[str, Any]:
    """Keep a job you ran yourself (in a script, with the Qiskit SDK on a simulator) as a
    local job, so the views and compare_jobs can use it. Returns its local- job id."""
    try:
        return runs.record(
            circuit,
            primitive,
            backend_name,
            counts,
            values,
            standard_errors,
            parameter_values,
            observables,
            label,
            circuit_format,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "Time left on the instance", "readOnlyHint": True})
async def usage_info() -> dict[str, Any]:
    """The IBM Quantum instance's time limit, consumed and remaining seconds. Read it before
    any hardware run; only execution time counts."""
    try:
        svc = await asyncio.to_thread(hardware.service, "Reading the time left")
        return {"status": "success", **await asyncio.to_thread(hardware.budget, svc)}
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "List backends", "readOnlyHint": True})
async def list_backends(
    include_fake: Annotated[bool, Field(description="List every fake device.")] = True,
) -> dict[str, Any]:
    """Backends to run on: the local simulators and fake devices (always), and the real
    devices of the instance when an API key is set."""
    out: dict[str, Any] = {
        "status": "success",
        "local": [
            {"name": backends.LOCAL_EXACT, "kind": "exact", "description": "Exact statevector"},
            {"name": backends.LOCAL_AER, "kind": "aer", "description": "Ideal shots on Aer"},
        ],
        "default_fake_backend": backends.DEFAULT_FAKE_BACKEND,
    }
    if include_fake:
        out["fake"] = [
            {"name": n, "num_qubits": backends.fake_backend(n).num_qubits}
            for n in backends.fake_backend_names()
        ]
    if hardware.has_key():
        from qiskit_ibm_runtime_mcp_server import ibm_runtime

        listed = await ibm_runtime.list_backends()
        out["hardware"] = listed.get("backends", [])
        if listed.get("status") == "error":
            out["hardware_note"] = listed.get("message")
    else:
        out["hardware_note"] = "No API key is set, so no real devices are listed."
    return out


@mcp.tool(annotations={"title": "Least busy device", "readOnlyHint": True})
async def least_busy_backend() -> dict[str, Any]:
    """The real device with the shortest queue on the instance. Needs the API key."""
    try:
        await asyncio.to_thread(hardware.service, "Finding the least busy device")
        from qiskit_ibm_runtime_mcp_server import ibm_runtime

        return await ibm_runtime.least_busy_backend()
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "Backend properties", "readOnlyHint": True})
async def get_backend_properties(backend_name: str) -> dict[str, Any]:
    """A backend's qubits, basis gates and coupling map; for a real device, its status and
    queue too."""
    try:
        kind, name = backends.classify(backend_name)
        if kind in ("exact", "aer"):
            return {"status": "success", "backend_name": name, "kind": kind, "simulator": True}
        if kind == "fake":
            fake = backends.fake_backend(name)
            return {
                "status": "success",
                **backends.fake_backend_summary(name),
                "coupling_map": [list(e) for e in devices.edges_of(fake)],
            }
        await asyncio.to_thread(hardware.service, "Reading a device")
        from qiskit_ibm_runtime_mcp_server import ibm_runtime

        return await ibm_runtime.get_backend_properties(name)
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "Backend calibration", "readOnlyHint": True})
async def get_backend_calibration(
    backend_name: str,
    qubit_indices: Annotated[
        list[int] | None, Field(description="Qubits to report; default the first 10.")
    ] = None,
) -> dict[str, Any]:
    """T1, T2, readout and gate errors per qubit, and two-qubit errors per coupling. A fake
    device reports the calibration snapshot it ships with."""
    try:
        kind, name = backends.classify(backend_name)
        if kind == "fake":
            return devices.calibration(backends.fake_backend(name), name, qubit_indices)
        if kind != "hardware":
            return {
                "status": "error",
                "reason": "no_calibration",
                "message": f"{name} is an ideal simulator; it has no calibration.",
            }
        await asyncio.to_thread(hardware.service, "Reading a device's calibration")
        from qiskit_ibm_runtime_mcp_server import ibm_runtime

        return await ibm_runtime.get_backend_calibration(name, qubit_indices)
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "Find good qubit chains", "readOnlyHint": True})
async def find_optimal_qubit_chains(
    backend_name: str,
    chain_length: Annotated[int, Field(ge=2, le=20)] = 5,
    num_results: Annotated[int, Field(ge=1, le=20)] = 5,
    metric: Literal["two_qubit_error", "readout_error", "combined"] = "two_qubit_error",
) -> dict[str, Any]:
    """Linear chains of connected qubits ranked by their errors, on a real or fake device."""
    try:
        kind, name = backends.classify(backend_name)
        if kind == "fake":
            return devices.qubit_chains(
                backends.fake_backend(name), name, chain_length, num_results, metric
            )
        if kind != "hardware":
            return {
                "status": "error",
                "reason": "no_calibration",
                "message": f"{name} is an ideal simulator; it has no qubits to rank.",
            }
        await asyncio.to_thread(hardware.service, "Ranking a device's qubits")
        from qiskit_ibm_runtime_mcp_server import ibm_runtime

        return await ibm_runtime.find_optimal_qubit_chains(name, chain_length, num_results, metric)
    except Exception as exc:
        return _error(exc)


def device_for(name: str) -> Any:
    """A fake device, or a real one through the configured key."""
    kind, name = backends.classify(name)
    if kind == "fake":
        return backends.fake_backend(name)
    if kind != "hardware":
        raise runs.RunError(
            "not_a_device", f"{name} is an ideal simulator, not a device to transpile for."
        )
    return hardware.device(hardware.service("Transpiling for a real device"), name)


@mcp.tool(annotations={"title": "Transpile a circuit", "readOnlyHint": True})
async def transpile_circuit(
    circuit: CircuitArg,
    backend_name: Annotated[
        str | None,
        Field(
            description="Transpile for this device (fake_<device> or a real one), as a run "
            "on it would be. Without it, basis_gates and coupling_map apply."
        ),
    ] = None,
    optimization_level: Annotated[int, Field(ge=0, le=3)] = 2,
    basis_gates: list[str] | str | None = None,
    coupling_map: list[list[int]] | str | None = None,
    initial_layout: list[int] | None = None,
    seed_transpiler: int | None = None,
    circuit_format: Literal["auto", "qasm3", "qpy"] = "auto",
) -> dict[str, Any]:
    """Transpile a circuit. For a device, the result has the depth and two-qubit gates before
    and after, the layout of the circuit's qubits on the device, and the swaps routing
    inserted (each with its layer and physical qubits)."""
    try:
        if backend_name:
            qc = runs.parse_circuit(circuit, circuit_format)
            device = await asyncio.to_thread(device_for, backend_name)
            report = await asyncio.to_thread(
                routing.layout_report, qc, device, optimization_level, seed_transpiler
            )
            report.pop("_circuit", None)
            return {"status": "success", "backend_name": backend_name, **report}
        from qiskit_mcp_server.transpiler import transpile_circuit as base

        return await base(
            circuit=circuit,
            optimization_level=optimization_level,
            basis_gates=basis_gates,
            coupling_map=coupling_map,
            initial_layout=initial_layout,
            seed_transpiler=seed_transpiler,
            circuit_format="qasm3" if circuit_format == "auto" else circuit_format,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool(annotations={"title": "Compare optimization levels", "readOnlyHint": True})
async def compare_optimization_levels(
    circuit: CircuitArg,
    backend_name: Annotated[
        str | None, Field(description="Compare transpiles for this device.")
    ] = None,
    seed_transpiler: int | None = None,
    circuit_format: Literal["auto", "qasm3", "qpy"] = "auto",
) -> dict[str, Any]:
    """Depth, two-qubit gates and swaps at optimization levels 0 to 3."""
    try:
        if backend_name:
            qc = runs.parse_circuit(circuit, circuit_format)
            device = await asyncio.to_thread(device_for, backend_name)
            levels = {}
            for level in range(4):
                report = await asyncio.to_thread(
                    routing.layout_report, qc, device, level, seed_transpiler
                )
                levels[str(level)] = {
                    "depth": report["after"]["depth"],
                    "two_qubit_gates": report["after"]["two_qubit_gates"],
                    "size": report["after"]["size"],
                    "swaps": len(report["swaps"]),
                    "physical_qubits": (report["layout"] or {}).get("physical_qubits"),
                }
            best = min(levels, key=lambda k: (levels[k]["two_qubit_gates"], levels[k]["depth"]))
            return {
                "status": "success",
                "backend_name": backend_name,
                "levels": levels,
                "fewest_two_qubit_gates": int(best),
            }
        from qiskit_mcp_server.transpiler import compare_optimization_levels as base

        return await base(
            circuit, circuit_format="qasm3" if circuit_format == "auto" else circuit_format
        )
    except Exception as exc:
        return _error(exc)


_mount_all()

from . import views  # noqa: E402  (registers the views and their tools on mcp)


del views


def main() -> None:
    """Run the server over stdio."""
    # The mounted runtime server sets the root logger to INFO; Qiskit's per-pass timing
    # lines would fill the host's log for every transpile.
    for name in ("qiskit", "qiskit_aer", "qiskit_ibm_runtime", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
