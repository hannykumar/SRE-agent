# Runbook: High Memory Usage / Suspected Memory Leak

## Metadata
- incident_type: HighMemory
- primary_signal: memory rising over time
- common_root_causes: memory leak, cache growth, large payloads
- severity: SEV2
- services: any

## Symptoms
- Memory usage steadily increases
- GC pauses (managed runtimes)
- Possible OOMKilled later

## Fast Checks (2 minutes)
1. Check memory trend (30–60 min)
2. Compare across replicas (all pods or one?)
3. Check recent deploy/config change

## Evidence to Collect
- Memory trend chart
- Restarts history
- Logs around spikes
- Memory limits/requests

## Diagnosis Rules
- Memory rises steadily and resets on restart → leak signal
- Memory rises with traffic spikes → workload allocation
- Only one pod affected → uneven traffic/hot partition

## Mitigation (Safe)
- Restart worst pod (if redundancy exists)
- Scale replicas
- Reduce cache size / feature flags if available

## Permanent Fix
- Heap profiling / memory dump analysis
- Fix leak
- Add memory budget + alerts

## Escalation
- Escalate to service owner when leak pattern confirmed
