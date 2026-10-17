"""#2984 (1.3 part): the six LIVEVIEW_CONFIG keys nothing read are removed.

1.2.1 (#3047) added ``djust.C018`` to warn when one is set, and 1.3 dropped
their defaults. 2.0 removed C018 itself; a project that still sets one of the
keys keeps loading.
"""

from django.test import override_settings

from djust.config import LiveViewConfig

REMOVED = (
    "jit_cache_backend",
    "jit_cache_dir",
    "jit_redis_url",
    "debug_components",
    "component_wrapper_class",
    "component_loading_class",
)


def test_removed_keys_have_no_default():
    config = LiveViewConfig()
    present = [key for key in REMOVED if key in config.as_dict()]
    assert present == [], "removed keys still have defaults: %r" % present


def test_setting_a_removed_key_still_loads():
    settings = {key: "x" for key in REMOVED}
    with override_settings(LIVEVIEW_CONFIG=settings, DJUST_CONFIG={}):
        config = LiveViewConfig()  # must not raise
        assert config.get("jit_debug") is False
