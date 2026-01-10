# Tinkoff Trading Bot Helm Chart

Helm chart for deploying Tinkoff Trading Bot to Kubernetes.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- PersistentVolume provisioner (for state.db and market_data_cache)

## Installation

### Quick start (sandbox mode)

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot \
  --set secrets.TOKEN="your-sandbox-token" \
  --set image.repository="ghcr.io/YOUR_ORG/tinkoff-trading-bot" \
  --set image.tag="latest"
```

Or use example values:

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot \
  -f helm/tinkoff-trading-bot/values-sandbox.yaml \
  --set secrets.TOKEN="your-sandbox-token"
```

### With custom values file

1. Copy `values.yaml` and customize:

```bash
cp helm/tinkoff-trading-bot/values.yaml my-values.yaml
# Edit my-values.yaml
```

2. Install:

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot -f my-values.yaml
```

### Real trading mode (requires explicit confirmation)

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot \
  -f helm/tinkoff-trading-bot/values-real.yaml \
  --set secrets.TOKEN="your-real-token"
```

**WARNING**: Real trading mode requires `I_KNOW_WHAT_I_AM_DOING=true` in values.

## Configuration

### Required values

- `secrets.TOKEN`: Tinkoff API token (can be set via UI after deployment in job mode)

### Key parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `image.repository` | Container image repository | `ghcr.io/YOUR_ORG/tinkoff-trading-bot` |
| `image.tag` | Container image tag | `latest` |
| `env.SANDBOX` | Sandbox mode (true/false) | `true` |
| `env.UI_ENABLED` | Enable web UI | `true` |
| `env.AUTO_START` | Auto-start trading on boot | `false` (job mode) |
| `persistence.enabled` | Enable PVC for state | `true` |
| `persistence.size` | PVC size | `2Gi` |
| `service.enabled` | Expose UI service | `true` |
| `service.type` | Service type | `ClusterIP` |

### Instruments configuration

Edit `values.yaml` section `instrumentsConfig` to configure strategies and instruments.

## Accessing UI

After installation, port-forward to access UI:

```bash
kubectl port-forward -n tinkoff-bot svc/tinkoff-bot-tinkoff-trading-bot-ui 8080:80
```

Open `http://localhost:8080/` in browser.

## Upgrading

```bash
helm upgrade tinkoff-bot ./helm/tinkoff-trading-bot -f my-values.yaml
```

## Uninstalling

```bash
helm uninstall tinkoff-bot
```

**Note**: PVC is not deleted by default. To remove it:

```bash
kubectl delete pvc -n tinkoff-bot tinkoff-bot-tinkoff-trading-bot-data
```

## Testing the chart

### Dry-run (validate templates)

```bash
helm template tinkoff-bot ./helm/tinkoff-trading-bot \
  -f helm/tinkoff-trading-bot/test-values.yaml \
  --debug
```

### Lint

```bash
helm lint ./helm/tinkoff-trading-bot
```

### Install with dry-run

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot \
  --dry-run --debug \
  -f helm/tinkoff-trading-bot/test-values.yaml
```

## Values reference

See `values.yaml` for all available configuration options.

### Overriding instruments config

You can override the entire `instrumentsConfig` section:

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot \
  --set-file instrumentsConfig=my-instruments.json
```

Or inline:

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot \
  --set-json 'instrumentsConfig={"instruments":[...]}'
```
