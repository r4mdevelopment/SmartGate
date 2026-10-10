[SmartGate](../../../README.md) / [Documentation](../README.md) / Services

[English](services.md) · [Русский](../../ru/providers/services.md)

# Adding services

Select services in [config/services.toml](../../../config/services.toml). The main GitHub Actions configuration uses this catalog. No program changes are needed: edit the file in your GitHub fork and apply changes through the verified workflow.

## Available presets

| Preset | Initial state | Scope |
| --- | --- | --- |
| `chatgpt` | Enabled | ChatGPT / OpenAI |
| `claude` | Disabled | Claude and Anthropic |
| `gemini` | Disabled | Gemini / AI Studio and selected API domains |
| `spotify` | Disabled | Spotify and its CDN |
| `notion` | Disabled | Notion |
| `deepl` | Disabled | DeepL |
| `cursor` | Disabled | Cursor and selected supporting domains |

The presets were checked against the official [GeoHide RU hosts](https://dns.geohide.ru:8443/static/formats/ru/hosts.txt) on 8 October 2026. A domain's presence in the source does not guarantee application availability: the list and relay can change, and the service may need additional addresses. Full acceptance of all application scenarios has not been confirmed for the new presets; checking individual domains or a reduced set does not validate the entire preset.

## Enable an available service

1. In the relevant file section, replace `enabled = false` with `enabled = true`. Start with one service.
2. Save the change to your fork's default branch. If automatic apply is already enabled, first set `SMARTGATE_APPLY_ENABLED=false` so the schedule cannot apply the new catalog before review.
3. Run **Actions → SmartGate sync → preview**, `apply=false`, `bootstrap=false`. Review new domains, selected routes and blocking reasons.
4. Make sure the plan fits the limits. Defaults allow up to 20 operations per run and 100 managed records in total; outside bootstrap, replace/delete operations are also limited by `max_change_ratio=0.25` relative to the number of owned records. A large preset may exceed the limit; do not enable the entire GeoHide list at once. If you need another limit, change it deliberately in your fork and repeat preview.
5. For a DNS adapter approved for production apply, run **sync**, `apply=true`, `bootstrap=false`. Bootstrap is only for an initially empty target, not for adding a service to existing routes.
6. On a separate device, check login, core features, files and the application you use. Then you can restore the previous schedule.

## Add your own preset

Copy one of the disabled sections and give it a unique name. For example:

```toml
[services.example]
enabled = false
include_suffixes = ["service.example", "api.service.example"]
required_domains = ["service.example"]
exclude_suffixes = []
provider_order = ["geohide-ru", "geohide-eu"]
allow_subtree_rewrites = true
```

This template uses demonstration domains. Replace them with your service's addresses and make sure the selected relay actually publishes them. SmartGate selects addresses from the source and checks TLS; adding a name to the catalog does not itself create a relay route.

| Field | Meaning |
| --- | --- |
| `enabled` | Enables domain selection for this preset |
| `include_suffixes` | A domain and its subdomains; specify a narrow scope |
| `required_domains` | Core addresses whose missing routes make the run degraded |
| `exclude_suffixes` | Exclusions; a parent rule that intercepts an exclusion must not be published |
| `provider_order` | Priority of relay sources registered in the configuration |
| `allow_subtree_rewrites` | Explicit consent to DNS rules that may affect subdomains |

Gemini deliberately has no broad `google.com` or `googleapis.com` rule. A broad parent suffix may redirect other applications. Check shared login/CDN domains and the application's own DNS separately; the preset does not promise complete coverage of every service scenario.

## Removal and updates

`enabled=false` stops selecting new routes, but with `allow_prune=false`, existing DNS records are not deleted automatically. Disabling a preset alone does not restore previous DNS answers. Removing published rules requires a separate reviewed plan with pruning enabled.

For new service features, compare its addresses with the current relay source, update suffixes, then repeat preview and device checks. GeoHide may publish thousands of domains; SmartGate uses only explicitly selected presets.
