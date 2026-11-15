# ibm-quantum-mcp-server

One MCP server for quantum programs: the circuit tools of `qiskit-mcp-server`, the
IBM Quantum Runtime tools of `qiskit-ibm-runtime-mcp-server` and the documentation
tools of `qiskit-docs-mcp-server`, mounted in one process, plus local simulation and
four MCP Apps views. The three mounted servers keep working on their own.

Everything except a run on a real device works with no IBM Quantum account.

## Where a circuit runs

`run_sampler` and `run_estimator` take `backend_name`:

| `backend_name` | Runs | Engine |
| --- | --- | --- |
| `local_exact` | Exact results; Estimator values with no shot noise, Sampler jobs with exact probabilities beside their counts | Qiskit's `StatevectorSampler` and `StatevectorEstimator` |
| `local_aer` (default) | Ideal shots | qiskit-ibm-runtime's `SamplerV2`/`EstimatorV2` in local mode on `AerSimulator` |
| `fake_<device>` | That device's noise, coupling map and basis gates, transpiled as the device would be | the same primitives on `AerSimulator.from_backend` |
| any other name | The real device | qiskit-ibm-runtime |

Hardware is never the default. Before a hardware job is submitted the server reads
the instance's remaining time (`usage_info`) and refuses a job whose estimate exceeds
it, with both numbers. An instance that reports no time limit is not used.

## One job model

Every run returns a `job_id` (`local-…` for local jobs) and a record in the job store
under the server's state folder (`IBM_QUANTUM_MCP_STATE_DIR`, default the platform's
user state folder). `get_job_status`, `get_job_results`, `list_my_jobs` and
`cancel_job` treat local and hardware jobs alike; local jobs survive a restart.

- `parameter_values` takes one parameter set or a list of them, and the Estimator's
  `observables` one observable or a list: a batch of inputs is one job.
- `compare_jobs` lines up jobs that ran the same circuit: probabilities per bitstring
  and total variation distance, or values per parameter set with their differences,
  and with `expected` labels, accuracy and the sets whose class flipped.
- `record_job` keeps a job run elsewhere (a script with the Qiskit SDK) as a local job.
- `transpile_circuit` and `compare_optimization_levels` take `backend_name` to
  transpile for a device and report the layout and the swaps routing inserted.

## Views

`view_circuit`, `view_layout`, `view_results` and `view_backend` open
`ui://ibm-quantum/{circuit,layout,results,backend}`: one self-contained page built
from `packages/quantum-views` (`npm ci && npm run build` writes
`src/ibm_quantum_mcp_server/views/quantum-views.html`). Each reports what the person
selects as model context, offers `focus`, `capture` and `describe_region` (and
`show_jobs` in the results view) as app tools, and makes each of its operations a
call to one of this server's tools. A host without MCP Apps gets a text summary.

## Configuration

| Variable | Meaning |
| --- | --- |
| `QISKIT_IBM_TOKEN` | IBM Quantum API key, only for real hardware |
| `QISKIT_IBM_RUNTIME_MCP_INSTANCE` | The instance CRN |
| `QISKIT_MCP_NO_SAVED_ACCOUNT=1` | Never read or write `~/.qiskit/qiskit-ibm.json`; the key comes only from the environment (for hosts with their own credential store) |
| `IBM_QUANTUM_MCP_STATE_DIR` | Where jobs and device positions are kept |

## Run

```bash
uv run ibm-quantum-mcp-server
```

From a pinned commit of this repository:

```bash
uvx --from "git+https://github.com/JaimeCernuda/ibm-quantum-mcp-servers@<commit>#subdirectory=ibm-quantum-mcp-server" ibm-quantum-mcp-server
```

## Test

```bash
uv sync --group dev --group test
uv run pytest tests/
```
