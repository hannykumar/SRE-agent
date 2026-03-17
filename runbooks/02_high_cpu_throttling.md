# Runbook: High CPU / CPU Throttling

## Metadata
- incident_type: HighCPU
- primary_signal: CPU usage / throttling
- common_root_causes: traffic spike, inefficient code, low CPU limit
- severity: SEV2
- services: any

## Symptoms
- Latency increases, timeouts
- CPU usage near limit
- Throttling present (if metrics available)

## Fast Checks (2 minutes)
1. Check CPU metrics (last 15 minutes)
2. Check request rate / traffic spike
3. Check CPU requests/limits

## Evidence to Collect
- CPU usage per pod
- CPU requests/limits
- Error rate + latency
- Recent deploy time

## Diagnosis Rules
- CPU high + throttling → CPU limit too low or workload too heavy
- CPU high no traffic spike → regression/infinite loop
- Recent deploy → suspect code change

## Mitigation (Safe)
- Scale replicas
- Increase CPU limit temporarily (if allowed)
- Rate-limit non-critical traffic

## Permanent Fix
- Optimize hot paths
- Add caching
- Fix autoscaling + sizing

## Escalation
- Escalate if sustained throttling > 10 minutes with customer impact
