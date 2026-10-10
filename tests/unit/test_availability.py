from dataclasses import replace

import pytest

from smartgate.availability import measure_availability, meets_availability_threshold
from smartgate.catalog import ServiceCatalog
from smartgate.models import RouteDecision, ServicePolicy, StateDocument
from tests.reconcile_fakes import target


def policy(name="one"):
    return ServicePolicy(
        name, True, (name + ".example",), (name + ".example",), (), ("relay",), True
    )


def decision(domain, available=True):
    candidate = target(domain, "8.8.8.8").candidate if available else None
    return RouteDecision(
        domain,
        candidate,
        "select" if available else "unavailable",
        "verified" if available else "degraded: optional domain has no fresh verified route",
    )


@pytest.mark.parametrize(
    "good,total,expected", [(17, 20, True), (16, 20, False), (6, 7, True), (11, 13, False)]
)
def test_threshold_uses_exact_counts_without_percentage_rounding(good, total, expected):
    catalog = ServiceCatalog((policy(),))
    values = tuple(
        decision("one.example" if i == 0 else f"d{i}.one.example", i < good) for i in range(total)
    )
    measured = measure_availability(catalog, values, StateDocument(1, "abcdef", None))
    assert measured[0].available == good and measured[0].total == total
    assert meets_availability_threshold(measured) is expected


def test_successful_service_cannot_compensate_for_another_failed_service():
    catalog = ServiceCatalog((policy("one"), policy("two")))
    values = tuple(decision("one.example" if i == 0 else f"d{i}.one.example") for i in range(100))
    values += (decision("two.example"), decision("bad.two.example", False))
    measured = measure_availability(catalog, values, StateDocument(1, "abcdef", None))
    assert not meets_availability_threshold(measured)


def test_required_domain_failure_cannot_be_hidden_by_99_percent_coverage():
    values = (decision("one.example", False),) + tuple(
        decision(f"d{i}.one.example") for i in range(99)
    )
    measured = measure_availability(
        ServiceCatalog((policy(),)), values, StateDocument(1, "abcdef", None)
    )
    assert measured[0].available == 99 and not measured[0].required_ok
    assert not meets_availability_threshold(measured)


def test_unknown_retained_route_is_not_counted_as_working():
    value = replace(
        decision("one.example"),
        action="retain",
        reason="degraded: current route verification unknown",
    )
    measured = measure_availability(
        ServiceCatalog((policy(),)), (value,), StateDocument(1, "abcdef", None)
    )
    assert measured[0].available == 0 and not meets_availability_threshold(measured)


def test_disabled_services_are_excluded_and_empty_coverage_is_not_success():
    assert not measure_availability(
        ServiceCatalog((replace(policy(), enabled=False),)), (), StateDocument(1, "abcdef", None)
    )
    assert not meets_availability_threshold(())


def test_cached_domain_missing_from_fresh_decisions_stays_in_denominator():
    from smartgate.models import RelaySnapshot
    from tests.reconcile_fakes import NOW

    catalog = ServiceCatalog((policy(),))
    snapshot = RelaySnapshot(
        "relay", NOW, "fixture", (target("lost.one.example", "8.8.8.8").candidate,), ()
    )
    state = StateDocument(1, "abcdef", None, last_sources=(snapshot,))
    measured = measure_availability(catalog, (decision("one.example"),), state)
    assert measured[0].available == 1 and measured[0].total == 2
    assert not meets_availability_threshold(measured)


def test_overlapping_service_scopes_are_measured_independently():
    broad = policy("one")
    narrow = replace(
        broad,
        id="narrow",
        include_suffixes=("small.one.example",),
        required_domains=("small.one.example",),
    )
    values = tuple(decision("one.example" if i == 0 else f"d{i}.one.example") for i in range(100))
    values += (decision("small.one.example"), decision("bad.small.one.example", False))
    measured = measure_availability(
        ServiceCatalog((broad, narrow)), values, StateDocument(1, "abcdef", None)
    )
    assert measured[1].available == 1 and measured[1].total == 2
    assert not meets_availability_threshold(measured)
