from dataclasses import replace
from importlib import import_module

import pytest

from smartgate.health.http import HttpObservation
from smartgate.monitor import MonitorReport, RouteObservation


def report(failed, count=10, classification="timeout", dns=True):
    routes = tuple(
        RouteObservation(
            f"d{i}.example.com",
            "8.8.8.8",
            dns,
            "confirmed",
            HttpObservation(
                "now",
                i >= failed,
                None if i < failed else 200,
                classification if i < failed else "response",
                1,
            ),
            ("8.8.8.8",),
            1.0,
        )
        for i in range(count)
    )
    return MonitorReport("now", "github_runner", routes)


@pytest.mark.parametrize(
    "failed,count,expected",
    [(5, 10, True), (4, 10, False), (5, 11, False), (6, 11, True), (4, 4, False), (25, 25, True)],
)
def test_hint_mass_threshold(failed, count, expected):
    assert import_module("smartgate.monitor").failover_needed(report(failed, count)) is expected


@pytest.mark.parametrize(
    "classification,dns",
    [("challenge", True), ("http_rejected", True), ("certificate_error", True), ("timeout", False)],
)
def test_http_rejections_and_dns_errors_do_not_trigger(classification, dns):
    assert not import_module("smartgate.monitor").failover_needed(
        report(10, classification=classification, dns=dns)
    )


def test_incomplete_snapshot_never_triggers_failover():
    assert not import_module("smartgate.monitor").failover_needed(
        replace(report(10), issues=("snapshot_changed",))
    )
