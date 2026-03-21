# CrashLoopBackOff

## Symptoms

- Pod status shows `CrashLoopBackOff` in `kubectl get pods`
- Restart count climbs rapidly (visible in the RESTARTS column)
- `kubectl describe pod <name>` shows repeated `Back-off restarting failed container` events
- Container exits with a non-zero exit code shortly after starting
- Logs may be truncated or empty if the container crashes before writing output

## Likely Causes

1. **Application startup failure** — misconfigured environment variables, missing secrets, or bad config files cause the process to exit immediately
2. **Missing or invalid entrypoint** — the container image's CMD/ENTRYPOINT is wrong or the binary does not exist at the specified path
3. **Dependency not ready** — the app requires a database, cache, or external service that is unavailable at startup
4. **OOMKilled on startup** — memory limits are too low for the initialization phase; the kernel kills the process before it can serve traffic
5. **Liveness probe misconfiguration** — an overly aggressive liveness probe kills the container before it finishes initializing
6. **Filesystem permission errors** — the process cannot write to a required path (e.g., a mounted ConfigMap or emptyDir)
7. **Port conflict** — two containers in the same pod trying to bind the same port

## Diagnostic Steps

1. Check the restart count and last exit code:
   ```
   kubectl get pod <pod-name> -o jsonpath='{.status.containerStatuses[0].restartCount}'
   kubectl get pod <pod-name> -o jsonpath='{.status.containerStatuses[0].lastState.terminated.exitCode}'
   ```
2. Read the most recent logs (add `--previous` to see the last crashed container):
   ```
   kubectl logs <pod-name> --previous
   ```
3. Describe the pod for event history:
   ```
   kubectl describe pod <pod-name>
   ```
4. Inspect environment variables and mounted secrets/configmaps:
   ```
   kubectl exec <pod-name> -- env
   kubectl get pod <pod-name> -o yaml | grep -A 20 env
   ```
5. Check resource limits — look for OOMKilled in the terminated reason:
   ```
   kubectl get pod <pod-name> -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}'
   ```
6. Verify the image entrypoint is correct:
   ```
   kubectl get pod <pod-name> -o jsonpath='{.spec.containers[0].command}'
   ```

## Possible Fixes

- **Bad env / missing secret**: mount the correct Secret or ConfigMap; verify all required keys exist with `kubectl get secret <name> -o yaml`
- **Dependency unavailable**: add an `initContainer` that polls the dependency before the main container starts; use `wait-for-it` or a custom readiness script
- **OOMKilled**: increase `resources.limits.memory`; profile the app's startup memory with `kubectl top pod`
- **Aggressive liveness probe**: increase `initialDelaySeconds` and `failureThreshold` to give the app time to initialize
- **Wrong entrypoint**: rebuild the image with the correct CMD, or override it in the pod spec with `command` / `args`
- **Permission error**: set `securityContext.runAsUser` / `fsGroup` to match the owning UID of the volume

## Notes

- Exit code `1` usually means an application-level error; check logs first
- Exit code `137` (128 + 9) means the process was sent SIGKILL — almost always OOMKilled or a manual kill
- Exit code `139` means segfault — likely a binary compatibility issue or corrupted image layer
- Exit code `143` (128 + 15) means SIGTERM — the container was gracefully terminated (check liveness/readiness probes)
- Use `kubectl debug` (Kubernetes 1.23+) to attach an ephemeral container for live debugging without restarting
