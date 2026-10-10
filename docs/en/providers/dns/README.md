[SmartGate](../../../../README.md) / [Documentation](../../README.md) / DNS providers

[English](README.md) · [Русский](../../../ru/providers/dns/README.md)

# DNS providers

DNS and relay providers are selected independently. DNS tells a device where to connect; a relay forwards traffic. SmartGate checks routes, builds a bounded change plan, retains record ownership and confirms the result through the API and DoH.

## Choose an adapter

| Adapter | Connection | Readiness | Production apply |
| --- | --- | --- | --- |
| [NextDNS](nextdns.md) | API key + profile; the binding defines API/DoH addresses | Contract and isolated live checks passed | Allowed after configuring and verifying your profile |
| [AdGuard Home](adguardhome.md) | HTTPS API + Basic username/password + dedicated scope + DoH | Reference, local HTTP tests; live behavior/ownership unverified | Blocked |
| [Technitium](technitium.md) | HTTPS API + Bearer token + existing zone + DoH | Reference, local HTTP tests; live behavior/ownership unverified | Blocked |

The two reference adapters demonstrate different connection methods and locally verified API contracts. Even a successful isolated live test does not change their built-in production apply prohibition. They do not promise a ready replacement for managed DNS: missing persistent IDs, exact/subtree semantics and other DNS types require separate verification on your server.

## Configuration

Schema 2 separates `dns.provider` / `instance_id`, `dns.connection`, `dns.auth` and `dns.verify`. Shared variables use `SMARTGATE_DNS_*`. The `api_key` and `bearer` methods need an API key; Basic uses username/password. Settings for other adapters are not required.

Examples are in [config/dns](../../../../config/dns). GitHub Actions always reads [smartgate.actions.toml](../../../../config/smartgate.actions.toml); to switch adapters, copy the relevant DNS blocks from the example into it and configure the required env variables. Such a switch requires verification of the target and ownership state; users do not need a local Python installation for deployment. Endpoints must use HTTPS with a valid certificate. API authentication is never sent to DoH or relay endpoints.

## Your own VPS

Your VPS can host DNS, a relay or the controller. These are separate roles. Two VPS instances can separate DNS from a relay abroad, but two servers are not required. SmartGate is also useful with your own infrastructure: it checks routes and manages address changes, safe apply and recovery.

## A new adapter

The architecture does not limit the number of adapters. Each server needs a binding for its API, authentication, scope, record identity and verified capabilities. The [adapter template](adapter-template.md) describes the contract and readiness criteria. New adapters are registered explicitly; arbitrary plugins are not imported from configuration.
