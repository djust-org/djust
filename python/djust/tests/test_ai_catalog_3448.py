"""Contract tests for the settings-free UI capability catalog."""

import json
import os
import re
import subprocess
import sys

import pytest

from djust.components.gallery.examples import EXAMPLES, CLASS_EXAMPLES, CATEGORY_ORDER


def test_every_catalog_entry_has_a_one_line_purpose():
    for info in [*EXAMPLES.values(), *CLASS_EXAMPLES.values()]:
        purpose = info.get("purpose")
        assert isinstance(purpose, str) and purpose
        assert "\n" not in purpose and len(purpose) <= 90
        assert purpose != info["label"]


def test_keywords_and_related_are_well_formed():
    names = set(EXAMPLES) | set(CLASS_EXAMPLES)
    for name, info in {**EXAMPLES, **CLASS_EXAMPLES}.items():
        for field in ("keywords", "related"):
            assert isinstance(info[field], tuple)
            assert all(isinstance(s, str) and s and s == s.lower() for s in info[field])
        assert set(info["related"]) <= names - {name}


def test_class_examples_have_a_snippet():
    assert all(isinstance(i.get("snippet"), str) and i["snippet"] for i in CLASS_EXAMPLES.values())


def test_every_registered_tag_is_cataloged_or_a_child():
    from djust.components.gallery.examples import CHILD_TAGS
    from djust.components.gallery.registry import discover_template_tags

    assert set(discover_template_tags()) - set(EXAMPLES) == set(CHILD_TAGS)
    assert set(CHILD_TAGS.values()) <= set(EXAMPLES)
    assert not set(CHILD_TAGS) & set(EXAMPLES)


def test_child_tag_resolves_to_parent():
    from djust.ai_discovery.catalog import get_entry

    assert get_entry("tab").name == "tabs"


def test_load_catalog_order_follows_category_order():
    from djust.ai_discovery.catalog import load_catalog

    entries = load_catalog()
    assert len(entries) == len(EXAMPLES) + len(CLASS_EXAMPLES)
    assert list(entries) == sorted(
        entries, key=lambda e: (CATEGORY_ORDER.index(e.category), e.name)
    )


def test_signature_props_mark_required():
    from djust.ai_discovery.catalog import get_entry

    assert set(get_entry("data_table").required_props()) >= {"rows", "columns"}
    assert get_entry("data_table").props_source == "signature"
    assert get_entry("badge").required_props() == []


def test_block_tag_props_come_from_snippet():
    from djust.ai_discovery.catalog import get_entry

    entry = get_entry("modal")
    assert entry.props_source == "snippet"
    assert "title" in [p.name for p in entry.props]
    assert entry.required_props() is None


def test_catalog_dict_is_json_serializable():
    from djust.ai_discovery.catalog import load_catalog

    payload = json.dumps([e.to_dict(full=True) for e in load_catalog()])
    assert '"context"' not in payload


def test_catalog_loads_without_django_settings(tmp_path):
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from djust.ai_discovery.catalog import load_catalog; print(len(load_catalog()))",
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout) == len(EXAMPLES) + len(CLASS_EXAMPLES)


@pytest.mark.parametrize(
    "intent,top,n,also",
    [
        (
            "table with filters, bulk select, infinite scroll",
            {"data_table"},
            1,
            {"infinite_scroll", "filter_bar"},
        ),
        ("searchable select dropdown", {"combobox"}, 3, set()),
        ("side panel drawer", {"sheet"}, 1, set()),
        ("toast notification after save", {"server_toast_container", "toast_container"}, 2, set()),
        ("confirm dialog", {"modal", "confirm_dialog"}, 2, set()),
        ("app layout with sidebar", {"app_shell"}, 3, set()),
    ],
)
def test_search_canonical_intents(intent, top, n, also):
    from djust.ai_discovery.catalog import search

    names = [e.name for e, _ in search(intent)]
    assert top & set(names[:n])
    assert also <= set(names)


def test_search_is_deterministic():
    from djust.ai_discovery.catalog import search

    assert search("table filters") == search("table filters")


@pytest.mark.parametrize("intent", ["", "with and the"])
def test_search_empty_intent_raises(intent):
    from djust.ai_discovery.catalog import search

    with pytest.raises(ValueError, match="intent is empty"):
        search(intent)


def test_search_limit_is_clamped():
    from djust.ai_discovery.catalog import search

    assert len(search("form", 0)) == 1
    assert len(search("form", 999)) <= 20


def test_x1xx_suggested_components_are_in_the_catalog():
    from djust import audit_ui
    from djust.ai_discovery.catalog import get_entry

    for code in range(101, 105):
        for name in re.findall(r"{%\s*(\w+)", getattr(audit_ui, f"_X{code}_DETAILS")):
            if not name.startswith("end"):
                assert get_entry(name) is not None


def test_catalog_survives_missing_optional_rendering_extras(tmp_path):
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in ('markdown', 'nh3'):
        raise ImportError('optional extra unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from djust.ai_discovery.catalog import load_catalog
entries = load_catalog()
assert entries
assert all(e.props is None and e.props_source is None for e in entries)
print(len(entries))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout) == len(EXAMPLES) + len(CLASS_EXAMPLES)


def test_class_snippets_use_public_constructor_signatures():
    import ast
    import inspect
    from djust.components.gallery.registry import discover_component_classes

    classes = discover_component_classes()
    for name, info in CLASS_EXAMPLES.items():
        call = ast.parse(info["snippet"], mode="eval").body
        assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        assert call.func.id == name
        params = inspect.signature(classes[name]).parameters
        assert all(keyword.arg in params for keyword in call.keywords)
        args = [ast.literal_eval(arg) for arg in call.args]
        kwargs = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        inspect.signature(classes[name]).bind(*args, **kwargs)
