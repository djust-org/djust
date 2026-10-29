import pytest
from djust._rust import RustLiveView


def fresh(tmp_path):
    (tmp_path / "root.html").write_text("<main dj-root>BASE</main>")
    view = RustLiveView('{% include "root.html" %}', [str(tmp_path)])
    view.set_authenticated_partial_root(True)
    return view


def test_reset_invalidates_an_initial_owned_preparation(tmp_path):
    view = fresh(tmp_path)
    _, _, version, _ = view.prepare_owned_diff([])
    view.reset()
    with pytest.raises(RuntimeError, match="pending"):
        view.commit_owned_diff(version)


def test_owned_preparation_cannot_be_modified_as_http(tmp_path):
    view = fresh(tmp_path)
    view.prepare_owned_diff([])
    html = "<main dj-root>BASE</main>"
    with pytest.raises(RuntimeError, match="owned diff"):
        view.validate_prepared_page(html, [(0, len(html))])


def test_owned_preparation_advances_only_on_accepted_version(tmp_path):
    import msgpack

    view = fresh(tmp_path)
    before = msgpack.unpackb(view.serialize_msgpack(), raw=False)[2:4]
    html, _, version, _ = view.prepare_owned_diff([])
    assert "BASE" in html
    assert msgpack.unpackb(view.serialize_msgpack(), raw=False)[2:4] == before
    view.commit_owned_diff(version)
    after = msgpack.unpackb(view.serialize_msgpack(), raw=False)[2:4]
    assert after[1] == version
    assert "BASE" in str(after[0])


def test_wrong_owned_version_preserves_the_delivered_baseline(tmp_path):
    import msgpack

    view = fresh(tmp_path)
    view.render_with_diff()
    before = msgpack.unpackb(view.serialize_msgpack(), raw=False)[2:4]
    _, _, version, _ = view.prepare_owned_diff([])
    with pytest.raises(RuntimeError, match="version changed"):
        view.commit_owned_diff(version + 1)
    assert msgpack.unpackb(view.serialize_msgpack(), raw=False)[2:4] == before


def test_http_commits_new_document_not_an_ignored_old_subtree(tmp_path):
    (tmp_path / "root.html").write_text(
        '<main dj-root><section dj-update="ignore">{% if show %}<b>OLD</b>{% endif %}</section></main>'
    )
    view = RustLiveView('{% include "root.html" %}', [str(tmp_path)])
    view.set_authenticated_partial_root(True)
    view.update_state({"show": True})
    view.render_with_diff()
    view.update_state({"show": False})
    html, _, _, _ = view.prepare_authenticated_page()
    assert "OLD" not in html
    view.validate_prepared_page(html, view.prepared_page_root_spans())
    baseline, _, _ = view.commit_authenticated_page()
    assert "OLD" not in baseline
    assert 'dj-id="0"' in baseline.split(">", 1)[0]
