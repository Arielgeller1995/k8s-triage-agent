# OOMKilled (Out of Memory Killed)

## Symptoms

- Pod status shows `OOMKilled` or the container's last terminated reason is `OOMKilled`
- Exit code is `137` (SIGKILL sent by the Linux kernel OOM killer)
- Pod may enter `CrashLoopBackOff` after repeated OOM kills
- `kubectl describe pod` shows `Last State: Terminated  Reason: OOMKilled`
- Node-level `dmesg` or system journal contains entries like `oom-kill event`, `Out of memory: Killed process`
- Application may produce no logs if it is killed mid-operation

## Likely Causes

1. **Memory limit set too low** — the container's `resources.limits.memory` is lower than the application's actual working-set memory
2. **Memory leak** — the application accumulates heap or off-heap memory over time and eventually exceeds the limit
3. **Bursty workload** — the app handles a sudden spike in traffic or data volume that exceeds normal memory usage
4. **JVM / runtime over-commitment** — JVM, Go runtime, or Node.js may allocate more than expected without explicit heap configuration
5. **Large in-memory cache or buffer** — unbounded caches, large request bodies held in memory, or streaming data accumulated before flushing
6. **Multiple containers sharing a pod** — the combined memory of all containers in a pod exceeds the node's available memory, triggering node-level OOM
7. **No limits set** — the container has no memory limit; the kernel kills it when the node itself runs out of memory

## Diagnostic Steps

1. Confirm OOMKill and note the timestamp:
   ```
   kubectl get pod <pod-name> -o jsonpath='{.status.containerStatuses[0].lastState.terminated}'
   ```
2. Check current memory usage vs limits:
   ```
   kubectl top pod <pod-name> --containers
   kubectl get pod <pod-name> -o jsonpath='{.spec.containers[0].resources}'
   ```
3. Review historical memory trend (if metrics-server or Prometheus is available):
   ```
   kubectl top pod <pod-name> --sort-by=memory
   ```
4. Examine logs just before the kill (the container may log an OOM hint):
   ```
   kubectl logs <pod-name> --previous --tail=200
   ```
5. Check node-level OOM events:
   ```
   kubectl describe node <node-name> | grep -i oom
   # On the node itself:
   dmesg | grep -i "oom\|killed process"
   ```
6. For JVM workloads, check heap configuration:
   ```
   kubectl exec <pod-name> -- java -XX:+PrintFlagsFinal -version 2>&1 | grep -i heapsize
   ```

## Possible Fixes

- **Increase memory limit**: raise `resources.limits.memory` based on observed peak usage plus a safety margin (typically 20–30%):
  ```yaml
  resources:
    requests:
      memory: "512Mi"
    limits:
      memory: "1Gi"
  ```
- **Fix memory leak**: profile the application with heap dumps, pprof (Go), jmap/jhat (JVM), or node-inspect (Node.js); patch the leak
- **Tune JVM heap**: set `-Xmx` / `-XX:MaxRAMPercentage` explicitly; without this the JVM may target 25% of node RAM, ignoring the container limit:
  ```
  env:
    - name: JAVA_OPTS
      value: "-Xmx768m -Xms256m"
  ```
- **Add Vertical Pod Autoscaler (VPA)**: let VPA recommend and apply right-sized requests/limits automatically
- **Implement backpressure**: cap in-memory queues, buffers, and caches; reject or shed load before memory is exhausted
- **Split the workload**: if multiple large containers share a pod, move memory-intensive sidecars to separate pods

## Notes

- The Linux OOM killer is non-deterministic when there is no memory limit — it may kill any process on the node, not just the offending container
- Memory `requests` affect scheduling (where the pod lands); `limits` affect runtime enforcement — both should be set
- Go applications may appear to use more memory than expected because the runtime does not immediately return freed memory to the OS; this is normal and does not indicate a leak
- For Node.js, set `--max-old-space-size` to a value below the container limit to allow graceful GC before the kernel intervenes
- Prometheus `container_memory_working_set_bytes` is the metric the kubelet compares against the limit, not `container_memory_usage_bytes`
