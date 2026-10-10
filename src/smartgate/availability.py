"""Per-service verified route coverage; independent from DNS mutation safety."""

from smartgate.catalog import ServiceCatalog, suffix_matches
from smartgate.models import RouteDecision, ServiceAvailability, StateDocument

MIN_ROUTE_AVAILABILITY_PERCENT = 85


def measure_availability(
    catalog: ServiceCatalog, decisions: tuple[RouteDecision, ...], state: StateDocument
) -> tuple[ServiceAvailability, ...]:
    known = {d.domain for d in decisions} | {r.domain for r in state.managed_records}
    known.update(c.domain for source in state.last_sources for c in source.candidates)
    working = {
        d.domain
        for d in decisions
        if d.action in {"select", "retain"}
        and d.candidate is not None
        and not d.reason.startswith("degraded:")
    }
    result = []
    for service in catalog.services:
        if not service.enabled:
            continue
        domains = {
            d
            for d in known
            if any(suffix_matches(d, suffix) for suffix in service.include_suffixes)
            and not any(suffix_matches(d, suffix) for suffix in service.exclude_suffixes)
        }
        domains.update(service.required_domains)
        result.append(
            ServiceAvailability(
                service.id,
                len(domains & working),
                len(domains),
                set(service.required_domains) <= working,
            )
        )
    return tuple(result)


def meets_availability_threshold(values: tuple[ServiceAvailability, ...]) -> bool:
    return (
        bool(values)
        and len({v.service_id for v in values}) == len(values)
        and all(
            v.required_ok
            and 0 <= v.available <= v.total
            and v.total > 0
            and v.available * 100 >= MIN_ROUTE_AVAILABILITY_PERCENT * v.total
            for v in values
        )
    )
