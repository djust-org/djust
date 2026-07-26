"""``validate_field`` must accept the key the client actually sends (#2137).

The client sends the field name under ``field`` — ``09-event-binding.js`` at
all three of its send sites, and ``20-model-binding.js`` for ``dj-model`` —
because djust maps ``data-*`` attributes to handler parameters.
``FormMixin.validate_field`` took ``field_name`` exclusively.

The `**kwargs` in the signature is what made it silent: the client's payload
matched no named parameter, `**kwargs` absorbed it instead of raising
`TypeError`, and the handler ran on every keystroke doing nothing. No error,
no warning — the form looked live and was inert. Reported against 1.1.0rc8.

`WizardMixin.validate_field` already had the coalesce, with a docstring
explaining the wire contract. Two other implementations never got it, which is
the #1646 shape: three implementations of one contract, one correct. The fix
lifts the working one (#1077) rather than inventing a third spelling.

These tests assert what the CALLER observes — the field was validated — rather
than which parameter carried the name, so they survive a future rename.
"""

from __future__ import annotations

from django import forms as djforms

from djust.forms import FormMixin


class _SignupForm(djforms.Form):
    email = djforms.EmailField()
    name = djforms.CharField(max_length=10)


class _View(FormMixin):
    form_class = _SignupForm

    def __init__(self):
        self.form_data = {}
        self.field_errors = {}
        self._model_instance = None


# --- the bug -------------------------------------------------------------


def test_the_key_the_client_sends_validates():
    # Exactly the payload 09-event-binding.js builds: {value, field}.
    v = _View()

    v.validate_field(field="email", value="not-an-email")

    assert v.form_data == {"email": "not-an-email"}, "the value must be stored"
    assert "email" in v.field_errors, "an invalid value must produce an error"


def test_the_legacy_key_still_validates():
    # Anyone who wrote against the documented signature keeps working.
    v = _View()

    v.validate_field(field_name="email", value="not-an-email")

    assert v.form_data == {"email": "not-an-email"}
    assert "email" in v.field_errors


def test_both_keys_produce_identical_state():
    # The point of the coalesce: which spelling arrived must not be observable.
    a, b = _View(), _View()

    a.validate_field(field="name", value="x" * 50)
    b.validate_field(field_name="name", value="x" * 50)

    assert a.form_data == b.form_data
    assert a.field_errors == b.field_errors


def test_a_valid_value_clears_the_error_via_the_client_key():
    v = _View()
    v.validate_field(field="email", value="nope")
    assert "email" in v.field_errors

    v.validate_field(field="email", value="user@example.com")

    assert "email" not in v.field_errors, "a valid value must clear the error"


def test_an_empty_field_name_is_still_a_no_op():
    # The guard that made the bug invisible must keep working for its real
    # purpose — an event with no field name at all.
    v = _View()

    v.validate_field(field="", value="whatever")
    v.validate_field(field_name="", value="whatever")
    v.validate_field(value="whatever")

    assert v.form_data == {}
    assert v.field_errors == {}


def test_field_wins_when_both_are_supplied():
    # `field` is the wire contract; `field_name` is the compatibility alias.
    # WizardMixin resolves it the same way (`name = field or field_name`).
    v = _View()

    v.validate_field(field="email", field_name="name", value="not-an-email")

    assert "email" in v.form_data
    assert "name" not in v.form_data


# --- the drift itself ----------------------------------------------------


def test_every_validate_field_accepts_the_client_key():
    # Structural pin. Three implementations of one wire contract existed and
    # only WizardMixin's was right; a fourth would drift the same way. Every
    # `validate_field` in the tree must accept `field`.
    import inspect

    from djust.admin_ext.forms import AdminFormMixin
    from djust.wizard import WizardMixin

    for owner in (FormMixin, AdminFormMixin, WizardMixin):
        sig = inspect.signature(owner.validate_field)
        assert "field" in sig.parameters, (
            f"{owner.__name__}.validate_field must accept `field` — that is the "
            f"key the client sends; got {list(sig.parameters)}"
        )


def test_admin_form_mixin_validates_via_the_client_key():
    # AdminFormMixin had ZERO behavioural coverage anywhere in the repo — its
    # only pin was a source-grep for the literal `field or field_name`, which
    # is decorative in both directions (#1859): sabotaging the method while
    # leaving the string in place passed, and a behaviour-preserving refactor
    # to a ternary failed. It also would have blocked the obvious future
    # improvement — extracting the coalesce into one shared helper, which is
    # the #1646 cure this fix's own rationale praises.
    from djust.admin_ext.forms import AdminFormMixin

    class _AdminView(AdminFormMixin):
        form_class = _SignupForm

        def __init__(self):
            self.form_data = {}
            self.field_errors = {}
            self._model_instance = None

        def _create_form(self, data=None):
            return _SignupForm(data) if data else _SignupForm()

    v = _AdminView()
    v.validate_field(field="email", value="not-an-email")

    assert v.form_data == {"email": "not-an-email"}
    assert "email" in v.field_errors


def test_admin_form_mixin_still_accepts_the_legacy_key():
    from djust.admin_ext.forms import AdminFormMixin

    class _AdminView(AdminFormMixin):
        form_class = _SignupForm

        def __init__(self):
            self.form_data = {}
            self.field_errors = {}
            self._model_instance = None

        def _create_form(self, data=None):
            return _SignupForm(data) if data else _SignupForm()

    v = _AdminView()
    v.validate_field(field_name="email", value="user@example.com")

    assert v.form_data == {"email": "user@example.com"}
    assert "email" not in v.field_errors


def test_the_admin_adapter_positional_shape_keeps_the_real_value():
    # `admin_ext/adapters.py` emits `validate_field('<name>', value)` at 8
    # sites; the bare `value` token arrives as the literal string "value" in
    # positional slot 1, with the REAL value as a keyword. `field` being first
    # is what makes the junk land in `field_name` instead of overwriting the
    # value — which is what it did before this change, so the admin validated
    # the string "value" on every keystroke.
    v = _View()

    v.validate_field("email", "value", value="user@example.com")

    assert v.form_data == {"email": "user@example.com"}, (
        "the real value must survive the adapter's junk positional token"
    )
    assert "email" not in v.field_errors


def test_two_arg_positional_binds_field_name_not_value():
    # The cost of that ordering, pinned so it is a known property rather than
    # a surprise: `validate_field("email", "text")` binds field_name="text"
    # and leaves value=None, so a filled field reports "required". No in-repo
    # or in-docs caller uses this shape; the docstring says to use keywords.
    v = _View()

    v.validate_field("email", "user@example.com")

    assert v.form_data == {"email": None}
    assert "email" in v.field_errors, (
        "documents the trap: the second positional is field_name, not value"
    )
