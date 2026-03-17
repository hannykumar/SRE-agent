# Runbook: Queue Backlog / Worker Lag

## Metadata
- incident_type: QueueBacklog
- primary_signal: queue depth and worker lag
- common_root_causes: slow consumers, dependency slowdown, insufficient replicas
- severity: SEV2
- services: worker, checkout, api

## Symptoms
- Queue depth grows steadily
- Worker latency grows and retries increase
- Downstream customer-facing latency may rise later

## Fast Checks (2 minutes)
1. Check queue depth and processing rate panels
2. Check worker logs for dependency timeouts
3. Check recent deploys for throughput regressions

## Evidence to Collect
- Queue depth / throughput metrics
- Worker logs and traces
- Recent deploys
- Incident history

## Diagnosis Rules
- Queue depth rising while workers healthy -> insufficient worker capacity
- Queue depth rising with dependency timeout spans -> downstream dependency issue
- Recent deploy and throughput drop -> worker regression

## Mitigation (Safe)
- Scale worker replicas to drain the backlog
- Restart a stuck worker if only one pod is wedged

## Permanent Fix
- Capacity planning for spikes
- Backpressure and retry tuning
- Consumer performance profiling
