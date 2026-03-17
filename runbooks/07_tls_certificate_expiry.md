# Runbook: TLS Certificate Expiry / Handshake Failures

## Metadata
- incident_type: TLSCertificateExpiry
- primary_signal: tls handshake failures
- common_root_causes: expired cert, bad trust chain, stale secret mount
- severity: SEV1
- services: ingress, api, checkout

## Symptoms
- Logs show `certificate expired`, `x509`, or `tls handshake failed`
- External requests fail immediately
- Recent restart may temporarily hide the issue if secret reload is inconsistent

## Fast Checks (2 minutes)
1. Check logs for x509 or certificate expiry messages
2. Check recent certificate rotation or deploy history
3. Check blast radius and entrypoint dashboard

## Evidence to Collect
- TLS error logs
- Recent deploy and secret rotation context
- Dashboard panel spikes on handshake failures
- Service owner and escalation target

## Diagnosis Rules
- x509/certificate expired logs -> certificate lifecycle issue
- Recent deploy and handshake failures -> bad secret mount or config regression
- One ingress only -> localized entrypoint issue

## Mitigation (Safe)
- Restart the affected pods only after validating the secret has been rotated
- Escalate immediately if the cert is actually expired and no safe local reload exists

## Permanent Fix
- Automate cert renewal validation
- Add expiry alerting and post-rotation smoke tests
