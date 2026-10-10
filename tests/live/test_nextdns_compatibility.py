"""Explicit opt-in test; writes only to an empty, separate NextDNS profile."""

import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import dns.message
import dns.rdatatype
import httpx
import pytest

from smartgate.compatibility import ensure_empty_profile, live_settings
from smartgate.errors import SafetyBlocked
from smartgate.http import HttpClient

pytestmark = pytest.mark.live


def persist(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def record_id(value):
    if not isinstance(value, str) or not value:
        raise SafetyBlocked("API record ID is not confirmed")
    return quote(value, safe="")


def dns_answers(client, profile_id, domain, qtype):
    request = dns.message.make_query(domain, qtype)
    response = client.post(
        "https://dns.nextdns.io/" + profile_id,
        content=request.to_wire(),
        headers={"Content-Type": "application/dns-message", "Accept": "application/dns-message"},
    )
    response.raise_for_status()
    answer = dns.message.from_wire(response.content)
    if answer.id != request.id or not request.is_response(answer):
        raise SafetyBlocked("DoH response does not match the question")
    return sorted(
        item.to_text()
        for rrset in answer.answer
        if rrset.rdtype == dns.rdatatype.from_text(qtype)
        for item in rrset
    )


def await_dns(query, expected, *, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + 60.0
    while True:
        if query() == expected:
            return
        remaining = deadline - clock()
        if remaining <= 0:
            raise SafetyBlocked("DNS answers did not converge within 60 seconds")
        sleep(min(5.0, remaining))


def test_nextdns_rewrites_and_profile_dns():
    settings = live_settings(os.environ)
    root = Path(__file__).parents[2]
    folder = root / ".smartgate" / "compatibility" / settings.test_profile_id
    journal_path = folder / "journal.json"
    if journal_path.exists():
        previous = json.loads(journal_path.read_text(encoding="utf-8"))
        if previous.get("phase") != "clean":
            pytest.fail("Previous live journal requires inspection of the disposable profile")
    api = HttpClient(settings.api_key)
    doh = httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
    endpoint = "/profiles/" + settings.test_profile_id + "/rewrites"
    journal = {"phase": "prepared", "intents": [], "results": [], "cleanup": []}
    evidence = {"checked": False, "update_strategy": "delete_create", "api": [], "dns": []}
    test_root = "smartgate-" + uuid.uuid4().hex[:12] + ".example.com"
    test_child = "child." + test_root
    created_ids = set()
    failed = False

    def listing():
        return api.list_pages(endpoint)

    def create(name, content):
        journal["intents"].append({"name": name, "content": content})
        persist(journal_path, journal)
        result = api.request("POST", endpoint, {"name": name, "content": content})
        evidence["api"].append({"method": "POST", "result": result})
        data = result.get("data")
        identifier = data.get("id") if isinstance(data, dict) else None
        if not isinstance(identifier, str) or not identifier:
            pytest.fail("Create response needs a confirmed record ID; inspect runtime evidence")
        created_ids.add(identifier)
        journal["results"].append({"id": identifier, "name": name, "content": content})
        persist(journal_path, journal)
        return identifier

    def delete(identifier):
        result = api.request("DELETE", endpoint + "/" + record_id(identifier))
        evidence["api"].append({"method": "DELETE", "result": result})
        assert not any(record.get("id") == identifier for record in listing())

    def query(domain, qtype):
        answers = dns_answers(doh, settings.test_profile_id, domain, qtype)
        evidence["dns"].append({"domain": domain, "qtype": qtype, "answers": answers})
        return answers

    try:
        ensure_empty_profile(listing())
        journal["phase"] = "applying"
        persist(journal_path, journal)
        baseline_aaaa = query("cloudflare.com", "AAAA")
        assert baseline_aaaa, "Baseline must include IPv6 to characterize rewrite suppression"
        query("cloudflare.com", "HTTPS")
        parent_id = create(test_root, "8.8.8.8")
        assert any(
            record.get("id") == parent_id
            and record.get("name") == test_root
            and record.get("content") == "8.8.8.8"
            for record in listing()
        ), "List payload must confirm ID/name/content"
        await_dns(lambda: query(test_root, "A"), ["8.8.8.8"])
        await_dns(lambda: query(test_child, "A"), ["8.8.8.8"])
        create(test_child, "1.1.1.1")
        await_dns(lambda: query(test_child, "A"), ["1.1.1.1"])
        delete(parent_id)
        create(test_root, "8.8.4.4")
        await_dns(lambda: query(test_root, "A"), ["8.8.4.4"])
        create("cloudflare.com", "8.8.8.8")
        await_dns(lambda: query("cloudflare.com", "A"), ["8.8.8.8"])
        await_dns(lambda: query("cloudflare.com", "AAAA"), [])
        await_dns(lambda: query("cloudflare.com", "HTTPS"), [])
        evidence["checked"] = True
    except BaseException:
        failed = True
        raise
    finally:
        cleanup_error = None
        try:
            if journal["intents"]:
                actual = listing()
                intents = {(item["name"], item["content"]) for item in journal["intents"]}
                owned = []
                for name, content in intents:
                    matches = [
                        record
                        for record in actual
                        if record.get("name") == name and record.get("content") == content
                    ]
                    if len(matches) > 1:
                        raise SafetyBlocked("Ambiguous test intent; manual cleanup required")
                    owned.extend(matches)
                for record in owned:
                    identifier = record.get("id")
                    if not isinstance(identifier, str):
                        raise SafetyBlocked("Unconfirmed test record; manual cleanup required")
                    # Empty initial profile plus exact persisted intent attributes this result.
                    delete(identifier)
                    journal["cleanup"].append(identifier)
                    persist(journal_path, journal)
                remaining = listing()
                if remaining:
                    raise SafetyBlocked("Disposable profile changed; remaining records preserved")
            journal["phase"] = "clean"
            persist(journal_path, journal)
        except BaseException as error:
            journal["phase"] = "cleanup_required"
            persist(journal_path, journal)
            cleanup_error = error
        finally:
            persist(folder / "evidence.json", evidence)
            api.close()
            doh.close()
        if cleanup_error is not None:
            pytest.fail("Live cleanup incomplete; inspect the local journal and disposable profile")
        if failed:
            evidence["checked"] = False
            persist(folder / "evidence.json", evidence)
