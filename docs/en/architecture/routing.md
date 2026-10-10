[SmartGate](../../../README.md) / [Documentation](../README.md) / Route selection

[English](routing.md) · [Русский](../../ru/architecture/routing.md)

# Route selection

SmartGate selects one IPv4 endpoint for each explicitly known domain. Routing receives the service catalog, relay source snapshots, TLS check results, and information about records owned by SmartGate. Selection makes no network requests and does not change DNS.


## On this page

- [Selection order](#selection-order)
- [Freshness and required domains](#freshness-and-required-domains)
- [Changing the relay provider](#changing-the-relay-provider)
- [Application boundary](#application-boundary)

## Selection order

| Condition | Decision |
| --- | --- |
| The current owned endpoint is present in a fresh source, TLS passed, and the policy is unchanged | Retain the current route |
| The current endpoint has a final failed result, and a verified fallback exists | Select the fallback |
| The current endpoint is present in a fresh source, the policy is unchanged, and the check returned unknown | Retain the current route with a degraded reason |
| The current endpoint is absent from a fresh source, and a verified replacement exists | Select the replacement |
| The policy has changed, and a verified endpoint exists under the new order | Select the route again |
| No fresh verified endpoint exists | Retain the owned route with a degraded reason; mark a new domain unavailable |
| An owned domain is explicitly disabled or excluded from the catalog | Mark remove for subsequent deletion rule checks |

A new endpoint is selected by provider order in `provider_order`, then numeric `priority`, then numeric IPv4. Lower priority values are preferred. TLS connection time is not used for sorting: latency from the controller does not describe client latency.

`failed` is the final outcome of the entire bounded HealthChecker check. Conflicting results for one endpoint are treated as unknown. Without confirmed ownership, a record cannot be retained solely on the basis of the selections cache.

## Freshness and required domains

The controller passes a single run-start timestamp to source fetching, validation, and routing. A fresh snapshot has that timestamp and is not marked stale. A snapshot from a previous run does not permit creation or replacement. If fetching a source fails, a valid cache up to 24 hours old is used only for route retention and diagnostics. An older snapshot is rejected before routing. Even without an eligible snapshot, existing owned records are retained with a degraded reason; new routes are not created from cache.

A TLS result must belong to the current run: `checked_at` must be between the run start and its 20-minute limit. An old successful TLS result cannot serve as new confirmation of availability.

If a required domain is absent from fresh eligible sources, new routes are blocked for the entire service. A failed TLS check for a required domain is reported as a required failure; for an optional domain, as an optional failure. A separate availability metric counts the proportion of service domains with a confirmed route and no degraded reason; the denominator also includes owned domains and domains known from saved sources. Each enabled service must reach at least 85%, and all required domains must be confirmed. A retained degraded route does not count as available. Application access to the service requires a separate check even when TLS succeeds.

Routing uses only domains declared by sources, required catalog domains, and records already owned by SmartGate. Subdomains are not added automatically.

## Changing the relay provider

For your own relay, prepare a manifest using the [provider format](../providers/relays.md) and place its ID first in `provider_order`. Changing the order changes the routing policy digest and permits reselection after a successful check of the new endpoint.

For example, changing `["geohide-ru", "own-nl"]` to `["own-nl", "geohide-ru"]` gives preference to your own relay. Without a verified replacement, the current owned route is retained with a degraded reason. The DNS layer receives the same `DesiredRewrite(domain, ipv4)` model regardless of the endpoint's origin.

## Application boundary

`select_routes` returns select, retain, unavailable, or remove decisions. `to_desired` converts select/retain into target IPv4 rewrites; the other decisions are not included in that list.

A domain's absence from the target list is not a deletion request. Remove is a separate intent that the synchronization layer must check against ownership, preconditions, and `allow_prune`. Durable state and the journal are implemented in [StateStore](../operations/state.md). Applying DNS operations and recovering from failures remain the responsibility of the separate reconcile layer.

---

[All documents](../README.md) · [Home](../../../README.md) · [Actions setup](../operations/actions.md) · [Troubleshooting](../operations/runbook.md)
