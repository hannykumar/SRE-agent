# AI Assistant for SRE

This project is a hybrid AI assistant for SRE and IT operations, focused on a simple workflow product:
- investigate an incident
- review grounded evidence
- approve a safe action
- verify whether the signal recovered

If you want the shortest architecture explanation first, read:
- `docs/ARCHITECTURE_SIMPLE.md`
- especially the `Tiny Diagram` and `Read These 5 Files First` sections

The active design is hybrid:
- the workflow, tool execution, approvals, audit, GitOps, and rollback paths are deterministic
- the `Agent` node can run either a deterministic fallback planner or an Ollama-backed LLM planner
- the active UI is workflow-first

It does five things well:
- investigates incidents through MCP tools
- shows a visible reasoning trace for every decision
- coordinates specialist findings across metrics, logs, and change context
- escalates safely when the evidence is weak
- requires human approval before any remediation executes

Phase 1 production additions:
- Postgres-backed run and audit storage
- isolated executor service for approved actions
- Redis-backed rate limiting with local fallback
- retries and timeouts around tool and executor calls
- rollback records persisted per execution

Phase 3 and 4 additions:
- live MCP backend for Kubernetes, Prometheus, and logs
- GitOps remediation path for config-managed workloads
- admin-only rollback execution endpoint

Phase 5 and 6 additions:
- incident catalog in `catalog/incident_catalog.json`
- confirmation rules, verification rules, and safe action templates per incident type
- hybrid retrieval with lexical + TF-IDF scoring, reranking, and one retry loop
- retrieval quality scoring and candidate diagnosis ranking
- evidence graph with citations in the final answer
- Grafana-native MCP-style tools for metrics, logs, traces, dashboard context, and deploy context
- async plan/execute queue with grouping and queue metrics
- Redis-backed durable worker path for plan and execute jobs
- replay evaluation on a 65-incident synthetic benchmark set

Platform-facing additions:
- generic alert-source adapter model (`Grafana` and `Generic Webhook`)
- infrastructure memory and integration registry surfaces
- coordinator-style investigation summary over metrics, logs, and change specialists
- platform overview API/UI for integrations, alert sources, and infrastructure memory

## Core flow

```text
Ingest -> Agent -> Tools -> Agent -> ...
                    |-> HITL interrupt
                    |-> Execute approved remediation
                    |-> Escalate unknown incidents
```

The active graph has five nodes:
- `Ingest`
- `Agent`
- `Tools`
- `HITL_Interrupt`
- `Execute`

The `Agent` node is the brain. It reads the shared `AgentState`, weighs ranked diagnoses plus specialist findings, decides whether to call a tool, escalate, or propose a remediation, and updates the reasoning trace in one place.

The previous deterministic graph file is backed up at:
- `archive/backup_20260311/langgraph_agent_deterministic.py`

## Safety model

- Unknown incidents never get guessed.
- The fallback output is:
  `Diagnosis: Unknown. Confidence: Low. Action: Escalate to Level 2 SRE Engineer.`
- Remediation uses structured actions, not raw LLM shell text.
- `execute_remediation` only runs after explicit approval.
- If the Ollama planner is unavailable or returns invalid structured output, the graph falls back to the deterministic planner and records that in `planner_backend`.

## Tooling

Product runtime profile:
- `mcp`: true MCP stdio server/client over the mock toolset
- `preview`: render and approve the remediation plan without changing infrastructure
- `planner_provider`: `deterministic` by default, `ollama` when enabled

Alert-source adapters:
- `Grafana` webhook
- `Generic Webhook`

MCP backends:
- `mock`: file-backed demo tools
- `live`: real read adapters for Kubernetes, Prometheus, and logs

Read tools:
- `get_pod_status`
- `describe_pod`
- `get_metrics`
- `get_pod_logs`
- `get_cluster_events`
- `query_prometheus`
- `query_loki`
- `query_tempo`
- `get_dashboard_context`
- `get_recent_deploys`

Write tool:
- `execute_remediation`

The mock MCP layer mutates the file-backed demo state so you can see before/after behavior in `simulate` mode.

## Incident Catalog

The scalable incident logic is no longer hardcoded to three demo cases.

The catalog in `catalog/incident_catalog.json` defines, per incident type:
- retrieval keywords
- preferred runbooks
- discriminating tools
- confirmation rules
- verification rules
- safe action templates

Current cataloged incident classes:
- `CrashLoopBackOff`
- `Service503`
- `DNSFailure`
- `HighCPU`
- `HighMemory`
- `DeploymentRegression`
- `DatabaseConnectionFailure`
- `TLSCertificateExpiry`
- `NodeNotReady`
- `DiskPressure`
- `QueueBacklog`
- `DependencyLatency`

The deterministic fallback planner and the hybrid LLM planner both consume the same catalog.

## Retrieval and Grounding

Retrieval now does:
- lexical scoring
- TF-IDF vector scoring
- optional semantic blending from Qdrant
- reranking with incident/evidence priors
- retrieval quality scoring
- one retry loop when the first retrieval is weak

Groundedness rules:
- runbooks are hints, not proof
- tool observations are the source of truth
- final answers cite evidence ids from the evidence graph
- unknown cases escalate instead of guessing

UI additions:
- `Investigation` shows retrieval quality, candidate diagnoses, rationale, and citations
- `Platform Overview` shows integrations, alert-source adapters, and infrastructure memory
- `Evaluation` exposes replay-benchmark metrics

## Platform services

- `ops.api`: agent-facing API and run orchestration
- `executor.api`: isolated execution service for approved actions
- `postgres`: run records, approvals, audits, executions, rollback records
- `redis`: request rate limiting and durable job queue backend
- `ops.worker`: durable plan/execute worker when `SRE_QUEUE_BACKEND=redis`
- `data/gitops_repo`: local GitOps artifact repo for config-change proposals and rollbacks

Local development falls back to:
- `sqlite` when `DATABASE_URL` is not set
- in-memory rate limiting and in-process queue when `REDIS_URL` is not set

Local `bash scripts/start_stack.sh` uses SQLite by default unless you export `DATABASE_URL` first.
If you want Postgres-backed runs immediately, use `docker compose up --build`.

## Local run

```bash
cd /Users/hannykumar/self-healing-sre
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

Optional semantic retrieval setup:

```bash
docker compose -f docker/qdrant.yml up -d
python ingestion/reset_and_ingest.py
```

Run tests:

```bash
PYTHONPATH=. .venv/bin/pytest -q
```

Enable the Ollama planner for local runs:

```bash
ollama pull qwen3:4b
export SRE_AGENT_PLANNER_PROVIDER=ollama
export SRE_AGENT_PLANNER_MODEL=qwen3:4b
export SRE_AGENT_PLANNER_BASE_URL=http://127.0.0.1:11434
```

Planner defaults:
- provider: `deterministic`
- model: `qwen3:4b`
- timeout: `20s`
- retries: `0`

Run the API:

```bash
PYTHONPATH=. .venv/bin/uvicorn ops.api:app --host 0.0.0.0 --port 8090
```

Run the isolated executor:

```bash
PYTHONPATH=. .venv/bin/uvicorn executor.api:app --host 0.0.0.0 --port 8091
```

Run the durable worker when using the Redis queue backend:

```bash
export SRE_QUEUE_BACKEND=redis
PYTHONPATH=. .venv/bin/python -m ops.worker
```

Run the UI:

```bash
PYTHONPATH=. .venv/bin/streamlit run ui/app.py
```

UI pages:
- `Workflow`: core control page for investigate -> approve -> verify
- `Investigation`: grounded diagnosis, evidence, runbooks, and ranked candidates
- `Approval`: review the proposed action and approve preview/live execution
- `Verification`: check whether the monitored signal recovered
- `Reasoning Trace`: only the agent-side decision path
- `Tool Calls`: only tool inputs, observations, and execution events
- `Evaluation`: built-in benchmark runner for the demo incidents
- `Alert Lab`: trigger CrashLoop/OOM, 503, or DNS scenarios and load the Grafana-created run from one page
- `Platform Overview`: inspect connector health, alert adapters, and infrastructure memory

Run the evaluation harness:

```bash
PYTHONPATH=. .venv/bin/python evaluate_langgraph.py
```

Preview benchmark on the 65-incident replay set:

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
from evaluate_langgraph import run_evaluation
print(run_evaluation(tool_mode='direct', execution_mode='preview', save_outputs=False, use_replays=True)['aggregates'])
PY
```

Simulated execution benchmark on the 65-incident replay set:

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
from evaluate_langgraph import run_evaluation
print(run_evaluation(tool_mode='direct', execution_mode='simulate', save_outputs=False, use_replays=True)['aggregates'])
PY
```

MCP transport smoke benchmark on the base incidents:

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
from evaluate_langgraph import run_evaluation
print(run_evaluation(incident_ids=['INC-001','INC-002','INC-003','INC-004'], tool_mode='mcp', execution_mode='preview', save_outputs=False, use_replays=False)['aggregates'])
PY
```

Live observability probe for Prometheus/Loki/Tempo-style backends:

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
from evaluate_langgraph import run_live_observability_evaluation
print(run_live_observability_evaluation(service='api', namespace='prod'))
PY
```

Run the local stack in one command:

```bash
bash scripts/start_stack.sh
```

Use the Ollama planner with Docker Compose:

```bash
export SRE_AGENT_PLANNER_PROVIDER=ollama
export SRE_AGENT_PLANNER_MODEL=qwen3:4b
export SRE_AGENT_PLANNER_BASE_URL=http://host.docker.internal:11434
docker compose up --build
```

Validate the Docker stack definition, including the durable worker:

```bash
docker compose config --services
```

Optional live backend env:

```bash
export SRE_MCP_BACKEND=live
export PROMETHEUS_BASE_URL=http://127.0.0.1:9090
export LOKI_BASE_URL=http://127.0.0.1:3100
export SRE_LOGS_BACKEND=loki
export KUBECONFIG=$HOME/.kube/config
```

Containerized live backend:

```bash
cp .env.live.example .env.live
# edit the URLs and kubeconfig path in .env.live
docker compose --env-file .env.live -f docker-compose.yml -f docker-compose.live.yml up --build -d
```

## Docker run

One-step stack:

```bash
docker compose up --build
```

Then open:
- UI: `http://localhost:8501`
- API: `http://localhost:8090`
- Executor: `http://localhost:8091`

The Docker stack now includes:
- `sre-agent`: UI + agent API
- `executor`: isolated execution service
- `postgres`: persistent run and audit storage
- `pgadmin`: browser UI for Postgres
- `redis`: rate limiting
- `redisinsight`: browser UI for Redis

It defaults to `tool_mode=mcp`, so the packaged demo uses the true MCP mock server/client path out of the box.
Set `SRE_MCP_BACKEND=live` in the `sre-agent` container if you want the app to talk to real Kubernetes and observability backends.
Set `SRE_AGENT_PLANNER_PROVIDER=ollama` if you want the `Agent` node to use your local Ollama model instead of the deterministic planner.

Check planner status:

```bash
curl http://127.0.0.1:8090/health
```

Look for:
- `runtime_profile.planner_provider`
- `runtime_profile.planner_model`

Each run result also records:
- `planner_backend`
- `planner_error`

Browser admin tools:
- pgAdmin: `http://localhost:5050`
- RedisInsight: `http://localhost:5540`

pgAdmin login:
- email: `admin@sre-agent.dev`
- password: `sre-agent-admin`

pgAdmin startup:
- the `SRE Agent Postgres` server is pre-registered automatically
- enter the password `sre_agent` the first time you connect

RedisInsight connection settings:
- host: `redis`
- port: `6379`

If you also want Qdrant for semantic retrieval in Docker:

```bash
docker compose --profile semantic up --build
docker compose exec sre-agent python ingestion/reset_and_ingest.py
```

## Alert Lab

The alert lab uses real Prometheus + Grafana alerts, then maps each alert into the matching mock investigation path:
- `INC-001`: CrashLoop / OOM
- `INC-002`: High 5xx / upstream 503
- `INC-003`: DNS failure

Bring up the lab:

```bash
docker compose --profile alert-lab up --build -d
```

Lab services:
- demo service: `http://localhost:8088`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`

Grafana login:
- username: `admin`
- password: `admin`

Trigger a scenario:

```bash
curl -X POST http://127.0.0.1:8088/admin/mode/crashloop
curl -X POST http://127.0.0.1:8088/admin/mode/error503
curl -X POST http://127.0.0.1:8088/admin/mode/dnsfailure
```

Recover the demo service:

```bash
curl -X POST http://127.0.0.1:8088/admin/reset
```

What happens:
- `trafficgen` sends steady requests to the demo service
- Prometheus scrapes the demo metrics
- Grafana fires one of:
  - `Demo API CrashLoop OOM Signal`
  - `Demo API High 5xx Rate`
  - `Demo API DNS Failure Signal`
- Grafana posts the alert to `/alerts/grafana/webhook`
- the API maps the alert label `incident_id` to `INC-001`, `INC-002`, or `INC-003`
- the API creates one active run per firing alert and deduplicates repeat posts while the run is still `planning` or `planned`
- the run appears in the main app under `Run History`

Live remediation in the lab:
- Grafana-created runs still start in `preview`
- after you load the run into the workspace, switch `Execution` to `live`
- approve the remediation from the `Overview` page
- the executor applies the lab remediation, resets the demo service, and verifies the result against Prometheus + Grafana
- the execute result now includes a `verification` block with:
  - `resolved`
  - `alert_state`
  - `metric_value`
  - `metric_threshold`

Current scope note:
- the alert ingress is real
- the first lab still maps into the mock `INC-002` investigation path
- the later live-backend step is where Kubernetes/log/metrics reads become fully real

## API examples

Create a plan:

```bash
curl -s -X POST http://127.0.0.1:8090/runs/plan \
  -H 'Content-Type: application/json' \
  -d '{"incident_id":"INC-002"}'
```

Approve the preview:

```bash
curl -s -X POST http://127.0.0.1:8090/runs/<run_id>/approve-execute
```

Fetch trace:

```bash
curl -s http://127.0.0.1:8090/runs/<run_id>/trace
```

Fetch audit trail:

```bash
curl -s http://127.0.0.1:8090/runs/<run_id>/audit
```

Fetch rollback record:

```bash
curl -s http://127.0.0.1:8090/runs/<run_id>/rollback
```

Execute rollback:

```bash
curl -s -X POST http://127.0.0.1:8090/runs/<run_id>/rollback-execute
```

## Access model

- local Docker and `scripts/start_stack.sh` run with auth disabled by default
- the RBAC layer still exists in code for production-style deployments
- if you set `OPS_API_TOKENS` or `OPS_API_TOKEN`, the role model becomes active again

## Demo incidents

- `INC-001`: CrashLoopBackOff / OOM
- `INC-002`: upstream 503s
- `INC-003`: DNS failures
- `INC-004`: ambiguous incident that should escalate

## Repo focus

The active implementation lives in:
- [agent/langgraph_agent.py](/Users/hannykumar/self-healing-sre/agent/langgraph_agent.py)
- [agent/state.py](/Users/hannykumar/self-healing-sre/agent/state.py)
- [agent/prompts.py](/Users/hannykumar/self-healing-sre/agent/prompts.py)
- [mcp_tools/mock_mcp.py](/Users/hannykumar/self-healing-sre/mcp_tools/mock_mcp.py)
- [mcp_tools/mock_mcp_server.py](/Users/hannykumar/self-healing-sre/mcp_tools/mock_mcp_server.py)
- [mcp_tools/live_backends.py](/Users/hannykumar/self-healing-sre/mcp_tools/live_backends.py)
- [mcp_tools/live_mcp_server.py](/Users/hannykumar/self-healing-sre/mcp_tools/live_mcp_server.py)
- [mcp_tools/mcp_client.py](/Users/hannykumar/self-healing-sre/mcp_tools/mcp_client.py)
- [mcp_tools/tool_gateway.py](/Users/hannykumar/self-healing-sre/mcp_tools/tool_gateway.py)
- [mcp_tools/actions.py](/Users/hannykumar/self-healing-sre/mcp_tools/actions.py)
- [executor/gitops.py](/Users/hannykumar/self-healing-sre/executor/gitops.py)
- [ops/api.py](/Users/hannykumar/self-healing-sre/ops/api.py)
- [ui/app.py](/Users/hannykumar/self-healing-sre/ui/app.py)
- [ui/common.py](/Users/hannykumar/self-healing-sre/ui/common.py)
- [ui/pages/1_Reasoning_Trace.py](/Users/hannykumar/self-healing-sre/ui/pages/1_Reasoning_Trace.py)
- [ui/pages/2_Tool_Calls.py](/Users/hannykumar/self-healing-sre/ui/pages/2_Tool_Calls.py)
- [ui/pages/3_Evaluation.py](/Users/hannykumar/self-healing-sre/ui/pages/3_Evaluation.py)

Legacy files were moved into [archive/README.md](/Users/hannykumar/self-healing-sre/archive/README.md) so the active repo stays readable.
