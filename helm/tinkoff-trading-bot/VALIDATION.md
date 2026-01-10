# Helm Chart Validation Checklist

## Pre-install checks

### 1. Lint chart

```bash
helm lint ./helm/tinkoff-trading-bot
```

### 2. Template validation (dry-run)

```bash
helm template tinkoff-bot ./helm/tinkoff-trading-bot \
  -f helm/tinkoff-trading-bot/test-values.yaml \
  --debug > /tmp/rendered.yaml
```

Check rendered.yaml for:
- All resources have correct names
- Namespace is created (if enabled)
- ConfigMap contains valid JSON
- Deployment references correct ConfigMap/Secret/PVC names
- Service selector matches Deployment labels
- Volume mounts match volumes

### 3. Install dry-run

```bash
helm install tinkoff-bot ./helm/tinkoff-trading-bot \
  --dry-run --debug \
  -f helm/tinkoff-trading-bot/test-values.yaml \
  --set secrets.TOKEN="test-token"
```

## Resource name consistency

All resources use `{{ include "tinkoff-trading-bot.fullname" . }}`:
- ConfigMap: `{fullname}-config`
- Secret: `{fullname}-secrets`
- PVC: `{fullname}-data`
- Deployment: `{fullname}`
- Service: `{fullname}-ui`
- Kill-switch ConfigMap: `{fullname}-kill-switch`

## Required values

Minimum required for installation:
- `image.repository` (or override via --set)
- `secrets.TOKEN` (can be empty for job mode, set via UI)

## Common issues

1. **JSON parsing error**: Check `instruments_config.json` in ConfigMap is valid JSON
2. **PVC not found**: Ensure `persistence.enabled=true` and storage class exists
3. **Image pull error**: Verify `image.repository` and `image.tag` are correct
4. **Service not accessible**: Check `service.enabled=true` and port-forward
