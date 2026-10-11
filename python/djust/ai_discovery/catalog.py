"""Settings-free catalog and deterministic intent ranking of bundled UI components."""

from dataclasses import asdict, dataclass
from difflib import get_close_matches
from functools import lru_cache
import inspect
import logging
import re
from typing import Any, Literal

logger = logging.getLogger(__name__)

CATALOG_VERSION = 1
MAX_QUERY_LENGTH = 512
_STOPWORDS = frozenset(
    "a an the with and or for of to in on my i want need some that which into from by using use".split()
)


@dataclass(frozen=True)
class Prop:
    name: str
    required: bool
    default: str | None


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    kind: Literal["tag", "class"]
    label: str
    category: str
    category_label: str
    purpose: str
    keywords: tuple[str, ...]
    load: str
    snippet: str
    variants: tuple[tuple[str, str], ...]
    props: tuple[Prop, ...] | None
    props_source: Literal["signature", "snippet"] | None
    children: tuple[str, ...]
    related: tuple[str, ...]

    def required_props(self) -> list[str] | None:
        if self.props is None or self.props_source != "signature":
            return None
        return [p.name for p in self.props if p.required]

    def to_dict(self, *, full: bool = False) -> dict[str, Any]:
        result = asdict(self)
        result.pop("variants")
        result["component_kind"] = result.pop("kind")
        result["required_props"] = self.required_props()
        if full:
            result["variants"] = [
                {"name": name, "template": template} for name, template in self.variants
            ]
        return result


def _signature_props(func: Any) -> tuple[Prop, ...]:
    params = list(inspect.signature(func).parameters.values())
    if params and params[0].name == "context":
        params = params[1:]
    return tuple(
        Prop(
            p.name,
            p.default is inspect.Parameter.empty,
            None if p.default is inspect.Parameter.empty else repr(p.default),
        )
        for p in params
        if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    )


@lru_cache(maxsize=1)
def load_catalog() -> tuple[CatalogEntry, ...]:
    from djust.components.gallery.examples import (
        EXAMPLES,
        CLASS_EXAMPLES,
        CATEGORIES,
        CATEGORY_ORDER,
        CHILD_TAGS,
    )

    try:
        from djust.components.gallery.registry import discover_template_tags

        tags = discover_template_tags()
    except ImportError:  # Optional rendering extras must not hide the catalog.
        logger.debug("UI catalog tag discovery unavailable")
        tags = None
    entries = []
    names = set(EXAMPLES) | set(CLASS_EXAMPLES)
    for kind, examples in (("tag", EXAMPLES), ("class", CLASS_EXAMPLES)):
        for name, info in examples.items():
            variants = (
                tuple((v["name"], v["template"]) for v in info["variants"]) if kind == "tag" else ()
            )
            snippet = variants[0][1] if variants else info["snippet"]
            props: tuple[Prop, ...] | None = None
            source: Literal["signature", "snippet"] | None = None
            if kind == "tag" and tags is not None:
                func = getattr(tags.get(name), "__wrapped__", None)
                if func is not None:
                    props, source = _signature_props(func), "signature"
                else:
                    match = re.search(r"{%\s*" + re.escape(name) + r"\b(.*?)%}", snippet, re.DOTALL)
                    keys = dict.fromkeys(re.findall(r"\b(\w+)\s*=", match[1] if match else ""))
                    props, source = tuple(Prop(key, False, None) for key in keys), "snippet"
            children = tuple(child for child, parent in CHILD_TAGS.items() if parent == name)
            used = [
                CHILD_TAGS.get(tag, tag)
                for _, tmpl in variants
                for tag in re.findall(r"{%\s*(\w+)", tmpl)
            ]
            related = tuple(
                dict.fromkeys(
                    r
                    for r in [*info.get("related", ()), *used, *children]
                    if r in names and r != name
                )
            )[:6]
            category = info["category"]
            entries.append(
                CatalogEntry(
                    name=name,
                    kind="tag" if kind == "tag" else "class",
                    label=info["label"],
                    category=category,
                    category_label=CATEGORIES[category],
                    purpose=info["purpose"],
                    keywords=tuple(info.get("keywords", ())),
                    load="{% load djust_components %}"
                    if kind == "tag"
                    else f"from djust.components.components import {name}",
                    snippet=snippet,
                    variants=variants,
                    props=props,
                    props_source=source,
                    children=children,
                    related=related,
                )
            )
    return tuple(sorted(entries, key=lambda e: (CATEGORY_ORDER.index(e.category), e.name)))


def get_entry(name: str) -> CatalogEntry | None:
    from djust.components.gallery.examples import CHILD_TAGS

    parent = CHILD_TAGS.get(name, name)
    return next((e for e in load_catalog() if e.name == parent), None)


def categories() -> list[tuple[str, str, list[CatalogEntry]]]:
    from djust.components.gallery.examples import CATEGORY_ORDER, CATEGORIES

    return [
        (slug, CATEGORIES[slug], [e for e in load_catalog() if e.category == slug])
        for slug in CATEGORY_ORDER
    ]


def _tokens(value: str) -> list[str]:
    result = []
    for token in re.split("[^a-z0-9]+", value.lower()):
        if not token or token in _STOPWORDS:
            continue
        if len(token) > 4 and token.endswith(("ses", "xes", "ches", "shes")):
            token = token[:-2]
        elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        result.append(token)
    return result


def search(intent: str, limit: int = 5) -> list[tuple[CatalogEntry, int]]:
    from djust.components.gallery.examples import CATEGORY_ORDER

    query = _tokens(intent[:MAX_QUERY_LENGTH])
    query_tokens = set(query)
    if not query:
        raise ValueError("intent is empty")
    results = []
    for entry in load_catalog():
        fields = [
            (entry.name, 6),
            (entry.label, 4),
            (" ".join(entry.keywords), 4),
            (entry.purpose, 2),
            (entry.category + " " + entry.category_label, 1),
        ]
        score = sum(weight * len(query_tokens & set(_tokens(field))) for field, weight in fields)
        name_tokens = _tokens(entry.name)
        if any(query[i : i + len(name_tokens)] == name_tokens for i in range(len(query))):
            score += 5
        if score:
            results.append((entry, score))
    results.sort(key=lambda item: (-item[1], CATEGORY_ORDER.index(item[0].category), item[0].name))
    return results[: max(1, min(20, limit))]


def close_matches(name: str, n: int = 5) -> list[str]:
    from djust.components.gallery.examples import CHILD_TAGS

    return get_close_matches(name, [e.name for e in load_catalog()] + list(CHILD_TAGS), n=n)
