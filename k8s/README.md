## Kubernetes deployment (kustomize)

### Build & push image

Set your registry and tag:

```bash
export IMG="ghcr.io/YOUR_ORG/tinkoff-trading-bot:latest"
docker build -t "$IMG" .
docker push "$IMG"
```

### Install (sandbox default)

```bash
kubectl apply -k k8s/overlays/sandbox
```

### Configure secrets

Edit secret values (TOKEN/ACCOUNT_ID) before applying:

```bash
kubectl -n tinkoff-bot apply -f k8s/base/secret.yaml
```

### Kill-switch

Enable (creates file `/etc/kill-switch/kill.switch` inside the pod):

```bash
kubectl -n tinkoff-bot apply -f k8s/base/kill-switch-configmap.yaml
```

Disable:

```bash
kubectl -n tinkoff-bot delete -f k8s/base/kill-switch-configmap.yaml
```

### Real mode (explicit)

This repo has a safety latch: if `SANDBOX=false` then `I_KNOW_WHAT_I_AM_DOING=true` is required.

```bash
kubectl apply -k k8s/overlays/real
```

