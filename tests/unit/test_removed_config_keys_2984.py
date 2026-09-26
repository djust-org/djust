"""#2984 (1.3 part): the six LIVEVIEW_CONFIG keys nothing read are removed.

1.2.1 (#3047) added ``djust.C018`` to warn when one is set. 1.3 drops their
defaults, so ``get_config()`` no longer advertises settings that do nothing.
A project that still sets one must keep loading and get the C018 warning.
"""

from django.test import override_settings

from djust.checks.configuration import DEAD_LIVEVIEW_CONFIG_KEYS, _check_dead_config_keys
from djust.config import LiveViewConfig

REMOVED = (
    "jit_cache_backend",
    "jit_cache_dir",
    "jit_redis_url",
    "debug_components",
    "component_wrapper_class",
    "component_loading_class",
)


def test_the_check_covers_exactly_the_removed_keys():
    assert set(DEAD_LIVEVIEW_CONFIG_KEYS) == set(REMOVED)


def test_removed_keys_have_no_default():
    config = LiveViewConfig()
    present = [key for key in REMOVED if key in config.as_dict()]
    assert present == [], "removed keys still have defaults: %r" % present


def test_setting_a_removed_key_loads_and_warns():
    settings = {key: "x" for key in REMOVED}
    with override_settings(LIVEVIEW_CONFIG=settings, DJUST_CONFIG={}):
        config = LiveViewConfig()  # must not raise
        assert config.get("jit_debug") is False
        errors: list = []
        _check_dead_config_keys(errors)
    c018 = [e for e in errors if e.id == "djust.C018"]
    assert len(c018) == 1
    for key in REMOVED:
        assert "LIVEVIEW_CONFIG['%s']" % key in c018[0].msg
    assert "removed in djust 1.3" in c018[0].msg
