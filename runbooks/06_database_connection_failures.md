# Runbook: Database Connection Failures

## Metadata
- incident_type: DatabaseConnectionFailure
- primary_signal: database connection errors
- common_root_causes: pool exhaustion, credential issues, network resets, database saturation
- severity: SEV1
- services: api, checkout, worker

## Symptoms
- Logs show `connection refused`, `too many clients`, or `timeout acquiring db connection`
- API latency and error rate rise quickly
- Retries make the incident worse under load

## Fast Checks (2 minutes)
1. Check application logs for DB connection errors
2. Check recent deploy or credential rotation
3. Check latency/error panels and dependency traces

## Evidence to Collect
- Error logs and stack traces
- Recent deploy record
- Trace span failures to the database dependency
- Incident history for similar failures

## Diagnosis Rules
- `too many clients` or pool timeout errors -> pool exhaustion or DB saturation
- Recent deploy + new connection errors -> config regression
- Trace spans fail on DB calls only -> dependency-specific outage

## Mitigation (Safe)
- Restart the worst pod to clear a stuck pool
- Scale stateless callers only if database saturation is not extreme
- Escalate to DB owner for credential or failover issues

## Permanent Fix
- Fix pooling configuration
- Add dependency circuit breaking
- Review database sizing and failover policy
