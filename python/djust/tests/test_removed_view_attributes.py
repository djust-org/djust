"""2.0 removed the actor system; a view that still asks for it is refused.

``use_actors = True`` used to move a view's state and rendering into a Tokio
actor. Ignoring the attribute would leave such a view running on a different
path than the one it asked for, without a word, so ``LiveView`` refuses the
class when it is created and names the replacement.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured

from djust import LiveView
from djust._removed_settings import REMOVED_VIEW_ATTRIBUTES


def test_a_view_that_sets_use_actors_is_refused_at_class_creation():
    with pytest.raises(ImproperlyConfigured) as refused:

        class ActorView(LiveView):
            template = "<div dj-root></div>"
            use_actors = True

    message = str(refused.value)
    assert "ActorView" in message
    assert "use_actors" in message
    assert "removed in djust 2.0" in message
    # The replacement, not just the refusal.
    assert "delete the attribute" in message
    assert "start_async()" in message


def test_a_mixin_that_sets_use_actors_is_refused_where_it_meets_liveview():
    class ActorsMixin:
        use_actors = True

    with pytest.raises(ImproperlyConfigured, match="use_actors"):

        class MixedView(ActorsMixin, LiveView):
            template = "<div dj-root></div>"


def test_a_subclass_of_a_plain_view_can_still_turn_it_on_and_is_refused():
    class Base(LiveView):
        template = "<div dj-root></div>"

    with pytest.raises(ImproperlyConfigured, match="Child"):

        class Child(Base):
            use_actors = True


def test_use_actors_false_and_an_ordinary_view_are_accepted():
    class Off(LiveView):
        template = "<div dj-root></div>"
        use_actors = False

    class Plain(LiveView):
        template = "<div dj-root></div>"

    assert Off() is not None and Plain() is not None


def test_the_attribute_and_the_extension_surface_are_gone():
    from djust import _rust

    assert not hasattr(LiveView, "use_actors")
    for name in ("create_session_actor", "get_actor_stats", "SessionActorHandle"):
        assert not hasattr(_rust, name), name
    assert "use_actors" in REMOVED_VIEW_ATTRIBUTES
