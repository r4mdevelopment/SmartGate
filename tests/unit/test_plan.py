from dataclasses import replace

import pytest

from smartgate.models import DesiredRewrite, DnsRecord, StateDocument
from smartgate.reconcile.plan import build_plan
from tests.reconcile_fakes import owned, policy

EMPTY = StateDocument(1, "abcdef", None)


def plan(desired=(), actual=(), state=EMPTY, removals=frozenset(), **options):
    return build_plan(tuple(desired), tuple(actual), state, removals, policy(**options))


def test_noop_create_replace_and_missing_source_retains():
    before, state = owned()
    assert plan([DesiredRewrite(before.domain, before.content)], [before], state).operations == ()
    created = plan([DesiredRewrite("new.example", "1.1.1.1")])
    assert created.operations[0].kind == "create"
    changed = plan([DesiredRewrite(before.domain, "1.1.1.1")], [before], state, max_change_ratio=1)
    assert changed.operations[0].before == before and changed.operations[0].kind == "replace"
    assert plan([], [before], state).operations == ()


@pytest.mark.parametrize("domain", ["chatgpt.com", "child.chatgpt.com", "com.example"])
def test_foreign_related_records_block(domain):
    foreign = DnsRecord("foreign", "chatgpt.com" if domain == "com.example" else domain, "9.9.9.9")
    wanted = "child.chatgpt.com" if domain == "com.example" else "chatgpt.com"
    assert plan([DesiredRewrite(wanted, "8.8.8.8")], [foreign]).blocked_reasons


@pytest.mark.parametrize(
    "actual",
    [
        (),
        (DnsRecord("owned-id", "chatgpt.com", "1.1.1.1"),),
        (DnsRecord("new-id", "chatgpt.com", "8.8.8.8"),),
    ],
)
def test_owned_drift_blocks(actual):
    before, state = owned()
    assert plan([DesiredRewrite(before.domain, "8.8.8.8")], actual, state).blocked_reasons


def test_prune_requires_explicit_owned_removal_and_allowance():
    before, state = owned()
    assert plan([], [before], state, frozenset({before.domain})).operations == ()
    result = plan(
        [], [before], state, frozenset({before.domain}), allow_prune=True, max_change_ratio=1
    )
    assert result.operations[0].kind == "delete" and result.operations[0].before == before
    assert plan(
        [], [before], state, frozenset({"foreign.example"}), allow_prune=True
    ).blocked_reasons


def test_count_ratio_and_bootstrap_limits():
    desired = [DesiredRewrite(f"d{i}.example", "8.8.8.8") for i in range(101)]
    assert plan(desired).blocked_reasons
    assert plan(desired[:21]).blocked_reasons
    assert not plan(desired[:20], bootstrap=True).blocked_reasons
    before, state = owned()
    assert plan([DesiredRewrite(before.domain, "1.1.1.1")], [before], state).blocked_reasons
    assert plan([], [before], state, bootstrap=True).blocked_reasons


def test_subtree_capability_and_duplicate_inputs_block():
    desired = DesiredRewrite("chatgpt.com", "8.8.8.8")
    assert plan([desired], subtree_capabilities=False).blocked_reasons
    assert plan([desired, desired]).blocked_reasons
    assert plan([desired], [DnsRecord("x", "manual.example", "9.9.9.9")] * 2).blocked_reasons


def test_shuffle_does_not_change_plan_and_foreign_unrelated_is_preserved():
    actual = [DnsRecord("z", "manual.example", "9.9.9.9")]
    desired = [DesiredRewrite("a.example", "8.8.8.8"), DesiredRewrite("b.example", "1.1.1.1")]
    assert plan(desired, actual) == plan(reversed(desired), actual)
    assert not plan(desired, actual).blocked_reasons


def test_parent_publication_cannot_cover_excluded_subtree_or_unaccepted_service():
    from smartgate.models import ServicePolicy

    service = ServicePolicy(
        "chat", True, ("chatgpt.com",), (), ("private.chatgpt.com",), ("relay",), True
    )
    ctx = replace(policy(), services=(service,))
    desired = (DesiredRewrite("chatgpt.com", "8.8.8.8"),)
    assert build_plan(desired, (), EMPTY, frozenset(), ctx).blocked_reasons
    rejected = replace(service, exclude_suffixes=(), allow_subtree_rewrites=False)
    assert build_plan(
        desired, (), EMPTY, frozenset(), replace(ctx, services=(rejected,))
    ).blocked_reasons
    assert build_plan(
        (DesiredRewrite("unknown.example", "8.8.8.8"),), (), EMPTY, frozenset(), ctx
    ).blocked_reasons


def test_prune_uses_the_removed_record_not_last_owned_record():
    first, state = owned("a.example", "8.8.8.8")
    second, other = owned("b.example", "1.1.1.1")
    second = replace(second, record_id="second-id")
    state = replace(
        state,
        managed_records=state.managed_records
        + (replace(other.managed_records[0], record_id="second-id"),),
        selections=state.selections + other.selections,
    )
    result = plan(
        [], [first, second], state, frozenset({first.domain}), allow_prune=True, max_change_ratio=1
    )
    assert not result.blocked_reasons
    assert result.operations[0].before == first
