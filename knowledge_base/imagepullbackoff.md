# ImagePullBackOff / ErrImagePull

## Symptoms

- Pod status shows `ImagePullBackOff` or `ErrImagePull` in `kubectl get pods`
- `kubectl describe pod <name>` contains events like:
  - `Failed to pull image "...": rpc error: ... not found`
  - `Failed to pull image "...": unauthorized: authentication required`
  - `Back-off pulling image "..."`
- Pod never reaches `Running` state; it stays in `Pending` or transitions directly to the back-off loop
- No application logs are available because the container never started

## Likely Causes

1. **Image tag does not exist** — the tag was deleted, never pushed, or has a typo (e.g., `lates` instead of `latest`)
2. **Wrong registry URL** — the image path points to a private registry that the node cannot reach, or the hostname is misspelled
3. **Missing imagePullSecret** — the registry requires authentication but no pull secret is configured in the namespace or service account
4. **Expired or revoked credentials** — the pull secret exists but the token has expired or the robot account was deactivated
5. **Network connectivity** — the node cannot reach the registry due to firewall rules, missing NAT gateway, or DNS resolution failure
6. **Registry rate limiting** — Docker Hub enforces anonymous and free-tier pull rate limits; nodes may hit this under heavy churn
7. **Digest mismatch** — the image was referenced by digest and the content no longer matches (rare, usually after a registry migration)

## Diagnostic Steps

1. Describe the pod to read the exact error message:
   ```
   kubectl describe pod <pod-name> | grep -A 5 "Failed to pull"
   ```
2. Confirm the image reference in the pod spec:
   ```
   kubectl get pod <pod-name> -o jsonpath='{.spec.containers[*].image}'
   ```
3. Try pulling the image manually from one of the affected nodes:
   ```
   # SSH to the node, then:
   crictl pull <image-reference>
   # or: docker pull <image-reference>
   ```
4. Check whether an imagePullSecret is configured:
   ```
   kubectl get pod <pod-name> -o jsonpath='{.spec.imagePullSecrets}'
   kubectl get serviceaccount <sa-name> -o yaml | grep imagePullSecrets
   ```
5. Verify the pull secret content:
   ```
   kubectl get secret <pull-secret-name> -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | jq .
   ```
6. Test DNS resolution and network reach from a debug pod on the affected node:
   ```
   kubectl run debug --image=busybox --rm -it -- sh
   nslookup <registry-hostname>
   wget -O- https://<registry-hostname>/v2/
   ```

## Possible Fixes

- **Tag does not exist**: correct the tag in the Deployment/Pod spec; use a digest reference for immutable pinning
- **Missing pull secret**: create a Secret of type `kubernetes.io/dockerconfigjson` and add it to the ServiceAccount or pod spec:
  ```
  kubectl create secret docker-registry regcred \
    --docker-server=<registry> \
    --docker-username=<user> \
    --docker-password=<password>
  kubectl patch serviceaccount default -p '{"imagePullSecrets":[{"name":"regcred"}]}'
  ```
- **Expired credentials**: rotate the robot account token and update the Secret:
  ```
  kubectl create secret docker-registry regcred ... --dry-run=client -o yaml | kubectl apply -f -
  ```
- **Rate limiting**: authenticate to Docker Hub even for public images, or mirror frequently used images to an internal registry
- **Network issue**: verify node egress rules; add an NAT gateway or VPC peering route; check corporate proxy settings

## Notes

- `ErrImagePull` is the immediate error; `ImagePullBackOff` is the back-off state Kubernetes enters after repeated failures — both indicate the same root cause
- Kubernetes retries with exponential back-off up to ~5 minutes between attempts; fixing the root cause will usually allow the pod to self-heal on the next retry
- In air-gapped environments, all images must be pre-loaded or mirrored to an internal registry; the external registry URL will never be reachable
- Avoid using `latest` in production — it makes debugging harder and breaks caching on nodes
