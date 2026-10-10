[SmartGate](../../../../README.md) / [Documentation](../../README.md) / Technitium

[English](technitium.md) · [Русский](../../../ru/providers/dns/technitium.md)

# Technitium DNS Server

A reference adapter for API v15+ that reads an existing dedicated authoritative zone and verifies the CRUD contract. Production apply and automatic zone creation are prohibited. Live verification has not yet been performed.

## Connection

The [configuration example](../../../../config/dns/technitium.example.toml) uses the HTTPS API origin `SMARTGATE_DNS_API_URL`, Bearer API token `SMARTGATE_DNS_API_KEY`, existing canonical zone `SMARTGATE_DNS_NAMESPACE` and HTTPS DoH `SMARTGATE_DNS_DOH_URL`. Give a dedicated user minimal permissions for that zone. The adapter does not create users, tokens or zones.

The token is sent only in Authorization, never in a URL or form. API and DoH use different clients. TLS verification, redirects/system proxy prohibitions, and request/time limits are enabled.

## Scope and DNS semantics

An authoritative zone is not a universal rewrite table. Creating a parent zone for a third-party service can intercept all its subdomains and other DNS types. This adapter never creates or deletes zones: every request explicitly names a zone prepared in advance.

Production Smart DNS needs a separately verified policy for exact/subtree handling and A/AAAA/HTTPS/SVCB, including preservation of other answers. If the selected zone does not provide these semantics, the target is unsuitable for this scenario. Having a VPS or API token alone does not solve this.

## Records and readiness

All record types remain visible, including SOA, NS, AAAA, HTTPS and SVCB. An unsupported owner name, disabled record, duplicate value or different zone in a response blocks operation. The record key reflects its value and some metadata; comments or a marker do not prove its lifecycle or grant permission to change external records. Therefore, `--apply` is blocked.

Reference CRUD is allowed only through explicit use by an isolated compatibility harness: adding A does not overwrite the RRset, and PTR and reverse-zone creation are disabled. Replace preserves the owner and metadata; every operation's result is confirmed by reading. A lost response requires recovery without retry. These local checks do not replace live acceptance. A successful isolated live harness does not change built-in capabilities or remove the production apply prohibition.

The contract is pinned to the [official APIDOCS](https://github.com/TechnitiumSoftware/DnsServer/blob/736bd562bea536f1dd93692ab70ad909ae03d16a/APIDOCS.md).
