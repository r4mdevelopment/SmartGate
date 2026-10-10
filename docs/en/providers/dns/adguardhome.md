[SmartGate](../../../../README.md) / [Documentation](../../README.md) / AdGuard Home

[English](adguardhome.md) · [Русский](../../../ru/providers/dns/adguardhome.md)

# AdGuard Home

A reference adapter for your own DNS server. Connection and the CRUD contract are covered by local HTTP tests; production `--apply` is blocked. Live verification of a server has not yet been performed.

## Connection

Use the [configuration example](../../../../config/dns/adguardhome.example.toml). It requires an HTTPS API origin in `SMARTGATE_DNS_API_URL`, username and password in `SMARTGATE_DNS_USERNAME` / `SMARTGATE_DNS_PASSWORD`, a dedicated scope in `SMARTGATE_DNS_NAMESPACE` and an HTTPS DoH endpoint in `SMARTGATE_DNS_DOH_URL`. Scope names the dedicated server or its isolated configuration; this rewrites API does not support a separate profile.

Basic credentials are sent only to the API origin. DoH is checked with a separate client without API authentication. TLS verification is required; redirects and system proxies are disabled. The adapter does not install the server or change its global settings.

## Records and ownership limitations

The official API returns domain/answer/enabled without a persistent record ID. The adapter calculates a value-pair key to find a record; that key does not prove that a deleted and recreated record belongs to SmartGate. Production create/delete/replace are therefore prohibited even with explicit `--apply`.

IPv6 and CNAME remain visible as foreign records. Duplicates, disabled rules and unsupported domain syntax block reading instead of allowing rules to be lost while building a plan. Subdomain semantics and A/AAAA/HTTPS/SVCB require separate live verification.

An isolated compatibility harness can explicitly enable reference CRUD directly in the adapter. Add/delete use POST; update uses PUT with target/update. Every result is confirmed by reading. An unknown write outcome requires recovery; there are no automatic retries. Such a check does not enable production apply or change built-in capabilities after a successful live run.

The API 0.107 contract is pinned to the [official OpenAPI](https://github.com/AdguardTeam/AdGuardHome/blob/336e9c9df4efa3fb5918e7f33827300805f46bf3/openapi/openapi.yaml); the source version is stored in test metadata.
