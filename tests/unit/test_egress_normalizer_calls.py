"""One HTTP GET runs the egress normalizer once on its response.

``get_template()`` runs several times per GET (the GET body, the template-hash
slot, the Rust view's initialization), and each run normalized the same
template source again: four normalizer passes over ~100 KB for one homepage
load. The normalized source is now cached per class, keyed by the source, so a
GET normalizes its response once, plus its template source the first time that
source is seen.
"""

from __future__ import annotations

import pytest

from djust.mixins.template import TemplateMixin


@pytest.fixture
def counted(monkeypatch):
    calls: list[str] = []
    real = TemplateMixin._strip_comments_and_whitespace

    def counting(self, html):
        calls.append(html)
        return real(self, html)

    monkeypatch.setattr(TemplateMixin, "_strip_comments_and_whitespace", counting)
    return calls


@pytest.fixture
def homepage_view():
    from demo_app.views import IndexView

    IndexView.__dict__.get("_djust_normalized_sources", {}).clear()
    return IndexView


def test_a_get_normalizes_its_source_once_and_its_response_once(
    client, settings, counted, homepage_view
):
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
    first = client.get("/")
    assert first.status_code == 200
    # The template source (first) and the rendered response (second).
    assert len(counted) == 2, [c[:60] for c in counted]
    assert counted[0] != counted[1]

    counted.clear()
    second = client.get("/")
    assert second.status_code == 200
    assert len(counted) == 1
    assert len(second.content) == len(first.content)  # per-request tokens differ


def test_an_edited_source_is_normalized_again(counted):
    """Keyed by the source itself, so a hot-reloaded template is not served
    from a stale entry."""

    class _View(TemplateMixin):
        pass

    view = _View()
    assert view._normalize_source_for_render("<div>\n <p>a</p>\n</div>") == "<div><p>a</p></div>"
    assert view._normalize_source_for_render("<div>\n <p>a</p>\n</div>") == "<div><p>a</p></div>"
    assert len(counted) == 1
    assert view._normalize_source_for_render("<div>\n <p>b</p>\n</div>") == "<div><p>b</p></div>"
    assert len(counted) == 2


def test_an_overridden_normalizer_is_not_bypassed():
    class _Base(TemplateMixin):
        pass

    class _Loud(_Base):
        def _strip_comments_and_whitespace(self, html):
            return "LOUD"

    source = "<div>\n <p>c</p>\n</div>"
    assert _Base()._normalize_source_for_render(source) == "<div><p>c</p></div>"
    assert _Loud()._normalize_source_for_render(source) == "LOUD"
