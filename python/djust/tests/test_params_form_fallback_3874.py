"""``params_form_fallback = "fields"``: keep the fields that validated (#3874).

When ``params_form`` rejects the URL the default ``handle_invalid_params`` handed
``handle_params`` the initial value of EVERY field, so ``?page=abc&sort=name``
lost the valid ``sort=name`` along with the invalid ``page``. A view that sets
``params_form_fallback = "fields"`` keeps what validated and replaces only the
fields in ``form.errors`` with their initial value. ``"initial"`` stays the
default.

Every case runs on every transport that applies the decision (#1646): the HTTP
GET, the socket mount, ``url_change`` over the socket, the SSE mount, a
``url_change`` over SSE, the stateless HTTP fallback POST, and the test client's
``mount`` / ``url_change``. They all reach the decision through
``run_handle_params`` / ``run_handle_params_sync``, but that is the claim under
test, so each is driven for real rather than trusted.
"""

from __future__ import annotations

import json
import re
import sys
import uuid

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from djust import LiveView
from djust.testing import LiveViewTestClient

from ._ws_frames import drain_extra, receive_until

pytestmark = pytest.mark.django_db

#: ``(params handle_params received, uri, params_errors at that moment)``, one per call.
CALLS: list = []
INVALID_CALLS: list = []

SORTS = [("name", "Name"), ("date", "Date")]


# --------------------------------------------------------------------------- #
# Forms
# --------------------------------------------------------------------------- #


class FilterParams(forms.Form):
    page = forms.IntegerField(min_value=1, required=False, initial=1)
    sort = forms.ChoiceField(choices=SORTS, required=False, initial="date")
    q = forms.CharField(required=False, initial="")


class RequiredParams(forms.Form):
    token = forms.CharField(required=True)
    page = forms.IntegerField(min_value=1, required=False, initial=1)


class RangeParams(forms.Form):
    """``clean()`` raises WITHOUT naming a field: the combination is what is wrong."""

    lo = forms.IntegerField(required=False, initial=0)
    hi = forms.IntegerField(required=False, initial=10)
    page = forms.IntegerField(min_value=1, required=False, initial=1)

    def clean(self):
        cleaned = super().clean()
        lo, hi = cleaned.get("lo"), cleaned.get("hi")
        if lo is not None and hi is not None and lo > hi:
            raise forms.ValidationError("The range is empty.")
        return cleaned


class AttributedRangeParams(RangeParams):
    """The same rule, but the error names the field that is wrong."""

    def clean(self):
        cleaned = forms.Form.clean(self)
        lo, hi = cleaned.get("lo"), cleaned.get("hi")
        if lo is not None and hi is not None and lo > hi:
            self.add_error("hi", "Must not be below the low end.")
        return cleaned


class FlagParams(forms.Form):
    flag = forms.BooleanField(required=False, initial=True)
    page = forms.IntegerField(min_value=1, required=False, initial=1)


class SpanParams(forms.Form):
    """The review's case: ``hi`` is replaced by ``3``, a pair with ``lo`` that ``clean()`` never saw."""

    lo = forms.IntegerField(required=False, initial=0)
    hi = forms.IntegerField(required=False, initial=3)

    def clean(self):
        cleaned = super().clean()
        lo, hi = cleaned.get("lo"), cleaned.get("hi")
        if lo is not None and hi is not None and lo > hi:
            self.add_error("hi", "hi must be >= lo")
        return cleaned


class SpanRaisesParams(SpanParams):
    """The same rule, raised instead of attributed: it only fires when both fields are present."""

    def clean(self):
        cleaned = forms.Form.clean(self)
        lo, hi = cleaned.get("lo"), cleaned.get("hi")
        if lo is not None and hi is not None and lo > hi:
            raise forms.ValidationError("empty span")
        return cleaned


class PostCleanParams(RangeParams):
    """A form-level error that does NOT come from ``clean()`` (a ModelForm's ``_post_clean`` does this).

    ``clean()`` accepts the merged values, so only the form-level error of the first bind can say
    the combination was refused.
    """

    def clean(self):
        return forms.Form.clean(self)

    def _post_clean(self):
        super()._post_clean()
        lo, hi = self.cleaned_data.get("lo"), self.cleaned_data.get("hi")
        if lo is not None and hi is not None and lo > hi:
            self.add_error(None, "The range is empty.")


class CrashesOnNoneParams(forms.Form):
    """``clean()`` survives a missing ``hi`` (default 0) but not the ``None`` an initial can be."""

    lo = forms.IntegerField(required=False, initial=0)
    hi = forms.IntegerField(required=False)  # no initial: replaced by None

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("lo") or 0) < 0:
            (
                cleaned.get("hi", 0) + 1
            )  # fine for a missing hi, TypeError for the None an initial can be
        return cleaned


class RepairParams(forms.Form):
    """``clean()`` REPAIRS the KEPT low end of an inverted range (in place); the valid path hands over the repair."""

    lo = forms.IntegerField(required=False, initial=0)
    hi = forms.IntegerField(required=False, initial=3)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("lo") is not None and cleaned.get("hi") is not None:
            if cleaned["lo"] > cleaned["hi"]:
                cleaned["lo"] = cleaned["hi"]
        return cleaned


class RepairHiParams(RepairParams):
    """The repair writes the field that FAILED (``hi``): not adopted, a replaced field is its initial."""

    def clean(self):
        cleaned = forms.Form.clean(self)
        if cleaned.get("lo") is not None and cleaned.get("hi") is not None:
            if cleaned["lo"] > cleaned["hi"]:
                cleaned["hi"] = cleaned["lo"]
        return cleaned


class RepairReturnsParams(RepairParams):
    """The same repair, handed back as a NEW dict (``clean()``'s return value wins)."""

    def clean(self):
        cleaned = forms.Form.clean(self)
        if cleaned.get("lo") is not None and cleaned.get("hi") is not None:
            if cleaned["lo"] > cleaned["hi"]:
                return {**cleaned, "lo": cleaned["hi"]}
        return cleaned


class CleanFieldParams(forms.Form):
    """A rule spanning two fields in ``clean_end``, which the second pass does not run."""

    start = forms.IntegerField(required=False, initial=10)
    end = forms.IntegerField(required=False, initial=20)

    def clean_end(self):
        end, start = self.cleaned_data["end"], self.cleaned_data.get("start")
        if start is not None and end is not None and end < start:
            raise forms.ValidationError("end before start")
        return end


class RereadParams(forms.Form):
    """``clean()`` re-reads ``self.data``: a failed field's RAW value must still never get out."""

    q = forms.CharField(max_length=5, required=False, initial="")
    page = forms.IntegerField(min_value=1, max_value=10, required=False, initial=1)

    def clean(self):
        cleaned = super().clean()
        for name in ("q", "page"):
            if name in self.data:
                cleaned[name] = self.data[name]  # the raw text, valid or not
        return cleaned


class PriceParams(forms.Form):
    """A NON-idempotent ``clean()``: documented to give a different value in the fallback."""

    price = forms.IntegerField(required=False, initial=0)
    page = forms.IntegerField(min_value=1, required=False, initial=1)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("price") is not None:
            cleaned["price"] = cleaned["price"] * 100
        return cleaned


class OrmParams(forms.Form):
    """``clean()`` uses the ORM: fine in a worker thread, an error on the event loop."""

    a = forms.IntegerField(required=False, initial=1)
    b = forms.IntegerField(required=False, initial=2)

    def clean(self):
        from django.contrib.auth.models import User

        User.objects.count()
        return super().clean()


class NoteParams(forms.Form):
    """A field with NO initial value: ``None`` when replaced, never the empty string."""

    page = forms.IntegerField(min_value=1, required=False, initial=1)
    note = forms.CharField(required=False)


class CopiesParams(forms.Form):
    """#3891: ``clean()`` copies the raw ``q`` into ``a``, a field the URL did not supply."""

    a = forms.CharField(required=False, initial="da")
    q = forms.CharField(max_length=5, required=False, initial="")

    def clean(self):
        cleaned = super().clean()
        if "q" in self.data:
            cleaned["a"] = self.data["q"]  # raw request data, valid or not
        return cleaned


class CopiesNoInitialParams(CopiesParams):
    """The same, but ``a`` has NO initial value: nothing is filled over ``clean()``'s write."""

    a = forms.CharField(required=False)


class CopiesCallableParams(CopiesParams):
    """The same, but ``a``'s initial is a callable."""

    a = forms.CharField(required=False, initial=lambda: "dyn")


class CopiesManyParams(forms.Form):
    """Several kept fields: two unsupplied (``a``, ``b``, both overwritten) and one the URL supplies."""

    a = forms.CharField(required=False, initial="da")
    b = forms.CharField(required=False, initial="db")
    c = forms.CharField(required=False, initial="dc")
    q = forms.CharField(max_length=5, required=False, initial="")

    def clean(self):
        cleaned = super().clean()
        if "q" in self.data:
            cleaned["a"] = cleaned["b"] = cleaned["c"] = self.data["q"]
        return cleaned


class CopiesRequiredParams(forms.Form):
    """A REQUIRED field next to the copy: unsupplied it fails and is replaced, supplied it is kept."""

    token = forms.CharField(required=True)
    a = forms.CharField(required=False, initial="da")
    q = forms.CharField(max_length=5, required=False, initial="")

    def clean(self):
        cleaned = super().clean()
        if "q" in self.data:
            cleaned["a"] = self.data["q"]
        return cleaned


class CopiesRaisesParams(CopiesParams):
    """``clean()`` raises on the copied value: a form-level error, so nothing is kept."""

    def clean(self):
        cleaned = super().clean()
        if str(cleaned.get("a", "")).startswith("boom"):
            raise forms.ValidationError("refused")
        return cleaned


class SpanDefaultParams(forms.Form):
    """#3921 review: a default ``start`` next to a supplied ``end`` that ``clean()`` refuses."""

    page = forms.IntegerField(min_value=1, required=False, initial=1)
    start = forms.IntegerField(required=False, initial=5)
    end = forms.IntegerField(required=False)  # no initial

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start is not None and end is not None and end < start:
            raise forms.ValidationError("end before start")
        return cleaned


class SpanCallableParams(SpanDefaultParams):
    """The same rule with a CALLABLE default for ``start``."""

    start = forms.IntegerField(required=False, initial=lambda: 5)


class SpanTextParams(forms.Form):
    """Text fields: a CharField strips a whitespace-only value to empty (but the URL "supplied" it)."""

    page = forms.IntegerField(min_value=1, required=False, initial=1)
    start = forms.CharField(required=False, initial="m")
    end = forms.CharField(required=False)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            raise forms.ValidationError("end before start")
        return cleaned


class CopiesTagsParams(forms.Form):
    """A multi-select the URL left out counts as supplied (#3922): the paths must still agree."""

    tags = forms.MultipleChoiceField(
        choices=[("ok", "ok"), ("x", "x")], required=False, initial=["x"]
    )
    q = forms.CharField(max_length=5, required=False, initial="")

    def clean(self):
        cleaned = super().clean()
        if "q" in self.data:
            cleaned["tags"] = [self.data["q"]]
        return cleaned


class CopiesModelParams(forms.ModelForm):
    """A ModelForm: ``last_name`` takes the instance's value as its initial, ``clean()`` overwrites it."""

    first_name = forms.CharField(max_length=5, required=False)

    class Meta:
        model = User
        fields = ["first_name", "last_name"]

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("instance", User(last_name="Default"))
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if "first_name" in self.data:
            cleaned["last_name"] = self.data["first_name"]
        return cleaned


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #

_TEMPLATE = '<div dj-root><p id="state">{{ shown }}</p><p>ERRS:{{ params_errors|length }}</p></div>'


class _Base(LiveView):
    login_required = False
    template = _TEMPLATE

    def mount(self, request, **kwargs):
        self.shown = "unset"

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri, dict(self.params_errors)))
        self.shown = json.dumps(dict(params), sort_keys=True, default=str)


class FieldsView(_Base):
    params_form = FilterParams
    params_form_fallback = "fields"


class DefaultView(_Base):
    params_form = FilterParams


class ExplicitInitialView(_Base):
    params_form = FilterParams
    params_form_fallback = "initial"


class RequiredFields(_Base):
    params_form = RequiredParams
    params_form_fallback = "fields"


class RangeFields(_Base):
    params_form = RangeParams
    params_form_fallback = "fields"


class AttributedFields(_Base):
    params_form = AttributedRangeParams
    params_form_fallback = "fields"


class BoolFields(_Base):
    params_form = FlagParams
    params_form_fallback = "fields"


class SpanFields(_Base):
    params_form = SpanParams
    params_form_fallback = "fields"


class SpanRaisesFields(_Base):
    params_form = SpanRaisesParams
    params_form_fallback = "fields"


class PostCleanFields(_Base):
    params_form = PostCleanParams
    params_form_fallback = "fields"


class RepairFields(_Base):
    params_form = RepairParams
    params_form_fallback = "fields"


class CleanFieldFields(_Base):
    params_form = CleanFieldParams
    params_form_fallback = "fields"


class RereadFields(_Base):
    params_form = RereadParams
    params_form_fallback = "fields"


class PriceFields(_Base):
    params_form = PriceParams
    params_form_fallback = "fields"


class OrmSync(_Base):
    """A sync override that calls ``super()``: the default runs off the loop, so the ORM works."""

    params_form = OrmParams
    params_form_fallback = "fields"

    def handle_invalid_params(self, form, uri):
        super().handle_invalid_params(form, uri)


class OrmAsync(OrmSync):
    """An ``async def`` override that calls ``super()``: the second clean() runs ON the loop."""

    async def handle_invalid_params(self, form, uri):
        super().handle_invalid_params(form, uri)


class CrashFields(_Base):
    params_form = CrashesOnNoneParams
    params_form_fallback = "fields"


class NoteFields(_Base):
    params_form = NoteParams
    params_form_fallback = "fields"


class CopiesFields(_Base):
    params_form = CopiesParams
    params_form_fallback = "fields"


class CopiesInitial(_Base):
    params_form = CopiesParams
    params_form_fallback = "initial"


class CopiesNoInitialFields(_Base):
    params_form = CopiesNoInitialParams
    params_form_fallback = "fields"


class CopiesCallableFields(_Base):
    params_form = CopiesCallableParams
    params_form_fallback = "fields"


class CopiesManyFields(_Base):
    params_form = CopiesManyParams
    params_form_fallback = "fields"


class CopiesRequiredFields(_Base):
    params_form = CopiesRequiredParams
    params_form_fallback = "fields"


class CopiesRaisesFields(_Base):
    params_form = CopiesRaisesParams
    params_form_fallback = "fields"


class CopiesModelFields(_Base):
    params_form = CopiesModelParams
    params_form_fallback = "fields"


class SpanDefaultFields(_Base):
    params_form = SpanDefaultParams
    params_form_fallback = "fields"


class SpanCallableFields(_Base):
    params_form = SpanCallableParams
    params_form_fallback = "fields"


class SpanTextFields(_Base):
    params_form = SpanTextParams
    params_form_fallback = "fields"


class AsyncFields(FieldsView):
    """An ``async def handle_params``: the default must still await it."""

    async def handle_params(self, params, uri):
        CALLS.append((dict(params), uri, dict(self.params_errors)))
        self.shown = json.dumps(dict(params), sort_keys=True, default=str)


class SuperFields(FieldsView):
    """The documented ``super().handle_invalid_params(form, uri)`` pattern."""

    def handle_invalid_params(self, form, uri):
        INVALID_CALLS.append((form, uri))
        super().handle_invalid_params(form, uri)


class BadMode(_Base):
    params_form = FilterParams
    params_form_fallback = "field"  # a typo for "fields"


class NoFormBadMode(_Base):
    """No ``params_form``: the attribute is never read, so it is not an error here."""

    params_form_fallback = "nonsense"

    def handle_params(self, params, uri):
        CALLS.append((dict(params), uri, {}))
        self.shown = "raw"


_VIEWS = (
    FieldsView,
    DefaultView,
    ExplicitInitialView,
    RequiredFields,
    RangeFields,
    AttributedFields,
    BoolFields,
    NoteFields,
    CopiesFields,
    CopiesInitial,
    CopiesNoInitialFields,
    CopiesCallableFields,
    CopiesManyFields,
    CopiesRequiredFields,
    CopiesRaisesFields,
    CopiesModelFields,
    SpanDefaultFields,
    SpanCallableFields,
    SpanTextFields,
    SpanFields,
    SpanRaisesFields,
    CrashFields,
    RepairFields,
    CleanFieldFields,
    RereadFields,
    PriceFields,
    OrmSync,
    OrmAsync,
    PostCleanFields,
    AsyncFields,
    SuperFields,
    BadMode,
    NoFormBadMode,
)
for _cls in _VIEWS:
    setattr(sys.modules[__name__], _cls.__name__, _cls)

urlpatterns = [path("%s/" % cls.__name__.lower(), cls.as_view()) for cls in _VIEWS]


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]
    CALLS.clear()
    INVALID_CALLS.clear()
    yield
    CALLS.clear()
    INVALID_CALLS.clear()


def _url(view_name: str, query: dict | None = None) -> str:
    base = "/%s/" % view_name.lower()
    if not query:
        return base
    from urllib.parse import urlencode

    return "%s?%s" % (base, urlencode(query))


# --------------------------------------------------------------------------- #
# The cases: (view, query, what handle_params receives, the fields in params_errors)
# --------------------------------------------------------------------------- #

#: A request value no field accepts and no response should carry (#3891).
RAW = "<script>alert(1)</script>"

KEPT = {"page": 1, "sort": "name", "q": "hello"}
INITIAL = {"page": 1, "sort": "date", "q": ""}

CASES = [
    # The issue's example: the invalid page is replaced, the valid sort and q are kept.
    ("FieldsView", {"page": "abc", "sort": "name", "q": "hello"}, KEPT, {"page"}),
    # The other field fails: page survives, sort takes its initial value.
    (
        "FieldsView",
        {"page": "2", "sort": "bogus", "q": "x"},
        {"page": 2, "sort": "date", "q": "x"},
        {"sort"},
    ),
    # A value that fails a validator (min_value) is replaced, not clamped or kept.
    ("FieldsView", {"page": "0", "sort": "name"}, {"page": 1, "sort": "name", "q": ""}, {"page"}),
    # Every field fails: nothing to keep, the result is the initial values.
    ("FieldsView", {"page": "abc", "sort": "bogus"}, INITIAL, {"page", "sort"}),
    # A field the URL left out is valid by the form's own say-so (it is optional): it takes its
    # initial value, exactly as on a valid URL.
    ("FieldsView", {"page": "abc"}, INITIAL, {"page"}),
    # A valid URL never reaches the fallback: unchanged from before.
    ("FieldsView", {"page": "3", "sort": "name"}, {"page": 3, "sort": "name", "q": ""}, set()),
    # The same URLs with the default: the initial value of every field (unchanged behaviour).
    ("DefaultView", {"page": "abc", "sort": "name", "q": "hello"}, INITIAL, {"page"}),
    ("DefaultView", {"page": "2", "sort": "bogus", "q": "x"}, INITIAL, {"sort"}),
    ("ExplicitInitialView", {"page": "abc", "sort": "name", "q": "hello"}, INITIAL, {"page"}),
    ("DefaultView", {"page": "3", "sort": "name"}, {"page": 3, "sort": "name", "q": ""}, set()),
    # A required field the URL left out is NOT valid by default: the form says it is required.
    ("RequiredFields", {"page": "3"}, {"token": None, "page": 3}, {"token"}),
    ("RequiredFields", {"token": "t", "page": "zzz"}, {"token": "t", "page": 1}, {"page"}),
    # A form-level error says the COMBINATION is wrong: nothing is kept, not even the page.
    (
        "RangeFields",
        {"lo": "5", "hi": "2", "page": "4"},
        {"lo": 0, "hi": 10, "page": 1},
        {"__all__"},
    ),
    # ... and a field error next to a form-level one still keeps nothing.
    (
        "RangeFields",
        {"lo": "7", "hi": "2", "page": "zzz"},
        {"lo": 0, "hi": 10, "page": 1},
        {"__all__", "page"},
    ),
    # clean() runs on what validated; with `hi` gone it has no range to judge, so `lo` is kept.
    ("RangeFields", {"lo": "5", "hi": "zzz"}, {"lo": 5, "hi": 10, "page": 1}, {"hi"}),
    # clean() that names the field is a field error: the siblings are kept.
    (
        "AttributedFields",
        {"lo": "5", "hi": "2", "page": "4"},
        {"lo": 5, "hi": 10, "page": 4},
        {"hi"},
    ),
    # A checkbox the URL left out takes its initial value; one it supplied is kept.
    ("BoolFields", {"page": "abc"}, {"flag": True, "page": 1}, {"page"}),
    ("BoolFields", {"page": "abc", "flag": "false"}, {"flag": False, "page": 1}, {"page"}),
    # The merged result is re-checked by clean(): a kept `lo=9` next to the replaced `hi`'s initial
    # `3` is a pair clean() never saw and rejects, so nothing is kept (the review's reproducer).
    ("SpanFields", {"lo": "9", "hi": "2"}, {"lo": 0, "hi": 3}, {"hi"}),
    ("SpanRaisesFields", {"lo": "9", "hi": "zzz"}, {"lo": 0, "hi": 3}, {"hi"}),
    # ... and when the merged pair satisfies the rule, the kept sibling stays.
    ("SpanFields", {"lo": "2", "hi": "zzz"}, {"lo": 2, "hi": 3}, {"hi"}),
    ("SpanRaisesFields", {"lo": "1", "hi": "zzz"}, {"lo": 1, "hi": 3}, {"hi"}),
    # A form-level error raised OUTSIDE clean() (a ModelForm's _post_clean) is invisible to the
    # re-check, so the first bind's form-level error alone must keep nothing.
    (
        "PostCleanFields",
        {"lo": "5", "hi": "2", "page": "4"},
        {"lo": 0, "hi": 10, "page": 1},
        {"__all__"},
    ),
    # What clean() repairs in a KEPT field it repairs here as on the valid path: (lo=9, hi=3) -> lo
    # repaired to 3 (hi, replaced by its initial 3, is not written by clean()).
    ("RepairFields", {"lo": "9", "hi": "zzz"}, {"lo": 3, "hi": 3}, {"hi"}),
    # The documented limit: clean_<field>() is NOT run again on the merged values, so a rule that
    # spans fields there is skipped (start failed, end=1 sits next to start's initial 10).
    ("CleanFieldFields", {"start": "zz", "end": "1"}, {"start": 10, "end": 1}, {"start"}),
    # clean() re-reads self.data: the failed q is its INITIAL value, never the raw URL text (what
    # "initial" mode hands over for q); page was kept and keeps its raw-read value.
    (
        "RereadFields",
        {"q": "<script>alert(1)</script>", "page": "5"},
        {"q": "", "page": "5"},
        {"q"},
    ),
    # A clean() that is NOT idempotent: documented, the valid path gives 500 and the fallback 50000.
    ("PriceFields", {"price": "5", "page": "zz"}, {"price": 50000, "page": 1}, {"page"}),
    # A clean() that cannot take the None an initial can be falls back to the initials, not a 500.
    ("CrashFields", {"lo": "-1", "hi": "zzz"}, {"lo": 0, "hi": None}, {"hi"}),
    # A kept field the URL left out and whose form declares NO initial is the value the valid path
    # hands over ("" for a CharField), in the fallback as on a valid URL.
    ("NoteFields", {"page": "abc"}, {"page": 1, "note": ""}, {"page"}),
    ("NoteFields", {"page": "2"}, {"page": 2, "note": ""}, set()),
    ("NoteFields", {"page": "abc", "note": "hi"}, {"page": 1, "note": "hi"}, {"page"}),
    # An async handle_params and a super() override see the same kept values.
    ("AsyncFields", {"page": "abc", "sort": "name", "q": "hello"}, KEPT, {"page"}),
    ("SuperFields", {"page": "abc", "sort": "name", "q": "hello"}, KEPT, {"page"}),
    # #3891: a KEPT field the URL did not supply takes its initial value AFTER clean() ran, as on
    # the valid path, so a clean() that copies the failed raw q into it cannot hand the raw value over.
    ("CopiesFields", {"q": RAW}, {"a": "da", "q": ""}, {"q"}),
    ("CopiesFields", {"q": "ok"}, {"a": "da", "q": "ok"}, set()),  # the valid path, for comparison
    # A field the URL supplied keeps clean()'s write, on both paths (the documented trust boundary).
    ("CopiesFields", {"a": "x", "q": "ok"}, {"a": "ok", "q": "ok"}, set()),
    ("CopiesFields", {"a": "x", "q": RAW}, {"a": RAW, "q": ""}, {"q"}),
    # A field the URL supplied EMPTY counts as left out, as on the valid path.
    ("CopiesFields", {"a": "", "q": "ok"}, {"a": "da", "q": "ok"}, set()),
    ("CopiesFields", {"a": "", "q": RAW}, {"a": "da", "q": ""}, {"q"}),
    # "initial" never handed a raw value over: unchanged, supplied or not.
    ("CopiesInitial", {"q": RAW}, {"a": "da", "q": ""}, {"q"}),
    ("CopiesInitial", {"a": "x", "q": RAW}, {"a": "da", "q": ""}, {"q"}),
    ("CopiesInitial", {"q": "ok"}, {"a": "da", "q": "ok"}, set()),
    # No initial value: nothing to fill over clean()'s write, on the valid path and the fallback alike.
    ("CopiesNoInitialFields", {"q": "ok"}, {"a": "ok", "q": "ok"}, set()),
    ("CopiesNoInitialFields", {"q": RAW}, {"a": RAW, "q": ""}, {"q"}),
    # A callable initial is what the field takes.
    ("CopiesCallableFields", {"q": "ok"}, {"a": "dyn", "q": "ok"}, set()),
    ("CopiesCallableFields", {"q": RAW}, {"a": "dyn", "q": ""}, {"q"}),
    # Several kept fields: every unsupplied one is filled, the supplied one keeps clean()'s write.
    ("CopiesManyFields", {"q": "ok"}, {"a": "da", "b": "db", "c": "dc", "q": "ok"}, set()),
    ("CopiesManyFields", {"q": RAW}, {"a": "da", "b": "db", "c": "dc", "q": ""}, {"q"}),
    ("CopiesManyFields", {"c": "x", "q": RAW}, {"a": "da", "b": "db", "c": RAW, "q": ""}, {"q"}),
    # A required field: unsupplied it is replaced (None) and the copy still cannot get out; supplied
    # it is kept.
    ("CopiesRequiredFields", {"q": RAW}, {"token": None, "a": "da", "q": ""}, {"token", "q"}),
    ("CopiesRequiredFields", {"token": "t", "q": RAW}, {"token": "t", "a": "da", "q": ""}, {"q"}),
    (
        "CopiesRequiredFields",
        {"token": "t", "q": "ok"},
        {"token": "t", "a": "da", "q": "ok"},
        set(),
    ),
    # clean() raising on the copied value is a form-level error: nothing is kept.
    ("CopiesRaisesFields", {"q": "boomboom"}, {"a": "da", "q": ""}, {"__all__", "q"}),
    ("CopiesRaisesFields", {"q": "boom"}, {"a": "da", "q": ""}, {"__all__"}),
    # The guarantee (#3921 review): what is handed over is a combination clean() accepts, or the
    # plain initials. A default start=5 next to a supplied end=2 is refused by clean(), so nothing
    # is kept (as before #3891); end=9 is accepted and honoured; the valid URL is for comparison.
    (
        "SpanDefaultFields",
        {"page": "abc", "end": "2"},
        {"page": 1, "start": 5, "end": None},
        {"page"},
    ),
    ("SpanDefaultFields", {"page": "abc", "end": "9"}, {"page": 1, "start": 5, "end": 9}, {"page"}),
    ("SpanDefaultFields", {"end": "9"}, {"page": 1, "start": 5, "end": 9}, set()),
    # A default the URL supplied EMPTY counts as left out: it is filled before the check too.
    (
        "SpanDefaultFields",
        {"page": "abc", "start": "", "end": "2"},
        {"page": 1, "start": 5, "end": None},
        {"page"},
    ),
    # A whitespace default is "supplied" to _url_supplied (the key carries a non-empty text) but a
    # CharField strips it to empty: it is filled before the check too, else the pair start="m",
    # end="a" would be handed over although clean() refuses it.
    (
        "SpanTextFields",
        {"page": "abc", "start": " ", "end": "a"},
        {"page": 1, "start": "m", "end": None},
        {"page"},
    ),
    (
        "SpanTextFields",
        {"page": "abc", "start": " ", "end": "z"},
        {"page": 1, "start": "m", "end": "z"},
        {"page"},
    ),
    (
        "SpanTextFields",
        {"page": "abc", "end": "a"},
        {"page": 1, "start": "m", "end": None},
        {"page"},
    ),
    # ... and so is a callable default.
    (
        "SpanCallableFields",
        {"page": "abc", "end": "2"},
        {"page": 1, "start": 5, "end": None},
        {"page"},
    ),
    (
        "SpanCallableFields",
        {"page": "abc", "end": "9"},
        {"page": 1, "start": 5, "end": 9},
        {"page"},
    ),
    (
        "SpanDefaultFields",
        {"page": "abc", "start": "3", "end": "4"},
        {"page": 1, "start": 3, "end": 4},
        {"page"},
    ),
    # A ModelForm: last_name's initial comes from the instance.
    (
        "CopiesModelFields",
        {"first_name": "toolong!"},
        {"first_name": "", "last_name": "Default"},
        {"first_name"},
    ),
    (
        "CopiesModelFields",
        {"first_name": "ok"},
        {"first_name": "ok", "last_name": "Default"},
        set(),
    ),
]


class Seen:
    """What a transport showed for one visit."""

    def __init__(self, *, failed=False, html=""):
        self.failed = failed
        self.html = html


def _state(html: str) -> dict:
    match = re.search(r'<p[^>]*id="state"[^>]*>([^<]*)</p>', html)
    assert match, html
    import html as html_lib

    return json.loads(html_lib.unescape(match.group(1)))


class _Cases:
    """Every case, on whichever transport the subclass drives."""

    async def visit(self, view_name: str, query: dict) -> Seen:  # pragma: no cover - abstract
        raise NotImplementedError

    #: The test client does not render the page, so it has no HTML to read back.
    renders = True

    @pytest.mark.parametrize("view_name, query, expected, error_fields", CASES)
    async def test_the_fallback_is_what_the_view_declared(
        self, view_name, query, expected, error_fields
    ):
        seen = await self.visit(view_name, query)
        assert not seen.failed
        params, _uri, errors = CALLS[-1]
        assert params == expected
        assert set(errors) == error_fields
        if self.renders and seen.html:
            assert _state(seen.html) == expected

    async def test_a_value_that_failed_never_reaches_handle_params(self):
        """The security property: only a cleaned value or an initial value is handed over."""
        seen = await self.visit(
            "FieldsView", {"page": "<script>alert(1)</script>", "sort": "x'; drop", "q": "ok"}
        )
        assert not seen.failed
        assert CALLS[-1][0] == {"page": 1, "sort": "date", "q": "ok"}
        assert "alert(1)" not in seen.html and "drop" not in seen.html

    async def test_a_failed_value_does_not_reach_handle_params_through_an_unsupplied_kept_field(
        self,
    ):
        """#3891: the raw value a clean() copies into a field the URL left out is that field's initial."""
        seen = await self.visit("CopiesFields", {"q": RAW})
        assert not seen.failed
        assert CALLS[-1][0] == {"a": "da", "q": ""}
        assert "alert(1)" not in seen.html

    async def test_the_url_a_view_with_no_form_gets_is_untouched(self):
        seen = await self.visit("NoFormBadMode", {"page": "abc", "x": "y"})
        assert not seen.failed
        assert CALLS[-1][0] == {"page": "abc", "x": "y"}

    @pytest.mark.parametrize("query", [{}, {"page": "abc"}])
    async def test_a_mode_nothing_implements_fails_the_request_valid_url_or_not(self, query):
        """``"field"`` is a typo for ``"fields"``: it must not silently mean ``"initial"``."""
        seen = await self.visit("BadMode", query)
        assert seen.failed
        assert CALLS == []

    async def test_a_form_that_was_refused_before_it_was_validated_keeps_nothing(self):
        """Past the form-data bounds the form is bound to nothing (#3834): there is nothing to keep.

        An all-optional form bound to nothing is VALID-looking (no field errors), which is why
        this must not read ``cleaned_data``: the note the URL carried was never validated.
        """
        query = {"note": "hello", "x1": "1", "x2": "2", "x3": "3", "x4": "4"}
        config = {"rate_limit": {"rate": 10**9, "burst": 10**9}, "max_form_fields": 1}
        with override_settings(LIVEVIEW_CONFIG=config):
            seen = await self.visit("NoteFields", query)
        assert not seen.failed
        params, _uri, errors = CALLS[-1]
        # `note` has no initial value: it is None (what "initial" hands over), not the "" a form
        # bound to nothing would clean it to.
        assert params == {"page": 1, "note": None}
        assert set(errors) == {"__all__"}


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #


class TestHttpGet(_Cases):
    async def visit(self, view_name, query):
        response = await sync_to_async(Client(raise_request_exception=False).get)(
            _url(view_name, query)
        )
        if response.status_code != 200:
            return Seen(failed=True)
        return Seen(html=response.content.decode())


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect(view_name, query):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    def _create_session():
        store = SessionStore()
        store.create()
        return store.session_key

    key = await sync_to_async(_create_session)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()  # connect frame
    await communicator.send_json_to(
        {
            "type": "mount",
            "view": "%s.%s" % (__name__, view_name),
            "url": _url(view_name),
            "params": query,
        }
    )
    frames = await receive_until(
        communicator, lambda fs: any(f.get("type") in ("mount", "error") for f in fs)
    )
    return communicator, frames[-1]


class TestSocketMount(_Cases):
    async def visit(self, view_name, query):
        communicator, frame = await _connect(view_name, query)
        try:
            if frame["type"] == "error":
                return Seen(failed=True)
            return Seen(html=frame["html"])
        finally:
            await communicator.disconnect()


class TestSocketUrlChange(_Cases):
    """Mounted on a clean URL, then ``live_patch``-ed to the one under test."""

    async def visit(self, view_name, query):
        communicator, frame = await _connect(view_name, {})
        try:
            if frame["type"] == "error":
                return Seen(failed=True)
            await drain_extra(communicator)
            CALLS.clear()
            await communicator.send_json_to(
                {"type": "url_change", "params": query, "uri": _url(view_name, query)}
            )
            frames = await receive_until(
                communicator,
                lambda fs: any(f.get("type") in ("patch", "html_update", "error") for f in fs),
            )
            last = frames[-1]
            if last["type"] == "error":
                return Seen(failed=True)
            # A patch carries only what changed; the state paragraph is the proof that matters and
            # the CALLS record is the assertion, so the frame text is only scanned for leaks.
            return Seen(html=json.dumps(last))
        finally:
            await communicator.disconnect()

    # url_change's frame is a patch, not the page: the state paragraph cannot be parsed back.
    renders = False


def _sse_request(method, url, body, key=None):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = SessionStore(key)
    if key is None:
        request.session.create()
    request.session = SessionStore(request.session.session_key)
    request.user = AnonymousUser()
    request.tenant = None
    return request


def _drain(session):
    frames = []
    while not session.queue.empty():
        frame = session.queue.get_nowait()
        if frame is not None:
            frames.append(frame)
    return frames


class _SseCases(_Cases):
    async def _start(self, view_name, query):
        from djust.sse import DjustSSEStreamView, _sse_sessions

        sid = str(uuid.uuid4())
        request = await sync_to_async(_sse_request)(
            "GET",
            "/djust/sse/%s/" % sid,
            {"view": "%s.%s" % (__name__, view_name), "_djust_url": _url(view_name), **query},
        )
        response = await DjustSSEStreamView().get(request, session_id=sid)
        session = _sse_sessions.get(sid)
        return response, session, request.session.session_key, sid


@pytest.mark.django_db(transaction=True)
class TestSseMount(_SseCases):
    async def visit(self, view_name, query):
        from djust.sse import _sse_sessions

        response, session, _key, sid = await self._start(view_name, query)
        try:
            if session is None:  # a failed mount registers nothing (#1237 finding 25)
                return Seen(failed=True)
            frames = _drain(session)
            mounts = [f for f in frames if f.get("type") == "mount"]
            if not mounts or any(f.get("type") == "error" for f in frames):
                return Seen(failed=True)
            return Seen(html=mounts[-1]["html"])
        finally:
            _sse_sessions.pop(sid, None)


@pytest.mark.django_db(transaction=True)
class TestSseUrlChange(_SseCases):
    renders = False

    async def visit(self, view_name, query):
        from djust.sse import DjustSSEMessageView, _sse_sessions

        response, session, key, sid = await self._start(view_name, {})
        try:
            if session is None:
                return Seen(failed=True)
            _drain(session)
            CALLS.clear()
            request = await sync_to_async(_sse_request)(
                "POST",
                "/djust/sse/%s/message/" % sid,
                {"type": "url_change", "params": query, "uri": _url(view_name, query)},
                key,
            )
            response = await DjustSSEMessageView().post(request, session_id=sid)
            assert response.status_code == 200
            frames = _drain(session)
            if any(f.get("type") == "error" for f in frames):
                return Seen(failed=True)
            return Seen(html=json.dumps(frames))
        finally:
            _sse_sessions.pop(sid, None)


def _stateless():
    """The stateless config ON TOP OF whatever a test already set (it must not undo a limit)."""
    from django.conf import settings

    current = dict(getattr(settings, "LIVEVIEW_CONFIG", None) or {})
    return override_settings(LIVEVIEW_CONFIG={**current, "view_state_in_session": False})


class TestFallbackPost(_Cases):
    """The stateless HTTP fallback POST re-runs ``handle_params`` for the URL it came from."""

    renders = False

    async def visit(self, view_name, query):
        def run():
            url = _url(view_name, query)
            initial = RequestFactory().get(url)
            initial.user, initial.tenant = AnonymousUser(), None
            initial.session = SessionStore()
            initial.session.create()
            view_class = getattr(sys.modules[__name__], view_name)
            with _stateless():
                try:
                    view_class().get(initial)
                except ImproperlyConfigured:
                    return Seen(failed=True)
                CALLS.clear()
                request = RequestFactory().post(
                    url,
                    data=json.dumps({}),
                    content_type="application/json",
                    HTTP_X_DJUST_EVENT="noop",
                )
                request.user, request.session, request.tenant = (
                    initial.user,
                    initial.session,
                    None,
                )
                try:
                    response = view_class().post(request)
                except ImproperlyConfigured:
                    return Seen(failed=True)
            return Seen(failed=response.status_code >= 500)

        return await sync_to_async(run)()


class _ClientCases(_Cases):
    renders = False

    def _client(self, view_name):
        return LiveViewTestClient(getattr(sys.modules[__name__], view_name))


class TestTestClientMount(_ClientCases):
    async def visit(self, view_name, query):
        def run():
            try:
                self._client(view_name).mount(query_params=query)
            except ImproperlyConfigured:
                return Seen(failed=True)
            return Seen()

        return await sync_to_async(run)()


class TestTestClientUrlChange(_ClientCases):
    async def visit(self, view_name, query):
        def run():
            try:
                client = self._client(view_name).mount()
                CALLS.clear()
                client.url_change(query)
            except ImproperlyConfigured:
                return Seen(failed=True)
            return Seen()

        return await sync_to_async(run)()


# --------------------------------------------------------------------------- #
# The declaration
# --------------------------------------------------------------------------- #


class TestTheDeclaration:
    def test_the_default_is_the_conservative_one(self):
        assert LiveView.params_form_fallback == "initial"

    def test_the_name_is_framework_state_not_reactive_state(self):
        from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS

        assert "params_form_fallback" in _FRAMEWORK_INTERNAL_ATTRS

    def test_an_explicit_state_field_cannot_use_the_name(self):
        from djust._exposure import ExposureContract, ExposureError, FieldExposure

        with pytest.raises(ExposureError, match="reserved"):
            ExposureContract("app.View", {"params_form_fallback": FieldExposure()})

    def test_an_instance_assignment_is_not_reactive_state(self):
        class Assigns(LiveView):
            template = "<div dj-root></div>"

            def mount(self, request, **kwargs):
                self.params_form_fallback = "fields"
                self.visible = 1

        view = Assigns()
        view.mount(RequestFactory().get("/"))
        for state in (view.get_state(), view._capture_snapshot_state()):
            assert "params_form_fallback" not in state
            assert "visible" in state

    def test_an_override_that_never_calls_super_never_uses_the_mode_but_a_bad_one_is_refused(self):
        """The docs say "never uses", not "never reads": the eager check runs before the hook."""
        from djust._params_form import run_handle_params_sync

        class Overrides(LiveView):
            template = "<div dj-root></div>"
            params_form = FilterParams
            params_form_fallback = "fields"

            def handle_params(self, params, uri):
                CALLS.append((dict(params), uri, {}))

            def handle_invalid_params(self, form, uri):
                INVALID_CALLS.append((form, uri))

        run_handle_params_sync(Overrides(), {"page": "abc", "sort": "name"}, "/")
        assert CALLS == []  # the override took over: no mode applied

        class BadOverrides(Overrides):
            params_form_fallback = "oops"

        with pytest.raises(ImproperlyConfigured, match="params_form_fallback"):
            run_handle_params_sync(BadOverrides(), {"page": "abc"}, "/")

    def test_a_mixin_listed_after_liveview_is_shadowed_by_the_default(self):
        """Documented: LiveView defines the name, so the mixin has to come first."""

        class Mixin:
            params_form_fallback = "fields"

        class After(LiveView, Mixin):
            pass

        class Before(Mixin, LiveView):
            pass

        assert After.params_form_fallback == "initial"
        assert Before.params_form_fallback == "fields"

    def test_an_instance_attribute_does_not_change_the_declaration(self):
        """Read from the class, like ``params_form``: ``self.params_form_fallback = ...`` is inert."""
        from djust._params_form import declared_fallback

        view = DefaultView()
        view.params_form_fallback = "fields"
        assert declared_fallback(view) == "initial"

    def test_it_is_in_the_schema_and_the_stub(self):
        from pathlib import Path

        import djust
        from djust.schema import CLASS_ATTRIBUTES

        (entry,) = [a for a in CLASS_ATTRIBUTES if a["name"] == "params_form_fallback"]
        assert entry["default"] == "'initial'"
        assert entry["type"] == "str"
        stub = (Path(djust.__file__).parent / "live_view.pyi").read_text()
        assert 'params_form_fallback: str = "initial"' in stub


class TestFallbackProblem:
    @pytest.mark.parametrize("declared", ["initial", "fields"])
    def test_a_known_mode_has_no_problem(self, declared):
        from djust._params_form import fallback_problem

        assert fallback_problem(declared) is None

    @pytest.mark.parametrize(
        "declared", ["field", "Fields", "", None, True, 1, ["fields"], b"fields"]
    )
    def test_anything_else_does(self, declared):
        from djust._params_form import fallback_problem

        assert "must be one of 'initial', 'fields'" in fallback_problem(declared)


# --------------------------------------------------------------------------- #
# The decision itself, on a real bound form
# --------------------------------------------------------------------------- #


class DeletesParams(forms.Form):
    """``clean()`` removes a field from ``cleaned_data`` without reporting an error."""

    page = forms.IntegerField(min_value=1, required=False, initial=1)
    sort = forms.ChoiceField(choices=SORTS, required=False, initial="date")
    q = forms.CharField(required=False, initial="")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("q") == "drop":
            del cleaned["sort"]
        return cleaned


class LegacyErrorParams(forms.Form):
    """Writes ``self._errors`` directly (an old pattern): the field STAYS in ``cleaned_data``."""

    page = forms.IntegerField(min_value=1, required=False, initial=1)
    sort = forms.ChoiceField(choices=SORTS, required=False, initial="date")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("sort") == "name":
            self._errors["sort"] = self.error_class(["Not this one."])
        return cleaned


class TestFallbackValues:
    def _bound(self, form_class, params):
        from types import SimpleNamespace

        from djust._params_form import bind_params

        view = SimpleNamespace()
        form, values = bind_params(view, form_class, params)
        return form, values, view

    def test_a_field_clean_removed_without_an_error_is_not_trusted(self):
        from djust._params_form import fallback_values

        form, values, _ = self._bound(DeletesParams, {"page": "abc", "sort": "name", "q": "drop"})
        assert values is None
        assert "sort" not in form.cleaned_data and "sort" not in form.errors
        assert fallback_values(form, "fields") == {"page": 1, "sort": "date", "q": "drop"}

    def test_a_field_with_an_error_is_replaced_even_when_it_is_still_in_cleaned_data(self):
        """``add_error`` removes a failed field from ``cleaned_data``; writing ``_errors`` does not."""
        from djust._params_form import fallback_values

        form, values, _ = self._bound(LegacyErrorParams, {"page": "2", "sort": "name"})
        assert values is None
        assert form.cleaned_data["sort"] == "name" and "sort" in form.errors
        assert fallback_values(form, "fields") == {"page": 2, "sort": "date"}

    def test_a_field_the_recheck_clean_removes_takes_its_initial_value(self):
        from djust._params_form import fallback_values

        class Removes(forms.Form):
            lo = forms.IntegerField(required=False, initial=0)
            hi = forms.IntegerField(required=False, initial=3)
            n = forms.IntegerField(required=False, initial=1)

            def clean(self):
                cleaned = super().clean()
                cleaned.pop("hi", None)
                return cleaned

        form, values, _ = self._bound(Removes, {"lo": "2", "n": "zzz"})
        assert values is None
        assert fallback_values(form, "fields") == {"lo": 2, "hi": 3, "n": 1}

    def test_the_keys_are_the_declared_fields_in_order_and_nothing_else(self):
        from djust._params_form import fallback_values

        form, values, _ = self._bound(FilterParams, {"page": "abc", "sort": "name", "extra": "z"})
        assert list(fallback_values(form, "fields")) == ["page", "sort", "q"]
        assert fallback_values(form, "initial") == INITIAL
        assert list(fallback_values(form, "initial")) == ["page", "sort", "q"]

    def test_a_value_clean_added_is_not_carried_into_the_fallback(self):
        """The valid path keeps a key ``clean()`` derived; the fallback hands over declared fields only."""
        from djust._params_form import fallback_values

        class Derives(FilterParams):
            def clean(self):
                cleaned = super().clean()
                cleaned["derived"] = "from a form that may be half-validated"
                return cleaned

        form, values, _ = self._bound(Derives, {"page": "abc", "sort": "name"})
        assert values is None
        assert "derived" not in fallback_values(form, "fields")

    @pytest.mark.parametrize(
        "params, expected",
        [
            ({"lo": "9", "hi": "2"}, {"lo": 0, "hi": 3}),  # the recheck rejects
            ({"lo": "2", "hi": "zzz"}, {"lo": 2, "hi": 3}),  # the recheck accepts
        ],
    )
    def test_the_form_the_caller_holds_is_not_touched_by_the_recheck(self, params, expected):
        """``clean()`` runs on a copy: the form's errors and cleaned_data stay what the bind made."""
        from djust._params_form import fallback_values

        form, values, _ = self._bound(SpanFields.params_form, params)
        errors = {name: list(messages) for name, messages in form.errors.items()}
        cleaned = dict(form.cleaned_data)
        assert fallback_values(form, "fields") == expected
        assert {name: list(messages) for name, messages in form.errors.items()} == errors
        assert form.cleaned_data == cleaned

    def test_the_default_hook_hands_the_view_to_the_failure_log(self, monkeypatch):
        """A failing clean() is logged for the view's exposure policy (ADR-038), not for nobody."""
        import djust._exposure_diagnostics as diagnostics
        from djust._params_form import bind_params

        owners = []
        monkeypatch.setattr(
            diagnostics, "log_failure_for", lambda log, who, exc, *a, **k: owners.append(who)
        )

        class Crashing(CrashFields):
            def handle_params(self, params, uri):
                CALLS.append((dict(params), uri, {}))

        view = Crashing()
        form, values = bind_params(view, CrashesOnNoneParams, {"lo": "-1", "hi": "zzz"})
        assert values is None
        view.handle_invalid_params(form, "/")
        assert owners == [(view,)]
        assert CALLS[-1][0] == {"lo": 0, "hi": None}

    def test_params_errors_is_the_same_whether_or_not_the_recheck_rejects(self):
        for params in ({"lo": "9", "hi": "2"}, {"lo": "2", "hi": "zzz"}):
            _, _, view = self._bound(SpanParams, params)
            assert set(view.params_errors) == {"hi"}

    def test_a_clean_that_fails_on_the_merged_values_is_logged_without_the_exception_text(
        self, caplog
    ):
        import logging

        from djust._params_form import fallback_values

        form, _, _ = self._bound(CrashesOnNoneParams, {"lo": "-1", "hi": "zzz"})
        with caplog.at_level(logging.WARNING, logger="djust._params_form"):
            assert fallback_values(form, "fields", DefaultView()) == {"lo": 0, "hi": None}
        assert any("clean() failed on the merged fields" in r.getMessage() for r in caplog.records)

    def test_a_form_with_nothing_kept_does_not_run_clean_again(self):
        from djust._params_form import fallback_values

        calls = []

        class Counts(SpanParams):
            def clean(self):
                calls.append(1)
                return super().clean()

        form, _, _ = self._bound(Counts, {"lo": "x", "hi": "y"})
        before = len(calls)
        assert fallback_values(form, "fields") == {"lo": 0, "hi": 3}
        assert len(calls) == before

    def test_clean_runs_once_more_and_only_on_the_failing_path_in_fields_mode(self):
        from djust._params_form import fallback_values

        calls = []

        class Counts(SpanParams):
            def clean(self):
                calls.append(1)
                return super().clean()

        form, _, _ = self._bound(Counts, {"lo": "2", "hi": "zzz"})
        assert len(calls) == 1
        fallback_values(form, "initial")
        assert len(calls) == 1
        fallback_values(form, "fields")
        assert len(calls) == 2

    def test_a_callable_initial_is_evaluated_only_where_it_is_needed_and_at_most_once(self):
        from djust._params_form import fallback_values

        counts = {"supplied": 0, "omitted": 0, "replaced": 0}

        def counted(name):
            def initial():
                counts[name] += 1
                return 5

            return initial

        class Lazy(forms.Form):
            supplied = forms.IntegerField(required=False, initial=counted("supplied"))
            omitted = forms.IntegerField(required=False, initial=counted("omitted"))
            replaced = forms.IntegerField(required=False, initial=counted("replaced"))

        form, values, _ = self._bound(Lazy, {"supplied": "1", "replaced": "zzz"})
        assert values is None
        assert fallback_values(form, "fields") == {"supplied": 1, "omitted": 5, "replaced": 5}
        assert counts == {"supplied": 0, "omitted": 1, "replaced": 1}

    def test_a_callable_initial_is_evaluated_once_even_when_the_recheck_rejects(self):
        """The fall back to the plain initials reuses the value already evaluated for the merge."""
        from djust._params_form import fallback_values

        calls = {"hi": 0}

        def hi_initial():
            calls["hi"] += 1
            return 3

        class Counted(SpanParams):
            hi = forms.IntegerField(required=False, initial=hi_initial)

        form, values, _ = self._bound(Counted, {"lo": "9", "hi": "2"})
        assert values is None
        assert fallback_values(form, "fields") == {"lo": 0, "hi": 3}
        assert calls["hi"] == 1

    @pytest.mark.parametrize("form_class", [RepairParams, RepairReturnsParams])
    def test_what_clean_repairs_is_what_the_valid_path_hands_over(self, form_class):
        """In place or returned, the repair is read the way BaseForm._clean_form reads it."""
        from djust._params_form import fallback_values

        _, valid, _ = self._bound(form_class, {"lo": "9", "hi": "3"})
        assert valid == {"lo": 3, "hi": 3}
        form, values, _ = self._bound(form_class, {"lo": "9", "hi": "zzz"})
        assert values is None
        assert fallback_values(form, "fields") == valid

    def test_a_clean_that_copies_raw_data_into_a_kept_field_hands_it_over_as_documented(self):
        """The documented trust boundary: a failed field's OWN entry is its initial value, but a
        clean() that copies raw request data into a KEPT field the URL SUPPLIED hands it over
        through that field, exactly as on the valid path (an unsupplied one takes its initial value
        after clean() on both paths, #3891). ``"initial"`` is the mode that never hands over a raw
        value."""
        from djust._params_form import fallback_values

        class Copies(forms.Form):
            a = forms.CharField(required=False, initial="da")
            q = forms.CharField(max_length=5, required=False, initial="")

            def clean(self):
                cleaned = super().clean()
                if "q" in self.data:
                    cleaned["a"] = self.data["q"]  # raw request data, valid or not
                return cleaned

        raw = "<script>alert(1)</script>"
        _, valid, _ = self._bound(Copies, {"a": "x", "q": "ok"})
        assert valid == {"a": "ok", "q": "ok"}  # on the valid path clean()'s copy is handed over
        form, values, _ = self._bound(Copies, {"a": "x", "q": raw})
        assert values is None
        assert fallback_values(form, "fields") == {"a": raw, "q": ""}  # via the kept field
        assert fallback_values(form, "initial") == {"a": "da", "q": ""}  # never a raw value

    def test_the_issues_reproducer_the_fallback_hands_over_the_valid_paths_value_for_an_unsupplied_field(
        self,
    ):
        """#3891: ``a`` is not in the URL; the valid path gives its initial and so must the fallback."""
        from djust._params_form import fallback_values

        _, valid, _ = self._bound(CopiesParams, {"q": "ok"})
        assert valid == {"a": "da", "q": "ok"}
        form, values, _ = self._bound(CopiesParams, {"q": RAW})
        assert values is None
        assert fallback_values(form, "fields") == {"a": "da", "q": ""}
        assert fallback_values(form, "initial") == {"a": "da", "q": ""}

    def test_the_recheck_sees_the_filled_values_and_its_output_is_filled_again(self):
        """The design (#3891, reversed in review): the re-check is given the combination that would be
        handed over (the unsupplied kept field already filled with its initial), and the fill is then
        applied again to its OUTPUT, so a ``clean()`` write to that field cannot survive."""
        from djust._params_form import fallback_values

        seen: list = []

        class Records(forms.Form):
            a = forms.CharField(required=False, initial="da")
            q = forms.CharField(max_length=5, required=False)

            def clean(self):
                cleaned = super().clean()
                seen.append(dict(self.cleaned_data))
                if len(seen) > 1:
                    cleaned["a"] = "written-by-the-recheck"  # must not survive
                return cleaned

        _, valid, _ = self._bound(Records, {"q": "ok"})
        assert valid == {"a": "da", "q": "ok"}
        assert seen == [{"a": "", "q": "ok"}]  # the valid path's clean() sees the raw ""
        seen.clear()
        form, values, _ = self._bound(Records, {"q": "toolong!"})
        assert values is None
        assert seen == [{"a": ""}]  # the first pass: q failed and left cleaned_data
        seen.clear()
        assert fallback_values(form, "fields") == {"a": "da", "q": None}
        # the re-check: a already filled with its initial, q (replaced) paired with its initial
        assert seen == [{"a": "da", "q": None}]

    def test_the_recheck_is_given_what_the_first_pass_wrote_to_a_supplied_field(self):
        """A kept field the URL supplied is re-run on the first pass's OUTPUT, not on its input."""
        from djust._params_form import fallback_values

        seen: list = []

        class Writes(forms.Form):
            a = forms.CharField(required=False, initial="da")
            q = forms.CharField(max_length=5, required=False, initial="")

            def clean(self):
                cleaned = super().clean()
                seen.append(dict(self.cleaned_data))
                cleaned["a"] = "written-by-clean"
                return cleaned

        form, values, _ = self._bound(Writes, {"a": "x", "q": "toolong!"})
        assert values is None
        assert seen == [{"a": "x"}]  # the first pass's input
        seen.clear()
        assert fallback_values(form, "fields") == {"a": "written-by-clean", "q": ""}
        assert seen == [{"a": "written-by-clean", "q": ""}]  # the re-check's input is its output

    def test_a_default_start_next_to_a_supplied_end_that_clean_refuses_gives_the_plain_initials(
        self,
    ):
        """The guarantee: the result is a combination clean() accepts, or the plain initials."""
        from djust._params_form import fallback_values

        form, values, _ = self._bound(SpanDefaultParams, {"page": "abc", "end": "2"})
        assert values is None
        assert fallback_values(form, "fields") == {"page": 1, "start": 5, "end": None}
        accepted, values, _ = self._bound(SpanDefaultParams, {"page": "abc", "end": "9"})
        assert fallback_values(accepted, "fields") == {"page": 1, "start": 5, "end": 9}
        # and whatever it hands over, a fresh bind of that pair is accepted by clean() or it is the initials
        fresh = SpanDefaultParams(data={"start": "5", "end": "9"})
        assert fresh.is_valid()

    def test_a_clean_that_defaults_an_unsupplied_field_that_has_an_initial_loses_to_the_initial(
        self,
    ):
        """A behaviour change of #3891, now as on the valid path: the initial wins over clean()'s default."""
        from djust._params_form import fallback_values

        class Defaults(forms.Form):
            mode = forms.CharField(required=False)
            page = forms.IntegerField(min_value=1, required=False, initial=1)
            q = forms.CharField(required=False, initial="")

            def clean(self):
                cleaned = super().clean()
                if cleaned.get("mode") == "b" and not cleaned.get("q"):
                    cleaned["q"] = "dflt"
                return cleaned

        _, valid, _ = self._bound(Defaults, {"mode": "b"})
        assert valid["q"] == ""
        form, values, _ = self._bound(Defaults, {"mode": "b", "page": "abc"})
        assert values is None
        assert fallback_values(form, "fields") == {"mode": "b", "page": 1, "q": ""}

    def test_a_mutable_initial_the_fallback_fills_in_is_a_copy(self):
        """Appending to the handed-over value must not change the form class's default (#3921 review).

        ``Field.__deepcopy__`` copies the field but not its ``initial``, so handing over the stored
        object would let one request's ``handle_params()`` change every later request's default.
        """
        from djust._params_form import fallback_values

        class MI(forms.Form):
            j = forms.JSONField(required=False, initial=[])
            q = forms.CharField(max_length=5, required=False, initial="")

        form, values, _ = self._bound(MI, {"q": "toolong!"})
        assert values is None
        out = fallback_values(form, "fields")
        assert out["j"] == [] and out["q"] == ""
        out["j"].append("x")
        assert form.fields["j"].initial == []
        assert MI().fields["j"].initial == []
        assert MI.base_fields["j"].initial == []

    def test_a_clean_that_mutates_a_filled_initial_in_place_never_reaches_the_class(self):
        """The check runs on a copy of the filled combination: ``clean()`` may append to it."""
        from djust._params_form import fallback_values

        class Appends(forms.Form):
            j = forms.JSONField(required=False, initial=[])
            q = forms.CharField(max_length=5, required=False, initial="")

            def clean(self):
                cleaned = super().clean()
                if isinstance(cleaned.get("j"), list):
                    cleaned["j"].append("by-clean")
                return cleaned

        form, values, _ = self._bound(Appends, {"q": "toolong!"})
        assert values is None
        out = fallback_values(form, "fields")
        assert out["j"] == []  # the second fill: clean()'s write to a left-out field loses
        assert Appends.base_fields["j"].initial == []
        assert Appends().fields["j"].initial == []

    def test_an_initial_that_cannot_be_copied_is_handed_over_as_it_is(self):
        """The copy is a courtesy, never a new way to fail: an uncopyable initial is not copied."""
        from djust._params_form import _copied

        class Uncopyable:
            def __deepcopy__(self, memo):
                raise TypeError("no copies")

        item = Uncopyable()
        assert _copied(item) is item
        assert _copied([1]) == [1]

    def test_the_fill_judges_the_re_checks_output_not_the_first_passs(self):
        """The value handed over is the re-check's, so emptiness is judged on it (#3891).

        ``clean()`` derives ``a`` from a sibling: with ``q`` failed the first pass leaves ``a`` at
        the URL's ``"x"``, but the re-check pairs ``q`` with its initial ``""`` and blanks ``a``, so
        it takes its initial value (the fill reads the output it fills, not ``cleaned_data``).
        """
        from djust._params_form import fallback_values

        class Derives(forms.Form):
            a = forms.CharField(required=False, initial="da")
            q = forms.CharField(max_length=5, required=False, initial="")

            def clean(self):
                cleaned = super().clean()
                cleaned["a"] = "" if cleaned.get("q") == "" else self.data.get("a", "")
                return cleaned

        _, valid, _ = self._bound(Derives, {"a": "x", "q": "ok"})
        assert valid == {"a": "x", "q": "ok"}
        form, values, _ = self._bound(Derives, {"a": "x", "q": RAW})
        assert values is None
        assert form.cleaned_data["a"] == "x"  # the first pass left it non-empty
        assert fallback_values(form, "fields") == {"a": "da", "q": ""}

    @pytest.mark.parametrize(
        "form_class",
        [
            CopiesParams,
            CopiesNoInitialParams,
            CopiesCallableParams,
            CopiesManyParams,
            CopiesRequiredParams,
            CopiesModelParams,
            CopiesTagsParams,
        ],
    )
    def test_every_kept_field_is_what_the_valid_path_hands_over_property(self, form_class):
        """The differential (#3891): the valid path's own function is the oracle.

        For random binds that fail, every field the fallback KEPT must equal ``_cleaned_values`` on
        the same first-pass form (that is, what the valid path would hand ``handle_params``), and
        every field that failed must be its initial value, in both modes. Randomised over which
        fields the URL supplies, leaves out, empties or sends as a value that fails.
        """
        import random

        from djust._params_form import _cleaned_values, fallback_values, initial_values

        rng = random.Random(3891)
        invalid_binds = kept_unsupplied = 0
        for _ in range(250):
            params = {}
            for name in form_class.base_fields:
                kind = rng.choice(("absent", "empty", "ok", "raw"))
                if kind == "empty":
                    params[name] = ""
                elif kind == "ok":
                    params[name] = rng.choice(("ok", "x"))
                elif kind == "raw":
                    params[name] = RAW + str(rng.randint(0, 9))
            form, values, _ = self._bound(form_class, params)
            if values is not None:
                continue
            invalid_binds += 1
            fields_result = fallback_values(form, "fields")
            initial = initial_values(form)
            assert fallback_values(form, "initial") == initial
            if form.non_field_errors():
                assert fields_result == initial
                continue
            valid_path = _cleaned_values(form)
            for name in form.fields:
                if name in form.errors:
                    assert fields_result[name] == initial[name], (params, name)
                elif name in form.cleaned_data:
                    assert fields_result[name] == valid_path[name], (params, name)
                    kept_unsupplied += name not in params or params[name] == ""
        assert invalid_binds > 50 and kept_unsupplied > 20  # exercised, not vacuous

    def test_the_initial_mode_is_unaffected_and_never_runs_clean_again(self):
        from djust._params_form import fallback_values, initial_values

        calls = []

        class Counts(CopiesParams):
            def clean(self):
                calls.append(1)
                return super().clean()

        for params in ({"q": RAW}, {"a": "x", "q": RAW}, {"a": "", "q": RAW}):
            calls.clear()
            form, values, _ = self._bound(Counts, params)
            assert values is None
            assert len(calls) == 1
            assert fallback_values(form, "initial") == initial_values(form) == {"a": "da", "q": ""}
            assert len(calls) == 1  # "initial" never re-checks

    def test_a_callable_initial_over_clean_is_evaluated_once_and_only_when_needed(self):
        from djust._params_form import fallback_values

        count = {"a": 0}

        def a_initial():
            count["a"] += 1
            return "dyn"

        class Counted(CopiesParams):
            a = forms.CharField(required=False, initial=a_initial)

        form, values, _ = self._bound(Counted, {"q": RAW})
        assert values is None
        assert fallback_values(form, "fields") == {"a": "dyn", "q": ""}
        assert count["a"] == 1
        count["a"] = 0
        form, values, _ = self._bound(Counted, {"a": "x", "q": RAW})  # supplied: never evaluated
        assert fallback_values(form, "fields") == {"a": RAW, "q": ""}
        assert count["a"] == 0

    def test_a_clean_that_raises_on_the_recheck_gives_the_plain_initials(self):
        """A clean() that accepts the first pass and refuses the second keeps nothing."""
        from djust._params_form import fallback_values

        calls = []

        class RefusesTheSecondCall(CopiesParams):
            def clean(self):
                calls.append(1)
                cleaned = super().clean()
                if len(calls) > 1:
                    raise forms.ValidationError("refused")
                return cleaned

        form, values, _ = self._bound(RefusesTheSecondCall, {"q": RAW})
        assert values is None
        result = fallback_values(form, "fields")
        assert result == {"a": "da", "q": ""}
        assert RAW not in repr(result)
        assert len(calls) == 2

    def test_a_repair_of_a_replaced_field_is_not_adopted(self):
        """clean() adopted for kept fields only: a write to the failed ``hi`` is its initial still."""
        from djust._params_form import fallback_values

        _, valid, _ = self._bound(RepairHiParams, {"lo": "9", "hi": "3"})
        assert valid == {"lo": 9, "hi": 9}
        form, values, _ = self._bound(RepairHiParams, {"lo": "9", "hi": "zzz"})
        assert values is None
        assert fallback_values(form, "fields") == {"lo": 9, "hi": 3}

    def test_a_clean_that_mutates_a_list_in_place_never_touches_the_held_form(self):
        """Deep-copied: the form the caller holds keeps what the first pass made.

        The result carries the second pass's own append: ``clean()`` runs on what it already
        cleaned once (documented: it must be idempotent). Without the copy the SAME list would
        be the held form's, the merged dict's and the result's.
        """
        from djust._params_form import fallback_values

        class Tags(forms.Form):
            tags = forms.MultipleChoiceField(
                choices=[("a", "A"), ("b", "B")], required=False, initial=["a"]
            )
            n = forms.IntegerField(required=False, initial=1)

            def clean(self):
                cleaned = super().clean()
                if "tags" in cleaned:
                    cleaned["tags"].append("X")
                return cleaned

        _, valid, _ = self._bound(Tags, {"tags": ["b"], "n": "1"})
        assert valid["tags"] == ["b", "X"]
        form, values, _ = self._bound(Tags, {"tags": ["b"], "n": "zzz"})
        assert values is None
        held = form.cleaned_data["tags"]
        assert held == ["b", "X"]  # the first pass's own append
        result = fallback_values(form, "fields")
        assert result["tags"] == ["b", "X", "X"]  # clean() ran again on its own output
        assert result["tags"] is not held
        assert held == ["b", "X"]  # the held form's list was not mutated

    def test_values_that_cannot_be_copied_give_the_plain_initials_and_a_warning(self, caplog):
        import logging

        from djust._params_form import fallback_values

        class Uncopyable:
            def __deepcopy__(self, memo):
                raise TypeError("no copies")

        class Odd(forms.Field):
            def to_python(self, value):
                return Uncopyable() if value else None

        class OddForm(forms.Form):
            odd = Odd(required=False)
            n = forms.IntegerField(required=False, initial=1)

        form, values, _ = self._bound(OddForm, {"odd": "x", "n": "zzz"})
        assert values is None
        with caplog.at_level(logging.WARNING, logger="djust._params_form"):
            result = fallback_values(form, "fields", DefaultView())
        assert result == {"odd": None, "n": 1}
        assert any("failed on the merged fields" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("exc", ["PermissionDenied", "Http404"])
    def test_an_exception_raised_only_on_the_second_call_gives_the_plain_initials(
        self, exc, caplog
    ):
        """Documented: swallowed with a warning (the first pass would have raised it)."""
        import logging

        from django.core.exceptions import PermissionDenied
        from django.http import Http404

        from djust._params_form import fallback_values

        raised = {"PermissionDenied": PermissionDenied, "Http404": Http404}[exc]
        calls = []

        class Second(SpanParams):
            def clean(self):
                calls.append(1)
                if len(calls) > 1:
                    raise raised("only the second time")
                return super().clean()

        form, values, _ = self._bound(Second, {"lo": "2", "hi": "zzz"})
        assert values is None
        with caplog.at_level(logging.WARNING, logger="djust._params_form"):
            assert fallback_values(form, "fields", DefaultView()) == {"lo": 0, "hi": 3}
        assert any("failed on the merged fields" in r.getMessage() for r in caplog.records)

    def test_a_callable_initial_on_a_kept_field_is_evaluated_once_when_the_recheck_rejects(self):
        """N15: kept-unsupplied field's initial is evaluated for the merge and reused for the fallback."""
        from djust._params_form import fallback_values

        calls = {"q": 0}

        def q_initial():
            calls["q"] += 1
            return "dq"

        class Counted(SpanParams):
            q = forms.CharField(required=False, initial=q_initial)

        form, values, _ = self._bound(Counted, {"lo": "9", "hi": "2"})
        assert values is None
        assert fallback_values(form, "fields") == {"lo": 0, "hi": 3, "q": "dq"}
        assert calls["q"] == 1

    def test_a_non_idempotent_clean_gives_a_different_value_than_the_valid_path(self):
        """The documented contract: clean() is re-run on its own output, so it must be idempotent."""
        from djust._params_form import fallback_values

        _, valid, _ = self._bound(PriceParams, {"price": "5", "page": "1"})
        assert valid["price"] == 500
        form, values, _ = self._bound(PriceParams, {"price": "5", "page": "zz"})
        assert values is None
        assert fallback_values(form, "fields")["price"] == 50000

    def test_a_clean_that_returns_an_empty_dict_leaves_no_field_so_all_take_their_initial(self):
        """BaseForm._clean_form adopts a returned ``{}`` (it is not None); so does the re-check."""
        from djust._params_form import fallback_values

        class EmptiesOnSecond(forms.Form):
            lo = forms.IntegerField(required=False, initial=0)
            hi = forms.IntegerField(required=False, initial=3)
            n = forms.IntegerField(required=False, initial=1)
            calls = 0

            def clean(self):
                type(self).calls += 1
                cleaned = super().clean()
                return {} if type(self).calls > 1 else cleaned

        EmptiesOnSecond.calls = 0
        form, values, _ = self._bound(EmptiesOnSecond, {"lo": "2", "hi": "5", "n": "zzz"})
        assert values is None
        assert fallback_values(form, "fields") == {"lo": 0, "hi": 3, "n": 1}

    def test_an_ordinary_rejection_logs_nothing_and_a_crash_logs_a_warning(self, caplog):
        import logging

        from djust._params_form import fallback_values

        form, _, _ = self._bound(SpanParams, {"lo": "9", "hi": "2"})  # add_error rejection
        raising, _, _ = self._bound(SpanRaisesParams, {"lo": "9", "hi": "zzz"})  # ValidationError
        with caplog.at_level(logging.DEBUG, logger="djust._params_form"):
            assert fallback_values(form, "fields", DefaultView()) == {"lo": 0, "hi": 3}
            assert fallback_values(raising, "fields", DefaultView()) == {"lo": 0, "hi": 3}
        assert [r for r in caplog.records if r.name == "djust._params_form"] == []

        crashing, _, _ = self._bound(CrashesOnNoneParams, {"lo": "-1", "hi": "zzz"})
        with caplog.at_level(logging.DEBUG, logger="djust._params_form"):
            fallback_values(crashing, "fields", DefaultView())
        (record,) = [r for r in caplog.records if r.name == "djust._params_form"]
        assert record.levelname == "WARNING"

    def test_a_failed_field_is_its_initial_value_whatever_clean_wrote(self):
        """clean()'s output is adopted for KEPT fields only (a clean() that re-reads self.data)."""
        from djust._params_form import fallback_values

        form, values, _ = self._bound(RereadParams, {"q": "<script>alert(1)</script>", "page": "5"})
        assert values is None
        assert fallback_values(form, "fields") == {"q": "", "page": "5"}
        assert fallback_values(form, "initial") == {"q": "", "page": 1}

    def test_a_failed_fields_raw_value_never_reaches_handle_params_property(self):
        """Random invalid binds over forms whose clean() normalises or re-reads the raw data."""
        import random

        from djust._params_form import fallback_values

        class Normalises(forms.Form):
            q = forms.CharField(max_length=4, required=False, initial="dq")
            sort = forms.ChoiceField(choices=SORTS, required=False, initial="date")
            page = forms.IntegerField(min_value=1, max_value=9, required=False, initial=1)

            def clean(self):
                cleaned = super().clean()
                for name in ("q", "sort", "page"):
                    raw = self.data.get(name)
                    if raw is not None:
                        cleaned[name] = raw.strip().lower()  # normalises the raw, valid or not
                return cleaned

        forms_under_test = (Normalises, RereadParams, SpanParams)
        rng = random.Random(3874)
        marker = "RAWMARKER"
        invalid_binds = 0
        for i in range(600):
            form_class = rng.choice(forms_under_test)
            params = {}
            for name in form_class.base_fields:
                kind = rng.choice(("valid", "bad", "absent", "empty"))
                if kind == "valid":
                    params[name] = rng.choice(("1", "2", "name", "a"))
                elif kind == "bad":
                    params[name] = "%s%d <script>" % (marker, i)
                elif kind == "empty":
                    params[name] = ""
            form, values, _ = self._bound(form_class, params)
            if values is not None:
                continue
            invalid_binds += 1
            initial = {
                name: form.get_initial_for_field(field, name) for name, field in form.fields.items()
            }
            for mode in ("fields", "initial"):
                result = fallback_values(form, mode)
                assert list(result) == list(form.fields)
                for name in form.errors:
                    if name in form.fields:
                        assert result[name] == initial[name], (form_class, params, mode, name)
                # every `bad` value fails its field, so the marker may not appear at all
                assert marker.lower() not in repr(result).lower(), (form_class, params, mode)
        assert invalid_binds > 100  # the property was exercised, not vacuous

    def test_the_valid_path_is_unchanged(self):
        form, values, view = self._bound(DeletesParams, {"page": "2", "sort": "name"})
        assert values == {"page": 2, "sort": "name", "q": ""}
        assert view.params_errors == {}

    def test_the_messages_are_the_same_in_either_mode(self):
        """``params_errors`` is published before the hook and does not depend on the fallback."""
        _, _, view = self._bound(FilterParams, {"page": "abc", "sort": "bogus"})
        assert set(view.params_errors) == {"page", "sort"}


# --------------------------------------------------------------------------- #
# V023 reports a mode the request path would refuse
# --------------------------------------------------------------------------- #


class CheckGood(LiveView):
    template = "<div dj-root></div>"
    params_form = FilterParams
    params_form_fallback = "fields"


class CheckBad(LiveView):
    template = "<div dj-root></div>"
    params_form = FilterParams
    params_form_fallback = "field"


class CheckBadInherited(CheckBad):
    pass


class CheckBadNotAString(LiveView):
    template = "<div dj-root></div>"
    params_form = FilterParams
    params_form_fallback = ["fields"]


class CheckNoForm(LiveView):
    """No ``params_form``: the mode is never read, so there is nothing to report."""

    template = "<div dj-root></div>"
    params_form_fallback = "nonsense"


class CheckBothBroken(LiveView):
    template = "<div dj-root></div>"
    params_form = "myapp.forms.Filter"
    params_form_fallback = "nonsense"


def _fallback_messages(*classes):
    from djust.checks.components import check_params_form_declarations

    labels = ["%s.%s" % (c.__module__, c.__qualname__) for c in classes]
    return [
        m
        for m in check_params_form_declarations(None)
        if any(m.msg.startswith(label + ".params_form_fallback ") for label in labels)
    ]


class TestV023Fallback:
    def test_a_known_mode_is_silent(self):
        assert _fallback_messages(CheckGood, DefaultView, FieldsView) == []

    @pytest.mark.parametrize("view", [CheckBad, CheckBadNotAString])
    def test_an_unknown_mode_is_an_error(self, view):
        (message,) = _fallback_messages(view)
        assert message.id == "djust.V023"
        assert message.level == 40
        assert "must be one of 'initial', 'fields'" in message.msg
        assert "ImproperlyConfigured" in message.hint
        assert message.fix_hint == "Fix the `params_form_fallback` declaration of `%s`." % (
            view.__qualname__
        )

    def test_the_message_names_what_was_declared(self):
        (message,) = _fallback_messages(CheckBad)
        assert "'field'" in message.msg

    def test_an_inherited_mode_is_reported_for_the_subclass(self):
        assert len(_fallback_messages(CheckBadInherited)) == 1

    def test_a_view_without_a_params_form_is_not_reported(self):
        assert _fallback_messages(CheckNoForm) == []

    def test_both_problems_are_reported_when_both_are_broken(self):
        from djust.checks.components import check_params_form_declarations

        label = "%s.%s" % (CheckBothBroken.__module__, CheckBothBroken.__qualname__)
        mine = [m for m in check_params_form_declarations(None) if m.msg.startswith(label + ".")]
        assert sorted(m.msg.split(" ")[0].rsplit(".", 1)[1] for m in mine) == [
            "params_form",
            "params_form_fallback",
        ]

    def test_it_can_be_suppressed(self, settings):
        settings.DJUST_CONFIG = {"suppress_checks": ["V023"]}
        assert _fallback_messages(CheckBad) == []

    @pytest.mark.parametrize("declared", ["fields", "initial", "field", "", None, ["fields"], 3])
    def test_the_check_judges_a_mode_as_the_runtime_does(self, declared):
        from djust._params_form import fallback_problem, run_handle_params_sync

        class Probe(LiveView):
            template = "<div dj-root></div>"
            params_form = FilterParams
            params_form_fallback = declared

            def handle_params(self, params, uri):
                pass

        if fallback_problem(declared) is None:
            run_handle_params_sync(Probe(), {}, "/")
        else:
            with pytest.raises(ImproperlyConfigured, match="params_form_fallback"):
                run_handle_params_sync(Probe(), {}, "/")


# --------------------------------------------------------------------------- #
# An async override that calls super(): the second clean() runs on the event loop
# --------------------------------------------------------------------------- #

_ORM_WARNING = "used the ORM on the event loop"


@pytest.mark.django_db(transaction=True)
class TestAsyncOverrideRunsTheRecheckOnTheLoop:
    """The documented limit: a clean() that uses the ORM falls back to the plain initials there."""

    QUERY = {"a": "5", "b": "zz"}

    async def test_a_sync_override_keeps_the_valid_field_over_the_socket(self):
        communicator, frame = await _connect("OrmSync", self.QUERY)
        try:
            assert frame["type"] == "mount", frame
            assert CALLS[-1][0] == {"a": 5, "b": 2}
        finally:
            await communicator.disconnect()

    async def test_an_async_override_gets_the_plain_initials_and_a_warning_over_the_socket(
        self, caplog
    ):
        with caplog.at_level("WARNING", logger="djust._params_form"):
            communicator, frame = await _connect("OrmAsync", self.QUERY)
        try:
            assert frame["type"] == "mount", frame
            assert CALLS[-1][0] == {"a": 1, "b": 2}
            assert any(_ORM_WARNING in r.getMessage() for r in caplog.records)
        finally:
            await communicator.disconnect()

    async def test_a_sync_override_keeps_the_valid_field_over_http(self):
        response = await sync_to_async(Client().get)(_url("OrmSync", self.QUERY))
        assert response.status_code == 200
        assert CALLS[-1][0] == {"a": 5, "b": 2}

    async def test_an_async_override_gets_the_plain_initials_and_a_warning_over_http(self, caplog):
        with caplog.at_level("WARNING", logger="djust._params_form"):
            response = await sync_to_async(Client().get)(_url("OrmAsync", self.QUERY))
        assert response.status_code == 200
        assert CALLS[-1][0] == {"a": 1, "b": 2}
        assert any(_ORM_WARNING in r.getMessage() for r in caplog.records)
