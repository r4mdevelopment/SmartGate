"""Offline regression checks for the live harness DNS response validation."""

import runpy
from pathlib import Path

import dns.message
import dns.rrset
import httpx
import pytest

from smartgate.errors import SafetyBlocked

dns_answers = runpy.run_path(
    str(Path(__file__).parents[1] / "live" / "test_nextdns_compatibility.py")
)["dns_answers"]


def transport_for(case):
    def respond(request):
        question = dns.message.from_wire(request.content)
        answer = dns.message.make_response(question)
        if case == "wrong_id":
            answer.id = (question.id + 1) % 65536
        elif case == "wrong_question":
            answer.question = dns.message.make_query("other.example.com", "AAAA").question
        elif case == "query":
            answer = question
        else:
            answer.answer.append(
                dns.rrset.from_text("cloudflare.com.", 60, "IN", "AAAA", "2606:4700::1111")
            )
        return httpx.Response(200, content=answer.to_wire())

    return httpx.MockTransport(respond)


def test_matching_doh_response_is_accepted():
    with httpx.Client(transport=transport_for("valid")) as client:
        assert dns_answers(client, "abcdef", "cloudflare.com", "AAAA") == ["2606:4700::1111"]


@pytest.mark.parametrize("case", ["wrong_id", "wrong_question", "query"])
def test_unrelated_or_nonresponse_doh_message_is_rejected(case):
    with httpx.Client(transport=transport_for(case)) as client:
        with pytest.raises(SafetyBlocked, match="does not match"):
            dns_answers(client, "abcdef", "cloudflare.com", "AAAA")


def test_dns_observation_waits_for_bounded_propagation():
    observe = runpy.run_path(
        str(Path(__file__).parents[1] / "live" / "test_nextdns_compatibility.py")
    )["await_dns"]
    values = iter([["8.8.8.8"], ["1.1.1.1"]])
    elapsed = [0.0]
    observe(
        lambda: next(values),
        ["1.1.1.1"],
        clock=lambda: elapsed[0],
        sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
    )
    assert elapsed[0] == 5.0


def test_dns_observation_times_out_without_hiding_incompatibility():
    observe = runpy.run_path(
        str(Path(__file__).parents[1] / "live" / "test_nextdns_compatibility.py")
    )["await_dns"]
    elapsed = [0.0]
    with pytest.raises(SafetyBlocked, match="did not converge"):
        observe(
            lambda: ["8.8.8.8"],
            ["1.1.1.1"],
            clock=lambda: elapsed[0],
            sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        )
    assert elapsed[0] == 60.0
