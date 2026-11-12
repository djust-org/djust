"""Unsafe code the safe-markup guard does NOT flag, probed by the review of #3848 and pinned.

Each case defines ``f(*args)`` and emits the payload ``<x>`` raw when RUN; the guard reports nothing for
it. ``test_safe_markup_guard_3848.py::TestKnownMisses`` fails the day the guard flags one: move it to
``safe_markup_corpus_3848.CASES``. The review pinned 44; 32 are caught since the follow-up of #3850
and live in the corpus now. Every case left here says WHY it is still a miss (``REASONS``), and most of
them are not a hole in the guard's model: the payload is a LITERAL the developer wrote (markup the
developer wrote is trusted by design), and the same code with data from anywhere else is flagged
(the ``*_data`` twins in the corpus pin that).
"""

CASES = {
    "F20_markupsafe_style_Markup": (
        'class Markup(str):\n    pass\ndef f(raw):\n    return Markup(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F22_exec_sink": (
        "def f(raw):\n"
        '    ns = {"mark_safe": mark_safe, "raw": raw}\n'
        "    exec(\"out = mark_safe(f'<b>{raw}</b>')\", ns)\n"
        '    return ns["out"]\n',
        ("<x>",),
    ),
    "F23_sink_in_default_arg": (
        'GLOBAL_RAW = "<x>"\ndef f(raw, out=mark_safe(f"<b>{GLOBAL_RAW}</b>")):\n    return out\n',
        ("<x>",),
    ),
    "F24_sink_in_decorator_arg": (
        'GLOBAL_RAW = "<x>"\n'
        "def deco(v):\n"
        "    def d(fn):\n"
        "        fn.out = v\n"
        "        return fn\n"
        "    return d\n"
        '@deco(mark_safe(f"<b>{GLOBAL_RAW}</b>"))\n'
        "def f(raw):\n"
        "    return f.out\n",
        ("<x>",),
    ),
    "F25_sink_in_class_bases_kw": (
        'GLOBAL_RAW = "<x>"\n'
        "class K:\n"
        '    out = mark_safe(f"<b>{GLOBAL_RAW}</b>")\n'
        "def f(raw):\n"
        "    return K.out\n",
        ("<x>",),
    ),
    "F27_conditional_escape_of_safe": (
        "def f(raw):\n    s = mark_safe(raw)\n    return conditional_escape(s)\n",
        ("<x>",),
    ),
    "I01_class_scope_name_seen_by_method": (
        'LABEL = "<x>"\n'
        "class C:\n"
        '    LABEL = "ok"\n'
        "    def r(self, raw):\n"
        '        return mark_safe(f"<b>{LABEL}</b>")\n'
        "def f(raw):\n"
        "    return C().r(raw)\n",
        ("<x>",),
    ),
    "I02_class_scope_name_assigned_from_raw_in_module": (
        "def get():\n"
        '    return "<x>"\n'
        "LABEL = get()\n"
        "class C:\n"
        '    LABEL = "ok"\n'
        "    def r(self):\n"
        "        return "
        'mark_safe(f"<b>{LABEL}</b>")\n'
        "def f(raw):\n"
        "    return C().r()\n",
        ("<x>",),
    ),
    "I07_module_level_sink": (
        'RAW = "<x>"\nOUT = mark_safe(f"<b>{RAW}</b>")\ndef f(raw):\n    return OUT\n',
        ("<x>",),
    ),
    "I08_module_level_loop": (
        'OUT = ""\n'
        'for RAW in ["<x>"]:\n'
        '    OUT = mark_safe(f"<b>{RAW}</b>")\n'
        "def f(raw):\n"
        "    return OUT\n",
        ("<x>",),
    ),
    "I09_class_body_loop": (
        "class K:\n"
        '    OUT = ""\n'
        '    for RAW in ["<x>"]:\n'
        '        OUT = mark_safe(f"<b>{RAW}</b>")\n'
        "def f(raw):\n"
        "    return K.OUT\n",
        ("<x>",),
    ),
    "I12_global_name_assigned_in_module_loop": (
        'for v in ["<x>"]:\n    pass\ndef f(raw):\n    return mark_safe(f"<b>{v}</b>")\n',
        ("<x>",),
    ),
}

_LITERAL = (
    "the payload is a string LITERAL the developer wrote; the guard trusts literal markup by design "
    "(it is not request data). The same code with a non-literal value is flagged: see {twin} in "
    "safe_markup_corpus_3848"
)

#: Why each case is still a miss. ``test_every_known_miss_says_why`` holds the two in step.
REASONS = {
    "F20_markupsafe_style_Markup": (
        "a ``str`` subclass without ``__html__`` is not a safe marker for Django (it is escaped when "
        "rendered); the case only emits ``<x>`` because the oracle calls ``str()``"
    ),
    "F22_exec_sink": (
        "the sink is inside a string handed to ``exec``: the guard reads the syntax tree, not code held "
        "in strings (``exec`` does turn off trust in the module's globals)"
    ),
    "F23_sink_in_default_arg": _LITERAL.format(twin="F23_sink_in_default_arg_data"),
    "F24_sink_in_decorator_arg": _LITERAL.format(twin="F24_sink_in_decorator_arg_data"),
    "F25_sink_in_class_bases_kw": _LITERAL.format(twin="F25_sink_in_class_body_data"),
    "F27_conditional_escape_of_safe": (
        "``mark_safe(raw)`` of a bare parameter is how trusted markup is passed in; no syntax says "
        "whether the caller escaped it (the module docstring's first limit)"
    ),
    "I01_class_scope_name_seen_by_method": _LITERAL.format(
        twin="I01_module_name_seen_by_method_data"
    ),
    "I02_class_scope_name_assigned_from_raw_in_module": (
        "``get()`` is a helper of the same file that returns a LITERAL, so ``LABEL`` is developer "
        "markup by design; " + _LITERAL.format(twin="I01_module_name_seen_by_method_data")
    ),
    "I07_module_level_sink": _LITERAL.format(twin="I07_module_level_sink_data"),
    "I08_module_level_loop": _LITERAL.format(twin="I08_module_level_loop_data"),
    "I09_class_body_loop": _LITERAL.format(twin="I09_class_body_loop_data"),
    "I12_global_name_assigned_in_module_loop": _LITERAL.format(
        twin="I12_global_name_assigned_in_module_loop_data"
    ),
}
