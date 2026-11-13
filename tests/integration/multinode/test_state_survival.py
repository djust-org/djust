"""What survives a reconnect, a restart and a second process, with real processes (#4000).

Every test drives two (or more) uvicorn processes of ``tests.integration.multinode.app``
that share one session store and one channel layer, and chooses for each request
and each socket which process it reaches, as a load balancer without stickiness
may. A test pins the outcome as it is, including where state does NOT survive,
and reports it through ``survival.observe``: the published table
(``survival_table.py``, ``docs/website/guides/state-survival.md``) is checked
against these observations, cell by cell.

Tier: every test here starts processes, so the module is ``slow``: it runs on
pushes to ``main``, nightly and on a ``full-tests`` PR, not on an ordinary PR
(``tests/slow_tier.py``). The Redis rows need ``redis-server`` and
``channels_redis``; without them they skip, and only the database, file and
in-memory rows run.
"""

from __future__ import annotations

import pytest

from tests.integration.multinode.harness import Browser, Cluster, kinds, probe_from, probe_of

pytestmark = pytest.mark.slow

V = "tests.integration.multinode.app.views."
COUNTER = V + "CounterView"


def _open(browser: Browser, node: str, url: str, view: str):
    """A page load on ``node`` (the GET) and its socket on the same node."""
    status, body, _ = browser.get(node, url)
    assert status == 200, body[:300]
    socket = browser.socket(node)
    frames = socket.mount(view, url)
    assert kinds(frames) == ["mount"], frames
    return socket, probe_from(frames)


def _post(browser: Browser, node: str, url: str, event: str, params=None):
    status, payload = browser.post_event(node, url, event, params)
    assert status == 200, payload
    return payload, probe_of(payload["html"])


def _kept(value, expected) -> str:
    return "kept" if value == expected else "changed: %r" % (value,)


# ---------------------------------------------------------------------------
# Kinds of state
# ---------------------------------------------------------------------------


def test_ws_events_on_one_process(db_cluster, survival):
    browser = Browser(db_cluster)
    socket, _ = _open(browser, "A", "/counter/", COUNTER)
    socket.event("increment")
    second = socket.event("increment")
    probe = probe_from(second)
    for field in ("count", "client_count", "plain_state"):
        row = {
            "count": "persist_server",
            "client_count": "persist_client",
            "plain_state": "plain_state",
        }[field]
        survival.observe("state", row, "same", _kept(probe[field], 2))
    survival.observe(
        "state", "plain_attr", "same", _kept((probe["plain_attr"], probe["private_attr"]), (2, 2))
    )
    survival.observe("state", "not_saved", "same", _kept(probe["not_saved"], 2))
    survival.observe("state", "render", "same", "patches" if kinds(second) == ["patch"] else "?")
    socket.close()


def test_http_fallback_alternating_processes(db_cluster, survival):
    """GET on A, then page POSTs on B, A, B: each request builds a new view."""
    browser = Browser(db_cluster)
    status, body, _ = browser.get("A", "/counter/")
    assert status == 200
    answers = [_post(browser, node, "/counter/", "increment") for node in ("B", "A", "B")]
    probes = [probe for _, probe in answers]
    pids = [probe["pid"] for probe in probes]
    assert pids[0] == pids[2] == db_cluster["B"].pid and pids[1] == db_cluster["A"].pid
    survival.observe(
        "state",
        "persist_server",
        "http",
        "kept (1, 2, 3 across A, B, A)" if [p["count"] for p in probes] == [1, 2, 3] else "?",
    )
    reset = "lost: every request starts from the default"
    survival.observe(
        "state",
        "persist_client",
        "http",
        reset if {p["client_count"] for p in probes} == {1} else "?",
    )
    # #4107: what the view sets on self is saved, declared or not.
    kept = "kept (1, 2, 3 across A, B, A)"
    survival.observe(
        "state",
        "plain_state",
        "http",
        kept if [p["plain_state"] for p in probes] == [1, 2, 3] else "?",
    )
    both = [(p["plain_attr"], p["private_attr"]) for p in probes]
    survival.observe(
        "state", "plain_attr", "http", kept if both == [(1, 1), (2, 2), (3, 3)] else "? %r" % both
    )
    survival.observe(
        "state",
        "not_saved",
        "http",
        "lost: every request starts from the default"
        if {p["not_saved"] for p in probes} == {1}
        else "?",
    )
    # 1.3's rule: the view is rebuilt from what the GET's mount() saved on A;
    # no later request runs mount() again, on either process.
    survival.observe(
        "state",
        "mount",
        "http",
        "not run again: the view is rebuilt from its saved state"
        if {p["mounted_pid"] for p in probes} == {db_cluster["A"].pid}
        else "? %r" % ([p["mounted_pid"] for p in probes],),
    )
    survival.observe(
        "state",
        "handle_restore",
        "http",
        "called on every request" if all(p["restored"] for p in probes) else "?",
    )
    full = all("html" in payload and not payload.get("patches") for payload, _ in answers)
    survival.observe("state", "render", "http", "full HTML on every request" if full else "?")


def _render_after_reconnect(mount_frames, first, second) -> str:
    if (
        kinds(mount_frames) == ["mount"]
        and mount_frames[-1].get("html")
        and kinds(first) == ["html_update"]
        and kinds(second) == ["patch"]
    ):
        return "full HTML in the mount, full HTML for the first event, then patches"
    return "? mount %r, then %r, %r" % (kinds(mount_frames), kinds(first), kinds(second))


def _restored_counter(survival, column: str, probe, *, count: int, client: int) -> None:
    survival.observe(
        "state",
        "persist_server",
        column,
        {
            "ws": "restored",
            "restart": "restored (last completed save)",
        }[column]
        if probe["count"] == count
        else "lost",
    )
    survival.observe(
        "state",
        "persist_client",
        column,
        "restored from the browser's signed token" if probe["client_count"] == client else "lost",
    )
    saved = {"ws": "restored", "restart": "restored (last completed save)"}[column]
    survival.observe(
        "state", "plain_state", column, saved if probe["plain_state"] == count else "lost"
    )
    survival.observe(
        "state",
        "plain_attr",
        column,
        saved if (probe["plain_attr"], probe["private_attr"]) == (count, count) else "lost",
    )
    survival.observe(
        "state", "not_saved", column, "lost: the default" if probe["not_saved"] == 0 else "kept"
    )
    survival.observe(
        "state",
        "handle_restore",
        column,
        "called after the restore" if probe["restored"] else "not called",
    )


def test_ws_reconnect_to_another_process(db_cluster, survival):
    browser = Browser(db_cluster)
    socket_a, _ = _open(browser, "A", "/counter/", COUNTER)
    socket_a.event("increment")
    socket_a.event("increment")
    socket_a.close()

    socket_b = browser.socket("B")
    mounted = socket_b.mount(COUNTER, "/counter/", reconnect_from=socket_a)
    probe = probe_from(mounted)
    survival.observe(
        "state",
        "mount",
        "ws",
        "runs on the new process, then the saved state is put back over it"
        if probe["count"] == 2
        else "?",
    )
    _restored_counter(survival, "ws", probe, count=2, client=2)
    first = socket_b.event("increment")
    second = socket_b.event("increment")
    assert probe_from(second)["count"] == 4
    survival.observe("state", "render", "ws", _render_after_reconnect(mounted, first, second))
    socket_b.close()

    # The HTTP fallback on A sees what B saved.
    _, probe = _post(browser, "A", "/counter/", "increment")
    assert probe["count"] == 5 and probe["pid"] == db_cluster["A"].pid


def test_kill_after_save(db_cluster, survival):
    """SIGKILL A after its saves have landed; the browser reconnects to B."""
    browser = Browser(db_cluster)
    socket_a, _ = _open(browser, "A", "/counter/", COUNTER)
    socket_a.event("increment")
    socket_a.event("increment")
    try:
        db_cluster["A"].kill()
        assert socket_a.recv() is None and socket_a.closed == 1006
        socket_b = browser.socket("B")
        mounted = socket_b.mount(COUNTER, "/counter/", reconnect_from=socket_a)
        _restored_counter(survival, "restart", probe_from(mounted), count=2, client=2)
        first = socket_b.event("increment")
        second = socket_b.event("increment")
        survival.observe(
            "state", "render", "restart", _render_after_reconnect(mounted, first, second)
        )
        socket_b.close()
    finally:
        db_cluster["A"].start()


def test_kill_while_save_held(db_cluster, survival):
    """SIGKILL A while an event's save is held by a gate inside A."""
    browser = Browser(db_cluster)
    socket_a, _ = _open(browser, "A", "/counter/", COUNTER)
    assert probe_from(socket_a.event("increment"))["count"] == 1
    try:
        status, _, _ = browser.get("A", "/_mn/gate/")
        assert status == 200
        socket_a.send({"type": "event", "event": "increment", "params": {}, "ref": 99})
        db_cluster["A"].wait_line("SAVE_HELD")
        db_cluster["A"].kill()
        assert socket_a.recv() is None and socket_a.closed == 1006
        socket_b = browser.socket("B")
        probe = probe_from(socket_b.mount(COUNTER, "/counter/", reconnect_from=socket_a))
        survival.observe(
            "state",
            "held_save",
            "restart",
            "event lost; the previous saved state restored" if probe["count"] == 1 else "?",
        )
        socket_b.close()
    finally:
        db_cluster["A"].start()


def test_url_kwargs_and_handle_params(db_cluster, survival):
    browser = Browser(db_cluster)
    socket_a, probe = _open(browser, "A", "/items/7/?page=3", V + "ItemView")
    assert (probe["pk"], probe["page"]) == (7, 3)
    probe = probe_from(socket_a.event("hit"))
    survival.observe(
        "state", "url", "same", _kept((probe["pk"], probe["page"], probe["hits"]), (7, 3, 1))
    )
    socket_a.close()

    _, probe = _post(browser, "B", "/items/7/?page=3", "hit")
    survival.observe(
        "state",
        "url",
        "http",
        "read from the request; persisted fields restored"
        if (probe["pk"], probe["page"], probe["hits"]) == (7, 3, 2)
        else "?",
    )
    socket_b = browser.socket("B")
    probe = probe_from(socket_b.mount(V + "ItemView", "/items/7/?page=5", reconnect_from=socket_a))
    survival.observe(
        "state",
        "url",
        "ws",
        "read from the new URL (it wins); persisted fields restored"
        if (probe["pk"], probe["page"], probe["hits"]) == (7, 5, 2)
        else "?",
    )
    socket_b.close()


def test_held_model_and_handle_params(db_cluster, survival):
    held = V + "HeldView"
    browser = Browser(db_cluster)
    socket_a, probe = _open(browser, "A", "/held/", held)
    assert probe["member"] == "User"
    after = probe_from(socket_a.event("pick", {"value": "b"}))
    survival.observe(
        "state", "held_model", "same", "the instance" if after["member"] == "User" else "?"
    )
    survival.observe(
        "state", "params_default", "same", _kept((after["tab"], after["count"]), ("b", 1))
    )
    socket_a.close()

    socket_b = browser.socket("B")
    moved = probe_from(socket_b.mount(held, "/held/", reconnect_from=socket_a))
    survival.observe(
        "state",
        "held_model",
        "ws",
        "the instance `mount()` loaded on the new process"
        if moved["member"] == "User"
        else "? %r" % (moved,),
    )
    survival.observe(
        "state", "params_default", "ws", _kept((moved["tab"], moved["count"]), ("b", 1))
    )
    assert probe_from(socket_b.event("pick", {"value": "c"}))["member"] == "User"
    socket_b.close()

    # The URL names another tab than the one a handler set: for an undeclared
    # attribute the handler's saved value wins, on both transports.
    socket_c = browser.socket("A")
    with_url = probe_from(socket_c.mount(held, "/held/?tab=z", reconnect_from=socket_b))
    socket_c.close()
    survival.observe(
        "state",
        "url_vs_saved",
        "ws",
        "the handler's saved value wins" if with_url["tab"] == "c" else "? %r" % (with_url,),
    )
    _, posted = _post(browser, "B", "/held/?tab=z", "noop")
    survival.observe(
        "state",
        "url_vs_saved",
        "http",
        "the handler's saved value wins" if posted["tab"] == "c" else "? %r" % (posted,),
    )

    _, first = _post(browser, "A", "/held/", "pick", {"value": "d"})
    _, second = _post(browser, "B", "/held/", "pick", {"value": "e"})
    survival.observe(
        "state",
        "held_model",
        "http",
        "a dict of its fields: the view is rebuilt from saved state, `mount()` does not run"
        if (first["member"], second["member"]) == ("dict", "dict")
        else "? %r %r" % (first, second),
    )
    survival.observe(
        "state",
        "params_default",
        "http",
        _kept((first["tab"], second["tab"], second["count"]), ("d", "e", 4)),
    )


def test_form_mid_validation(db_cluster, survival):
    browser = Browser(db_cluster)
    socket_a, _ = _open(browser, "A", "/form/", V + "FormView")
    socket_a.event("validate_field", {"field": "name", "value": "ab"})
    probe = probe_from(socket_a.event("validate_field", {"field": "email", "value": "x"}))
    survival.observe(
        "state",
        "form",
        "same",
        _kept(
            (probe["form_data"], probe["field_errors"]),
            ({"name": "ab", "email": "x"}, ["email", "name"]),
        ),
    )
    socket_a.close()

    restored = "input and errors restored"
    socket_b = browser.socket("B")
    probe = probe_from(socket_b.mount(V + "FormView", "/form/", reconnect_from=socket_a))
    survival.observe(
        "state",
        "form",
        "ws",
        restored
        if (probe["form_data"], probe["field_errors"])
        == ({"name": "ab", "email": "x"}, ["email", "name"])
        else "? %r" % (probe,),
    )
    socket_b.close()

    # One more field validated over the fallback on A: the other field's
    # input and its error are there too (#4107).
    _, probe = _post(browser, "A", "/form/", "validate_field", {"field": "email", "value": "y"})
    survival.observe(
        "state",
        "form",
        "http",
        restored
        if probe["form_data"] == {"name": "ab", "email": "y"}
        and probe["field_errors"] == ["email", "name"]
        else "? %r" % (probe,),
    )


def test_wizard_step(db_cluster, survival):
    browser = Browser(db_cluster)
    socket_a, _ = _open(browser, "A", "/wizard/", V + "WizardView")
    socket_a.event("validate_field", {"field": "first", "value": "x"})
    assert probe_from(socket_a.event("next_step", {"first": "x"}))["step"] == 1
    probe = probe_from(socket_a.event("validate_field", {"field": "second", "value": "y"}))
    survival.observe("state", "wizard", "same", _kept(probe["step"], 1))
    socket_a.close()

    socket_b = browser.socket("B")
    probe = probe_from(socket_b.mount(V + "WizardView", "/wizard/", reconnect_from=socket_a))
    survival.observe(
        "state",
        "wizard",
        "ws",
        "restored: the step and the answers"
        if (probe["step"], probe["data"]) == (1, {"one": {"first": "x"}, "two": {"second": "y"}})
        else "? %r" % (probe,),
    )
    socket_b.close()

    # Over the fallback, on alternating processes: the step and the answers carry.
    _, first = _post(browser, "B", "/wizard/", "validate_field", {"field": "second", "value": "z"})
    _, second = _post(browser, "A", "/wizard/", "validate_field", {"field": "second", "value": "y"})
    survival.observe(
        "state",
        "wizard",
        "http",
        "kept: the step and the answers"
        if (first["step"], second["step"]) == (1, 1)
        and second["data"] == {"one": {"first": "x"}, "two": {"second": "y"}}
        else "? %r %r" % (first, second),
    )


def test_legacy_view(db_cluster, survival):
    browser = Browser(db_cluster)
    socket_a, _ = _open(browser, "A", "/legacy/", V + "LegacyCounterView")
    socket_a.event("increment")
    probe = probe_from(socket_a.event("increment"))
    survival.observe("state", "legacy", "same", _kept(probe["count"], 2))
    socket_a.close()
    socket_b = browser.socket("B")
    probe = probe_from(socket_b.mount(V + "LegacyCounterView", "/legacy/", reconnect_from=socket_a))
    survival.observe(
        "state", "legacy", "ws", "lost: `mount()` value" if probe["count"] == 0 else "?"
    )
    socket_b.close()
    counts = [_post(browser, node, "/legacy/", "increment")[1]["count"] for node in ("A", "B")]
    survival.observe(
        "state", "legacy", "http", "lost: every request runs `mount()`" if counts == [1, 1] else "?"
    )


def test_components(db_cluster, survival):
    browser = Browser(db_cluster)
    socket_a, probe = _open(browser, "A", "/component/", V + "ComponentView")
    socket_a.event("bump", {"component_id": probe["bump_id"]})
    socket_a.event("toggle", {"component_id": probe["menu_id"]})
    after = probe_from(socket_a.event("bump", {"component_id": probe["bump_id"]}))
    survival.observe("state", "component", "same", _kept(after["n"], 2))
    survival.observe("state", "dropdown", "same", _kept(after["menu_open"], True))
    socket_a.close()

    socket_b = browser.socket("B")
    moved = probe_from(socket_b.mount(V + "ComponentView", "/component/", reconnect_from=socket_a))
    survival.observe(
        "state", "component", "ws", "restored" if moved["n"] == 2 else "? %r" % (moved,)
    )
    survival.observe(
        "state",
        "dropdown",
        "ws",
        "lost: closed, with a new component id"
        if moved["menu_open"] is False and moved["menu_id"] != probe["menu_id"]
        else "?",
    )
    socket_b.close()

    for row, params in (
        ("component", {"component_id": probe["bump_id"]}),
        ("dropdown", {"component_id": probe["menu_id"]}),
    ):
        status, payload = browser.post_event(
            "A", "/component/", "bump" if row == "component" else "toggle", params
        )
        if status == 400 and payload.get("error") == "Component not found":
            outcome = "event refused (Component not found)"
        elif row == "component" and status == 200 and probe_of(payload["html"])["n"] == 3:
            outcome = "kept: the event reaches the component and its state carries"
        else:
            outcome = "? %s %r" % (status, payload)
        survival.observe("state", row, "http", outcome)


def test_async_work_in_flight(db_cluster, survival):
    """The socket moves while ``start_async`` work runs on A (held by a gate)."""
    browser = Browser(db_cluster)
    socket_a, _ = _open(browser, "A", "/async/", V + "AsyncView")
    socket_a.event("start")
    db_cluster["A"].wait_line("WORK_STARTED")
    socket_a.close()
    socket_b = browser.socket("B")
    moved = probe_from(socket_b.mount(V + "AsyncView", "/async/", reconnect_from=socket_a))
    # The work finishes on A, for a view no socket holds any more.
    assert browser.get("A", "/_mn/release/")[0] == 200
    db_cluster["A"].wait_line("WORK_FINISHED")
    again = probe_from(browser.socket("B").mount(V + "AsyncView", "/async/"))
    survival.observe(
        "state",
        "async",
        "ws",
        "result lost; a persisted in-progress flag stays set"
        if (moved["result"], moved["running"]) == ("", True)
        and (again["result"], again["running"]) == ("", True)
        else "? %r %r" % (moved, again),
    )
    socket_b.close()

    # The same work on one process (the gate is open now) delivers its result.
    other = Browser(db_cluster)
    socket, _ = _open(other, "A", "/async/", V + "AsyncView")
    socket.event("start")
    done = socket.until(lambda f: (probe_from([f]) or {}).get("result") == "done")
    survival.observe(
        "state",
        "async",
        "same",
        "result delivered" if probe_from(done)["running"] is False else "?",
    )
    socket.close()


def test_tick(db_cluster, survival):
    browser = Browser(db_cluster)
    socket_a, _ = _open(browser, "A", "/tick/", V + "TickView")
    frames = socket_a.until(lambda f: (probe_from([f]) or {}).get("ticks", 0) >= 2)
    survival.observe(
        "state", "tick", "same", "keeps ticking" if probe_from(frames)["ticks"] == 2 else "?"
    )
    socket_a.close()

    socket_b = browser.socket("B")
    moved = probe_from(socket_b.mount(V + "TickView", "/tick/", reconnect_from=socket_a))
    ticked = probe_from(socket_b.until(lambda f: probe_from([f]) is not None))
    survival.observe(
        "state",
        "tick",
        "ws",
        "restarts on the new process; the counters continue"
        if moved["ticks"] >= 2
        and moved["ticks_saved"] == moved["ticks"]
        and ticked["pid"] == db_cluster["B"].pid
        and ticked["ticks"] == moved["ticks"] + 1
        and ticked["ticks_saved"] == moved["ticks_saved"] + 1
        else "? %r %r" % (moved, ticked),
    )
    socket_b.close()


def test_stream_rows(db_cluster, survival):
    browser = Browser(db_cluster)
    socket_a, _ = _open(browser, "A", "/stream/", V + "StreamView")
    inserted = []
    for _ in range(2):
        for frame in socket_a.event("add"):
            for patch in frame.get("patches") or ():
                if patch.get("type") == "InsertChild":
                    inserted.append(patch["node"]["attrs"]["id"])
    survival.observe("state", "stream", "same", _kept(inserted, ["rows-2", "rows-3"]))
    socket_a.close()
    socket_b = browser.socket("B")
    html = socket_b.mount(V + "StreamView", "/stream/", reconnect_from=socket_a)[-1]["html"]
    survival.observe(
        "state",
        "stream",
        "ws",
        "lost: the mount renders only `mount()`'s rows"
        if 'id="rows-1"' in html and 'id="rows-2"' not in html
        else "?",
    )
    socket_b.close()


# ---------------------------------------------------------------------------
# Sessions, identity, sockets, messaging
# ---------------------------------------------------------------------------


def test_login_then_other_process(db_cluster, survival):
    browser = Browser(db_cluster)
    status, _, _ = browser.get("B", "/who/")
    assert status == 302, "anonymous is sent to the login page"
    assert browser.get("A", "/_mn/login/?user=alice")[0] == 200
    status, body, _ = browser.get("A", "/who/")
    survival.observe(
        "sessions", "login", "same", "authenticated" if probe_of(body)["who"] == "alice" else "?"
    )
    status, body, _ = browser.get("B", "/who/")
    over_http = status == 200 and probe_of(body)["who"] == "alice"
    socket = browser.socket("B")
    probe = probe_from(socket.mount(V + "WhoView", "/who/"))
    survival.observe(
        "sessions",
        "login",
        "ws",
        "authenticated (HTTP and WebSocket)"
        if over_http and probe["who"] == "alice" and probe["pid"] == db_cluster["B"].pid
        else "?",
    )
    socket.close()


def test_logout_while_socket_open_elsewhere(db_cluster, survival):
    browser = Browser(db_cluster)
    browser.get("A", "/_mn/login/?user=bob")
    socket, _ = _open(browser, "B", "/who/", V + "WhoView")
    assert probe_from(socket.event("look"))["seen"] == "bob"
    assert browser.get("A", "/_mn/logout/")[0] == 200
    answer = socket.event("look")
    error = answer[-1] if answer else {}
    closed = socket.recv() is None and socket.closed
    survival.observe(
        "sessions",
        "logout",
        "ws",
        "next event refused, socket closed 4403"
        if error.get("code") == "permission_denied" and closed == 4403
        else "? %r %r" % (answer, closed),
    )


def test_tenant_scoping_across_processes(db_cluster, survival):
    browser = Browser(db_cluster, headers={"X-Tenant-ID": "t1"})
    socket, _ = _open(browser, "A", "/counter/", COUNTER)
    socket.event("increment")
    socket.close()
    browser.headers["X-Tenant-ID"] = "t2"
    other = probe_from(browser.socket("B").mount(COUNTER, "/counter/"))
    browser.headers["X-Tenant-ID"] = "t1"
    own = probe_from(browser.socket("B").mount(COUNTER, "/counter/"))
    survival.observe(
        "sessions",
        "tenant",
        "ws",
        "other tenant mounts fresh; own tenant restored"
        if other["count"] == 0 and own["count"] == 1
        else "? %r %r" % (other, own),
    )


@pytest.mark.parametrize("second_node", ["A", "B"], ids=["same_process", "other_process"])
def test_two_sockets_same_route(db_cluster, survival, second_node):
    column = "same" if second_node == "A" else "ws"
    survival.scope(columns=[column])
    browser = Browser(db_cluster)
    # Two page loads of one route in one session: two tabs, two tab ids.
    browser.get("A", "/counter/")
    tab_one = browser.tab("/counter/")
    first = browser.socket("A")
    first.mount(COUNTER, "/counter/", tab=tab_one)
    browser.get(second_node, "/counter/")
    tab_two = browser.tab("/counter/")
    assert tab_one and tab_two and tab_one != tab_two
    second = browser.socket(second_node)
    second.mount(COUNTER, "/counter/", tab=tab_two)
    first.event("increment")
    assert probe_from(first.event("increment"))["plain_attr"] == 2
    assert probe_from(second.event("increment"))["plain_attr"] == 1
    again_one = probe_from(browser.socket("A").mount(COUNTER, "/counter/", tab=tab_one))
    again_two = probe_from(browser.socket("A").mount(COUNTER, "/counter/", tab=tab_two))
    survival.observe(
        "sessions",
        "two_sockets_same_route",
        column,
        'independent; a `persist="server"` field is still shared (last write wins)'
        if (again_one["plain_attr"], again_two["plain_attr"]) == (2, 1)
        and again_one["count"] == again_two["count"] == 1
        else "? %r %r" % (again_one, again_two),
    )
    for socket in (first, second):
        socket.close()


def test_two_sockets_other_route(db_cluster, survival):
    browser = Browser(db_cluster)
    browser.get("A", "/counter/")
    browser.get("A", "/counter2/")
    one = browser.socket("A")
    one.mount(COUNTER, "/counter/")
    two = browser.socket("B")
    two.mount(COUNTER, "/counter2/")
    one.event("increment")
    one.event("increment")
    two.event("increment")
    first = probe_from(browser.socket("B").mount(COUNTER, "/counter/"))
    second = probe_from(browser.socket("A").mount(COUNTER, "/counter2/"))
    survival.observe(
        "sessions",
        "two_sockets_other_route",
        "ws",
        "independent" if (first["count"], second["count"]) == (2, 1) else "?",
    )


def test_push_to_view_across_processes(redis_cluster, survival):
    browser = Browser(redis_cluster)
    socket, _ = _open(browser, "B", "/push/", V + "PushView")
    assert browser.get("A", "/_mn/push/?value=5")[0] == 200
    frames = socket.until(lambda f: f.get("source") == "broadcast")
    survival.observe(
        "sessions",
        "push_redis",
        "ws",
        "delivered" if probe_from(frames)["received"] == [5] else "?",
    )
    socket.close()


def test_push_with_in_memory_layer(db_cluster, survival):
    browser = Browser(db_cluster)
    on_a, _ = _open(browser, "A", "/push/", V + "PushView")
    on_b, _ = _open(Browser(db_cluster), "B", "/push/", V + "PushView")
    assert browser.get("A", "/_mn/push/?value=1")[0] == 200
    got_a = probe_from(on_a.until(lambda f: f.get("source") == "broadcast"))
    survival.observe(
        "sessions", "push_memory", "same", "delivered" if got_a["received"] == [1] else "?"
    )
    # B's own push reaches B; A's push from before it never does.
    assert browser.get("B", "/_mn/push/?value=2")[0] == 200
    got_b = probe_from(on_b.until(lambda f: f.get("source") == "broadcast"))
    after = probe_from(on_b.event("probe"))
    survival.observe(
        "sessions",
        "push_memory",
        "ws",
        "not delivered" if got_b["received"] == [2] and after["received"] == [2] else "?",
    )
    on_a.close()
    on_b.close()


def _online(socket, count: int):
    return socket.until(lambda f: (probe_from([f]) or {}).get("online") == count)


def test_presence_across_processes(redis_cluster, survival):
    x, _ = _open(Browser(redis_cluster), "A", "/room/", V + "RoomView")
    y, seen_by_y = _open(Browser(redis_cluster), "B", "/room/", V + "RoomView")
    joined = _online(x, 2)
    y.close()
    left = _online(x, 1)
    survival.observe(
        "sessions",
        "presence_redis",
        "ws",
        "join and leave seen by the other process"
        if seen_by_y["online"] == 2 and probe_from(joined) and probe_from(left)
        else "?",
    )
    x.close()


def test_presence_with_memory_backend(db_cluster, survival):
    x, on_a = _open(Browser(db_cluster), "A", "/room/", V + "RoomView")
    y, on_b = _open(Browser(db_cluster), "B", "/room/", V + "RoomView")
    survival.observe(
        "sessions",
        "presence_memory",
        "ws",
        "each process counts only its own" if (on_a["online"], on_b["online"]) == (1, 1) else "?",
    )
    x.close()
    y.close()


def test_clock_skew_between_processes(survival):
    """B's clock is ten minutes behind A's (an envelope A wrote is "from the future")."""
    with Cluster(
        "db", "memory", None, node_env={"B": {"DJUST_MN_CLOCK_OFFSET": "-600"}}
    ) as cluster:
        browser = Browser(cluster)
        socket, _ = _open(browser, "A", "/counter/", COUNTER)
        socket.event("increment")
        socket.close()
        moved = probe_from(browser.socket("B").mount(COUNTER, "/counter/", reconnect_from=socket))
        _, over_http = _post(browser, "B", "/counter/", "increment")
        survival.observe(
            "sessions",
            "clock_skew",
            "ws",
            "discarded: the view mounts fresh"
            if moved["count"] == 0 and over_http["count"] == 1
            else "? %r %r" % (moved, over_http),
        )


# ---------------------------------------------------------------------------
# Session engines
# ---------------------------------------------------------------------------

ENGINES = ["db", "file", "cache_file", "cache_redis", "cached_db", "cache_locmem"]


@pytest.mark.parametrize("engine", ENGINES)
def test_session_engine_across_processes(engine, survival, request):
    survival.scope(rows=[engine])
    redis = request.getfixturevalue("redis_url") if engine in ("cache_redis", "cached_db") else None
    with Cluster(engine, "memory", redis) as cluster:
        browser = Browser(cluster)
        assert browser.get("A", "/counter/")[0] == 200
        answers = [browser.post_event("B", "/counter/", "increment")]
        cookie_kept = "sessionid" in browser.cookies
        answers.append(browser.post_event("A", "/counter/", "increment"))
        statuses = [status for status, _ in answers]
        counts = [
            probe_of(payload["html"])["count"] if status == 200 else None
            for status, payload in answers
        ]
        if counts == [1, 2]:
            over_http = "kept"
        elif statuses == [500, 200] and not cookie_kept and counts[1] == 1:
            over_http = (
                "500 error, the session cookie is deleted; the next request starts a new session"
            )
        elif statuses == [200, 200] and counts == [1, 1]:
            over_http = "not kept: each process holds its own sessions, so each request starts over"
        else:
            over_http = "? %r %r %r" % (statuses, counts, cookie_kept)
        survival.observe("engines", engine, "http", over_http)

        browser = Browser(cluster)
        socket, _ = _open(browser, "A", "/counter/", COUNTER)
        at_a = probe_from(socket.event("increment"))["count"]
        socket.close()
        moved = browser.socket("B").mount(COUNTER, "/counter/", reconnect_from=socket)
        if kinds(moved) == ["mount"] and probe_from(moved)["count"] == at_a:
            over_ws = "restored"
        elif kinds(moved) == ["error"]:
            over_ws = "mount fails with an error"
        else:
            over_ws = "? %r" % (moved,)
        survival.observe("engines", engine, "ws", over_ws)


def test_session_cost_per_event(db_cluster, survival):
    """Session loads and saves, and SQL on ``django_session``, for ONE event."""
    browser = Browser(db_cluster)
    socket, _ = _open(browser, "A", "/counter/", COUNTER)
    socket.event("increment")  # the first event after a mount sends full HTML
    db_cluster.stats("A", reset=True)
    socket.event("increment")
    ws = db_cluster.stats("A", reset=True)
    socket.close()
    _post(browser, "A", "/counter/", "increment")
    db_cluster.stats("A", reset=True)
    _post(browser, "A", "/counter/", "increment")
    http = db_cluster.stats("A", reset=True)
    for row, counts in (("ws_event", ws), ("http_event", http)):
        observed = {
            "loads": counts.get("session_load", 0) + counts.get("session_aload", 0),
            "saves": counts.get("session_save", 0) + counts.get("session_asave", 0),
            "selects": counts.get("sql_select", 0),
            "updates": counts.get("sql_update", 0) + counts.get("sql_insert", 0),
        }
        for column, value in observed.items():
            survival.observe("cost", row, column, str(value))
