# Runbook: Dependency Latency Spike

## Metadata
- incident_type: DependencyLatency
- primary_signal: dependency span latency
- common_root_causes: upstream slowdown, regional network issues, saturation
- severity: SEV2
- services: api, checkout, worker

## Symptoms
- P95 latency rises before error rate
- Traces point to one downstream span
- Logs show timeouts to the same dependency

## Fast Checks (2 minutes)
1. Check traces for the slowest downstream span
2. Check logs for timeout or retry spam
3. Check recent deploys to rule out local regression

## Evidence to Collect
- Trace summary
- Error logs
- Dashboard latency panels
- Recent deploys

## Diagnosis Rules
- Trace span errors cluster on one dependency -> upstream latency issue
- Latency rises with low local CPU/memory -> dependency, not local saturation
- Recent deploy absent -> supports upstream origin

## Mitigation (Safe)
- Restart one unhealthy caller pod if connections appear wedged
- Escalate to dependency owner if the slowdown persists

## Permanent Fix
- Circuit breaking and timeout policy
- Better dependency SLOs and alerts
