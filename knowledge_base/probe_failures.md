# Liveness and Readiness Probe Failures

## Symptoms

- Pod is running but not receiving traffic — `kubectl get endpoints <service>` shows no addresses
- Pod is repeatedly restarted despite the application appearing healthy
- `kubectl describe pod <name>` shows events like:
  - `Liveness probe failed: HTTP probe failed with statuscode: 503`
  - `Readiness probe failed: Get "http://...": dial tcp: connection refused`
  - `Liveness probe failed: OCI runtime exec failed` (exec probe)
- Pod transitions between `Running` and `CrashLoopBackOff` on a regular interval
- Traffic drops or errors spike at the same cadence as restarts

## Likely Causes

1. **Probe targeting the wrong port or path** — the handler path was renamed or the port number changed without updating the probe spec
2. **initialDelaySeconds too short** — the app is still starting when the first probe fires; it returns a non-2xx status and the pod is killed before it is ready
3. **Slow dependency causing transient failures** — a downstream service (database, cache) is slow; the health endpoint does a deep check and times out
4. **Application is genuinely unhealthy** — the liveness probe correctly identifies a deadlock, resource exhaustion, or stuck goroutine
5. **Exec probe command missing in image** — the image does not include the binary called by the exec probe (e.g., `curl`, `wget`, `grpc_health_probe`)
6. **TLS misconfiguration** — the probe uses HTTP but the app listens on HTTPS (or vice versa); or the certificate is self-signed and `httpGet` does not skip verification
7. **Resource starvation** — under CPU throttling, the app responds too slowly; the probe times out even though the app is not actually unhealthy

## Diagnostic Steps

1. List recent probe failure events:
   ```
   kubectl describe pod <pod-name> | grep -A 3 "probe failed"
   ```
2. Show the probe configuration:
   ```
   kubectl get pod <pod-name> -o jsonpath='{.spec.containers[0].livenessProbe}'
   kubectl get pod <pod-name> -o jsonpath='{.spec.containers[0].readinessProbe}'
   ```
3. Manually test the probe endpoint from inside the pod:
   ```
   kubectl exec <pod-name> -- wget -qO- http://localhost:<port><path>
   # or for gRPC:
   kubectl exec <pod-name> -- grpc_health_probe -addr=:50051
   ```
4. Check application logs around the time of failures:
   ```
   kubectl logs <pod-name> --since=5m
   ```
5. Inspect resource usage to rule out CPU throttling:
   ```
   kubectl top pod <pod-name> --containers
   kubectl describe pod <pod-name> | grep -A 4 "Limits\|Requests"
   ```
6. Verify the endpoint or port is actually listening:
   ```
   kubectl exec <pod-name> -- ss -tlnp
   # or: kubectl exec <pod-name> -- netstat -tlnp
   ```

## Possible Fixes

- **Wrong port/path**: update the probe spec to match the application's actual health endpoint:
  ```yaml
  livenessProbe:
    httpGet:
      path: /healthz
      port: 8080
    initialDelaySeconds: 30
    periodSeconds: 10
    failureThreshold: 3
  ```
- **initialDelaySeconds too short**: increase it to exceed the application's worst-case startup time; add a `startupProbe` for slow-starting apps to decouple startup from liveness:
  ```yaml
  startupProbe:
    httpGet:
      path: /healthz
      port: 8080
    failureThreshold: 30
    periodSeconds: 10
  ```
- **Deep health check timing out**: make the liveness probe shallow (process alive?) and the readiness probe deep (dependencies reachable?); never do expensive DB queries in a liveness probe
- **Missing exec binary**: install the required binary in the Dockerfile, or switch to an `httpGet` probe
- **TLS**: use `scheme: HTTPS` in `httpGet`, or expose a separate plaintext health port
- **CPU throttling causing timeouts**: raise `resources.limits.cpu` or switch from `Guaranteed` to `Burstable` QoS; increase probe `timeoutSeconds`

## Notes

- **Liveness vs Readiness vs Startup**: liveness restarts the container; readiness removes it from load balancer endpoints; startup delays liveness/readiness until the app has initialized
- A pod can be `Running` but not `Ready` — this is correct behavior when the readiness probe fails; traffic should not reach it
- Avoid coupling liveness to external dependencies — if the database is down, the liveness probe should not kill the pod (use readiness for that)
- `failureThreshold * periodSeconds` is the effective grace period before the pod is restarted; tune these to match your SLO for incident response
- The kubelet runs probes from the node, not from within the pod — network policies that block node-to-pod traffic will cause probe failures
