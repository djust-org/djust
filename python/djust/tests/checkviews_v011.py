"""Synthetic LiveView fixtures for the ``djust.V011`` sticky-child opt-in check.

These classes live in an importable dotted module so the V011 scanner's
``import_string`` resolution (``djust.tests.checkviews_v011.OptedInChild`` …)
succeeds during ``check_sticky_child_optin``. They are NOT real views — they
exist purely so the V011 template scan has resolvable child classes and so
the parent-lookup (by ``template_name``) has parent LiveView subclasses to
match against.

ADR-018 Decision 5 enforcement: a sticky child with
``enable_state_snapshot = True`` whose embedding parent does NOT opt in is a
misconfiguration. V011 flags it; these fixtures supply every combination
(child opted-in / not, parent opted-in / not).
"""

from __future__ import annotations

from djust import LiveView

# Template filenames the V011 scanner-tests write into a tmp template dir.
# A parent class's ``template_name`` must equal the template's relative path
# for the V011 parent-lookup to match.


class OptedInChild(LiveView):
    """Sticky child that DOES opt into snapshot persistence."""

    template = "<div>opted-in child</div>"
    sticky = True
    sticky_id = "v011_optin_child"
    enable_state_snapshot = True


class NotOptedInChild(LiveView):
    """Sticky child that does NOT opt into snapshot persistence."""

    template = "<div>not-opted-in child</div>"
    sticky = True
    sticky_id = "v011_no_optin_child"
    # NOTE: no ``enable_state_snapshot`` — nothing to persist, nothing to flag.


class OptedInParent(LiveView):
    """Parent LiveView that DOES opt in — matches a misconfig-free template."""

    template_name = "v011_parent_optin.html"
    enable_state_snapshot = True


class NotOptedInParent(LiveView):
    """Parent LiveView that does NOT opt in — the V011 trigger when it
    embeds an opted-in sticky child."""

    template_name = "v011_parent_no_optin.html"
    # NOTE: no ``enable_state_snapshot`` opt-in.


class SecondNotOptedInParent(LiveView):
    """A second non-opted-in parent for the multi-parent test."""

    template_name = "v011_parent_multi.html"
    # NOTE: no ``enable_state_snapshot`` opt-in.


class MultiOptedInParent(LiveView):
    """A second parent that DOES opt in and shares its ``template_name`` with
    :class:`SecondNotOptedInParent` — so one template maps to TWO parent
    classes. This exercises V011's multi-parent loop (``parent_map`` value is
    a list of length > 1): the same opted-in sticky child is reachable from a
    non-opted-in parent (flagged) and an opted-in parent (silent)."""

    template_name = "v011_parent_multi.html"
    enable_state_snapshot = True
