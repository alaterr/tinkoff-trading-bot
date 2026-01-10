# Helm Installation Guide

## Prerequisites

- Helm 3.0+
- Kubernetes cluster with PV provisioner
- Docker image built and pushed to registry

## Quick Start

### 1. Build and push image

```bash
export IMG="ghcr.io/YOUR_ORG/tinkoff-trading-bot:latest"
docker build -t "$IMG" .
docker push "$IMG"
```

### 2. Validate chart

```bash
helm lint ./helm/tinkoff-trading-bot
helm template tinkoff-bot ./helm/tinkoff-trading-bot \
  -f helm/tinkoff-trading-bot/test-values.yaml \
  --debug
```

### 3. Install (sandbox)

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot \
  --set image.repository="ghcr.io/YOUR_ORG/tinkoff-trading-bot" \
  --set image.tag="latest" \
  --set secrets.TOKEN="your-sandbox-token"
```

### 4. Access UI

```bash
kubectl port-forward -n tinkoff-bot svc/tinkoff-bot-tinkoff-trading-bot-ui 8080:80
```

Open http://localhost:8080

## Custom Configuration

### Using values file

```bash
# Edit values
vim helm/tinkoff-trading-bot/values.yaml

# Install
helm install tinkoff-bot ./helm/tinkoff-trading-bot -f helm/tinkoff-trading-bot/values.yaml
```

### Override specific values

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot \
  --set image.repository="my-registry/tinkoff-bot" \
  --set image.tag="v1.0.0" \
  --set secrets.TOKEN="token" \
  --set persistence.size="5Gi" \
  --set service.type="NodePort"
```

## Real Trading Mode

**WARNING**: Requires explicit confirmation.

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot \
  -f helm/tinkoff-trading-bot/values-real.yaml \
  --set secrets.TOKEN="your-real-token" \
  --set image.repository="ghcr.io/YOUR_ORG/tinkoff-trading-bot"
```

## Upgrade

```bash
helm upgrade tinkoff-bot ./helm/tinkoff-trading-bot \
  --set image.tag="new-tag" \
  -f my-values.yaml
```

## Uninstall

```bash
helm uninstall tinkoff-bot
# PVC is preserved by default
kubectl delete pvc -n tinkoff-bot tinkoff-bot-tinkoff-trading-bot-data  # if needed
```

## Troubleshooting

### Check pod status

```bash
kubectl get pods -n tinkoff-bot
kubectl logs -n tinkoff-bot -l app.kubernetes.io/name=tinkoff-trading-bot
```

### Verify configuration

```bash
kubectl get configmap -n tinkoff-bot tinkoff-bot-tinkoff-trading-bot-config -o yaml
kubectl get secret -n tinkoff-bot tinkoff-bot-tinkoff-trading-bot-secrets -o yaml
```

### Test UI connectivity

```bash
kubectl exec -n tinkoff-bot -it deployment/tinkoff-bot-tinkoff-trading-bot -- curl localhost:8000/api/status
```
