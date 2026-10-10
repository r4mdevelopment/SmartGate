[SmartGate](../../../README.md) / [Documentation](../README.md) / DNS layer

[English](dns.md) · [Русский](../../ru/architecture/dns.md)

# DNS layer

The DNS provider publishes answers for selected domains. The relay forwards traffic to the service, while SmartGate checks available routes and manages DNS changes through an adapter.

The shared interface includes reading, creating, deleting, and replacing records. The API format, authentication, and identifier behavior belong to the specific adapter. The current working integration is described in the [NextDNS guide](../providers/dns/nextdns.md).

## DNS verification

The shared verification component queries the selected target's HTTPS DoH endpoint. It does not change device DNS or receive API credentials. Redirects and system proxies are disabled; the address must not contain embedded credentials, a query, or a fragment.

For each domain, A must contain exactly the selected IPv4. AAAA, HTTPS, and SVCB must return an empty successful answer so the client cannot bypass the IPv4 route. The question ID, name, type, response code, completeness, and time limit are checked; an unexpected CNAME does not count as confirmation.

## Target and capabilities

The target describes the provider type, API origin, scope, and DoH. Its fingerprint distinguishes servers and profiles even when they share the same instance name. Adapter capabilities separately indicate ID stability, subdomain semantics, and eligibility for production application. A shared interface alone does not prove these properties. NextDNS is registered with production apply; AdGuard Home and Technitium are registered as read-only references with unverified subdomain semantics and value-based identity. The live compatibility harness does not change these built-in capabilities.
