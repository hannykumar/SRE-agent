# Runbook: Disk Pressure / Storage Saturation

## Metadata
- incident_type: DiskPressure
- primary_signal: disk pressure or storage errors
- common_root_causes: log growth, temp file leak, node disk pressure
- severity: SEV2
- services: any

## Symptoms
- Cluster events mention disk pressure
- Writes fail or pods restart unexpectedly
- Latency grows as IO slows down

## Fast Checks (2 minutes)
1. Check cluster events for disk pressure or eviction signals
2. Check logs for `no space left` or storage write errors
3. Check recent deploys for log volume changes

## Evidence to Collect
- Cluster events
- Error logs
- Recent deploy context
- Incident history for recurring storage issues

## Diagnosis Rules
- `no space left` logs + disk pressure events -> storage saturation
- Recent deploy + log volume spike -> rollout regression
- Multi-pod restarts on same node -> infrastructure issue

## Mitigation (Safe)
- Restart the noisiest pod if it clears runaway temp files
- Scale stateless workload cautiously to reduce per-pod pressure if storage is remote

## Permanent Fix
- Log retention and temp file cleanup
- Disk sizing and alerts
- Node-level storage monitoring
