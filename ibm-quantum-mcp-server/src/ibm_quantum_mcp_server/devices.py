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

"""A device's map and calibration, for a fake device and a real one alike.

Everything comes from the backend's ``target`` (and its calibration properties), so
the calibration tool, the qubit chains and the backend view read the same numbers.
Positions for drawing the coupling map are computed once per device with a
deterministic layout and kept in the state folder, so a heavy-hex device looks the
same every time.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
from typing import Any

from .jobs import state_dir


_NOT_GATES = {"barrier", "measure", "reset", "delay", "store", "for_loop", "if_else"}


def edges_of(backend: Any) -> list[tuple[int, int]]:
    """The device's couplings, each pair once, lowest qubit first."""
    pairs: set[tuple[int, int]] = set()
    cmap = getattr(backend, "coupling_map", None)
    if cmap is not None:
        for a, b in cmap.get_edges():
            pairs.add((min(a, b), max(a, b)))
    return sorted(pairs)


def two_qubit_gate(backend: Any) -> str | None:
    """The device's native two-qubit gate (``cz``, ``ecr`` or ``cx``)."""
    target = backend.target
    for name in ("cz", "ecr", "cx"):
        if name in target.operation_names:
            return name
    for name in target.operation_names:
        with contextlib.suppress(Exception):
            if name not in _NOT_GATES and target.operation_from_name(name).num_qubits == 2:
                return str(name)
    return None


def _round(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def qubit_metrics(backend: Any) -> list[dict[str, Any]]:
    """T1 and T2 (µs) and readout error per qubit."""
    target = backend.target
    out = []
    for q in range(backend.num_qubits):
        props = target.qubit_properties[q] if target.qubit_properties else None
        t1 = getattr(props, "t1", None)
        t2 = getattr(props, "t2", None)
        readout = None
        with contextlib.suppress(Exception):
            readout = target["measure"][(q,)].error
        out.append(
            {
                "qubit": q,
                "t1_us": _round(t1 * 1e6, 2) if t1 is not None else None,
                "t2_us": _round(t2 * 1e6, 2) if t2 is not None else None,
                "readout_error": _round(readout),
            }
        )
    return out


def edge_metrics(backend: Any) -> list[dict[str, Any]]:
    """The two-qubit gate error per coupling (the lower of its two directions)."""
    gate = two_qubit_gate(backend)
    target = backend.target
    out = []
    for a, b in edges_of(backend):
        errors = []
        if gate is not None:
            for pair in ((a, b), (b, a)):
                with contextlib.suppress(Exception):
                    props = target[gate][pair]
                    if props is not None and props.error is not None:
                        errors.append(float(props.error))
        out.append(
            {
                "edge": [a, b],
                "gate": gate,
                "two_qubit_error": _round(min(errors)) if errors else None,
            }
        )
    return out


def calibration(backend: Any, name: str, qubit_indices: list[int] | None = None) -> dict[str, Any]:
    """The calibration data the runtime server's tool returns, read from a fake device."""
    from qiskit_ibm_runtime_mcp_server import ibm_runtime

    properties = backend.properties()
    num_qubits = backend.num_qubits
    faulty: list[int] = []
    with contextlib.suppress(Exception):
        faulty = list(properties.faulty_qubits())
    if qubit_indices is None:
        qubit_indices = list(range(min(10, num_qubits)))
    else:
        qubit_indices = [q for q in qubit_indices if 0 <= q < num_qubits]
    qubits = [ibm_runtime._get_qubit_calibration_data(properties, q, faulty) for q in qubit_indices]
    chosen = set(qubit_indices)
    edges = [list(e) for e in edges_of(backend) if e[0] in chosen and e[1] in chosen]
    two_qubit = [
        {"gate": m["gate"], "qubits": m["edge"], "error": m["two_qubit_error"]}
        for m in edge_metrics(backend)
        if m["edge"][0] in chosen and m["edge"][1] in chosen and m["two_qubit_error"] is not None
    ]
    single = [
        g
        for g in ibm_runtime._get_gate_errors(properties, qubit_indices, [])
        if len(g["qubits"]) == 1
    ]
    return {
        "status": "success",
        "backend_name": name,
        "num_qubits": num_qubits,
        "last_calibration": str(getattr(properties, "last_update_date", "") or "") or None,
        "faulty_qubits": faulty,
        "faulty_gates": [],
        "qubit_calibration": qubits,
        "gate_errors": single + two_qubit,
        "couplings": edges,
        "note": "A fake device: its calibration is the snapshot qiskit-ibm-runtime ships, "
        "not today's. T1/T2 in microseconds, errors are probabilities (0-1). "
        f"Showing qubits {qubit_indices}.",
    }


def qubit_chains(
    backend: Any, name: str, chain_length: int, num_results: int, metric: str
) -> dict[str, Any]:
    """The runtime server's qubit-chain search, on a fake device's calibration."""
    from qiskit_ibm_runtime_mcp_server import ibm_runtime as ir

    if metric not in ("two_qubit_error", "readout_error", "combined"):
        return {"status": "error", "message": f"Unknown metric {metric!r}."}
    chain_length = max(2, min(chain_length, 20))
    num_results = max(1, min(num_results, 20))
    properties = backend.properties()
    edges = [list(e) for e in edges_of(backend)]
    adjacency: dict[int, list[int]] = {q: [] for q in range(backend.num_qubits)}
    for a, b in edges:
        adjacency[a].append(b)
        adjacency[b].append(a)
    adjacency_list = {str(k): sorted(v) for k, v in adjacency.items()}
    faulty: set[int] = set()
    with contextlib.suppress(Exception):
        faulty = set(properties.faulty_qubits())
    qcal = ir._build_qubit_calibration(properties, backend.num_qubits, faulty)
    gerr = ir._build_gate_errors(properties, edges)
    chains = ir._find_all_linear_chains(adjacency_list, chain_length, faulty)
    if not chains:
        return {
            "status": "error",
            "message": f"No chain of {chain_length} qubits on {name}.",
        }
    chosen: Any = metric
    scored = sorted(
        ((c, ir._score_chain(c, qcal, gerr, chosen)) for c in chains), key=lambda x: x[1]
    )
    return {
        "status": "success",
        "backend_name": name,
        "chain_length": chain_length,
        "metric": metric,
        "total_chains_found": len(chains),
        "faulty_qubits": sorted(faulty),
        "chains": [
            ir._build_chain_result(rank, chain, score, qcal, gerr)
            for rank, (chain, score) in enumerate(scored[:num_results], 1)
        ],
    }


# --- Positions ------------------------------------------------------------------


def positions(name: str, backend: Any) -> list[list[float]]:
    """2D positions for each qubit, computed once per device and kept on disk."""
    edges = edges_of(backend)
    digest = hashlib.sha256(json.dumps([backend.num_qubits, edges]).encode()).hexdigest()[:16]
    folder = state_dir() / "positions"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{''.join(c for c in name if c.isalnum() or c in '-_')}-{digest}.json"
    if path.exists():
        with contextlib.suppress(OSError, ValueError):
            cached: list[list[float]] = json.loads(path.read_text(encoding="utf-8"))
            return cached
    coords = compute_positions(backend.num_qubits, edges)
    path.write_text(json.dumps(coords), encoding="utf-8")
    return coords


def compute_positions(num_qubits: int, edges: list[tuple[int, int]]) -> list[list[float]]:
    """Qiskit's drawn coordinates where it has them, else a heavy-hex row layout, else a
    seeded spring layout. Each is deterministic."""
    from qiskit.visualization import gate_map

    known = _known_coordinates(gate_map, num_qubits)
    if known is not None and _edges_short(known, edges):
        return [[float(x), float(y)] for y, x in known]
    rows = _row_layout(num_qubits, edges)
    if rows is not None and _edges_short(rows, edges, limit=1.5):
        return rows
    return _spring_layout(num_qubits, edges)


def _known_coordinates(gate_map: Any, num_qubits: int) -> list[list[float]] | None:
    """The coordinates ``plot_gate_map`` draws a device of this size with, if it has them.

    Qiskit keeps that table inside the function, so it is read from the module's source
    (``qubit_coordinates_map[N] = [...]``) rather than copied here.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(gate_map))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "qubit_coordinates_map"
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == num_qubits
        ):
            with contextlib.suppress(ValueError):
                table = ast.literal_eval(node.value)
                if len(table) == num_qubits:
                    return [[float(x), float(y)] for x, y in table]
    return None


def _edges_short(coords: list[Any], edges: list[tuple[int, int]], limit: float = 1.5) -> bool:
    for a, b in edges:
        (xa, ya), (xb, yb) = coords[a], coords[b]
        if math.hypot(xa - xb, ya - yb) > limit:
            return False
    return True


def _neighbours(num_qubits: int, edges: list[tuple[int, int]]) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {q: set() for q in range(num_qubits)}
    for a, b in edges:
        out[a].add(b)
        out[b].add(a)
    return out


def _rows_and_bridges(
    num_qubits: int, neighbours: dict[int, set[int]]
) -> tuple[list[list[int]], list[int]]:
    rows: list[list[int]] = []
    bridges: list[int] = []
    for q in range(num_qubits):
        if rows and rows[-1][-1] == q - 1 and (q - 1) in neighbours[q]:
            rows[-1].append(q)
        elif (q + 1) in neighbours[q]:
            rows.append([q])
        else:
            bridges.append(q)
    return rows, bridges


def _row_layout(num_qubits: int, edges: list[tuple[int, int]]) -> list[list[float]] | None:
    """IBM numbers heavy-hex devices row by row: a row is a run of qubits each coupled to
    the one before; a qubit coupled to neither neighbour by index bridges two rows.
    Rows are shifted so each bridge sits straight between the qubits it joins."""
    neighbours = _neighbours(num_qubits, edges)
    rows, bridges = _rows_and_bridges(num_qubits, neighbours)
    if len(rows) < 2:
        return None
    row_of = {q: r for r, row in enumerate(rows) for q in row}
    local = {q: i for row in rows for i, q in enumerate(row)}
    offset = [0] * len(rows)
    for r in range(1, len(rows)):
        for b in bridges:
            up = [n for n in neighbours[b] if row_of.get(n) == r - 1]
            down = [n for n in neighbours[b] if row_of.get(n) == r]
            if up and down:
                offset[r] = offset[r - 1] + local[up[0]] - local[down[0]]
                break
    low = min(offset)
    coords: dict[int, list[float]] = {
        q: [float(local[q] + offset[r] - low), float(2 * r)] for q, r in row_of.items()
    }
    for b in bridges:
        placed = [coords[n] for n in neighbours[b] if n in coords]
        if not placed:
            return None
        coords[b] = [
            sum(p[0] for p in placed) / len(placed),
            sum(p[1] for p in placed) / len(placed),
        ]
    if len(coords) != num_qubits:
        return None
    return [coords[q] for q in range(num_qubits)]


def _spring_layout(num_qubits: int, edges: list[tuple[int, int]]) -> list[list[float]]:
    import rustworkx as rx

    graph = rx.PyGraph()
    graph.add_nodes_from(range(num_qubits))
    graph.add_edges_from_no_data(edges)
    pos = rx.spring_layout(graph, seed=7, num_iter=200)
    scale = math.sqrt(max(num_qubits, 1))
    return [[round(pos[q][0] * scale, 4), round(pos[q][1] * scale, 4)] for q in range(num_qubits)]
