# Runbook: Node Not Ready / Scheduling Instability

## Metadata
- incident_type: NodeNotReady
- primary_signal: node readiness failures
- common_root_causes: kubelet failure, network partition, disk pressure
- severity: SEV1
- services: any

## Symptoms
- Pods are evicted or stuck pending
- Cluster events mention node readiness or taints
- A subset of workloads degrade together

## Fast Checks (2 minutes)
1. Check cluster events for `NodeNotReady` or scheduling failures
2. Check whether the impact is isolated to one node
3. Check if error spikes correlate with a pod reschedule wave

## Evidence to Collect
- Cluster events
- Pod status and restart pattern
- Error rate / latency panels
- Recent deploy context to rule out application regressions

## Diagnosis Rules
- Cluster events mention `NodeNotReady` -> platform issue
- Many pods degrade together -> infrastructure not application-specific
- Error spike without app logs changing -> suspect node or scheduling issue

## Mitigation (Safe)
- Scale the deployment to re-spread traffic while platform owners investigate
- Escalate to platform SRE immediately for node repair

## Permanent Fix
- Node health automation
- Better pod disruption protection
- Capacity headroom and placement review
