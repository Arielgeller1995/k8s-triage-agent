# Pending Pods

## Symptoms

- Pod status is `Pending` indefinitely in `kubectl get pods`
- `kubectl describe pod <name>` shows events like:
  - `0/3 nodes are available: 3 Insufficient cpu`
  - `0/3 nodes are available: 3 Insufficient memory`
  - `0/3 nodes are available: 3 node(s) had untolerated taint`
  - `0/3 nodes are available: 3 node(s) didn't match Pod's node affinity/selector`
  - `no nodes available to schedule pods`
- No container logs are available — the pod was never placed on a node
- Horizontal Pod Autoscaler (HPA) may be stuck at desired replicas without scaling up

## Likely Causes

1. **Insufficient CPU or memory** — no node in the cluster has enough allocatable resources to satisfy the pod's `requests`
2. **Node selector / affinity mismatch** — the pod requires a label (e.g., `disktype=ssd`, `zone=us-east-1a`) that no available node has
3. **Untolerated taint** — nodes are tainted (e.g., `dedicated=gpu:NoSchedule`) and the pod does not have a matching toleration
4. **PersistentVolumeClaim not bound** — the pod requires a PVC that is `Pending` because no PV matches the storage class or capacity request
5. **Resource quota exceeded** — the namespace quota for CPU, memory, or pod count has been reached
6. **No nodes available** — all nodes are `NotReady`, cordoned, or drained (e.g., during a rolling upgrade)
7. **Pod disruption budget (PDB) blocking eviction** — during rolling updates, PDBs may prevent new pods from being scheduled if old ones cannot be evicted
8. **Topology spread constraints** — the pod cannot be placed without violating `maxSkew` across zones or nodes

## Diagnostic Steps

1. Read the scheduler's reason for not placing the pod:
   ```
   kubectl describe pod <pod-name> | grep -A 10 "Events:"
   ```
2. Check overall node capacity and allocatable resources:
   ```
   kubectl describe nodes | grep -A 5 "Allocatable\|Allocated resources"
   kubectl top nodes
   ```
3. Inspect node taints and labels:
   ```
   kubectl get nodes --show-labels
   kubectl describe nodes | grep -i taint
   ```
4. Check namespace resource quotas:
   ```
   kubectl describe resourcequota -n <namespace>
   ```
5. Check PVC status if the pod uses persistent storage:
   ```
   kubectl get pvc -n <namespace>
   kubectl describe pvc <pvc-name>
   ```
6. Verify the pod's affinity, tolerations, and node selector:
   ```
   kubectl get pod <pod-name> -o jsonpath='{.spec.nodeSelector}'
   kubectl get pod <pod-name> -o jsonpath='{.spec.affinity}'
   kubectl get pod <pod-name> -o jsonpath='{.spec.tolerations}'
   ```
7. Check cluster autoscaler logs if CA is enabled:
   ```
   kubectl logs -n kube-system -l app=cluster-autoscaler --tail=100
   ```

## Possible Fixes

- **Insufficient resources**: scale up the node group (manually or via Cluster Autoscaler); or reduce the pod's `requests` to better reflect actual usage
- **Cluster Autoscaler not triggering**: verify the CA is running and the node group has not hit its `maxSize`; check CA logs for `no.scale.up.reason`
- **Node selector mismatch**: add the required label to a node (`kubectl label node <node> disktype=ssd`), or relax the selector/affinity rule
- **Taint not tolerated**: add a toleration to the pod spec, or remove the taint from the node if it was applied by mistake:
  ```yaml
  tolerations:
    - key: "dedicated"
      operator: "Equal"
      value: "gpu"
      effect: "NoSchedule"
  ```
- **PVC not bound**: check the StorageClass provisioner is running; verify the requested storage size and access mode match an available PV; create a PV manually if dynamic provisioning is unavailable
- **Quota exceeded**: increase the namespace quota or delete unused resources; use `kubectl describe resourcequota` to identify which limit is hit
- **All nodes cordoned/drained**: uncordon a node after maintenance: `kubectl uncordon <node-name>`
- **Topology spread too strict**: relax `maxSkew` or change `whenUnsatisfiable` from `DoNotSchedule` to `ScheduleAnyway`

## Notes

- A pod stuck in `Pending` does not consume cluster resources but does block rollouts and deployments
- `kubectl get events --sort-by=.lastTimestamp -n <namespace>` gives a chronological view of all scheduling events in the namespace
- Cluster Autoscaler only adds nodes when a pod is unschedulable due to resource constraints — it does not respond to taint, affinity, or quota issues
- Requests (not limits) are what the scheduler uses for bin-packing; over-requesting is a common cause of artificial resource exhaustion
- In multi-zone clusters, ensure node groups exist in all zones that pods can be scheduled into; a zone outage can cause topology constraints to make pods permanently unschedulable
