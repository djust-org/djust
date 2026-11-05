"""Unsafe code the safe-markup guard does NOT flag, probed by the review of #3848 and pinned.

Each case defines ``f(*args)`` and emits the payload ``<x>`` raw when RUN; the guard reports nothing for
it. They are known false negatives, listed in one issue (see the module docstring of
``scripts/lib/safe_markup_taint.py``). ``test_safe_markup_guard_3848.py::TestKnownMisses`` fails the day
the guard flags one: move it to ``safe_markup_corpus_3848.CASES`` and delete the audit entries it
changes. Generated from the review's corpus.
"""

CASES = {
    "B18_closure_append": (
        "def f(raw):\n"
        '    parts = ["<div>"]\n'
        "    def add(s):\n"
        "        parts.append(s)\n"
        '    add(f"<b>{raw}</b>")\n'
        '    parts.append("</div>")\n'
        '    return mark_safe("".join(parts))\n',
        ("<x>",),
    ),
    "B19_closure_append_raw_direct": (
        "def f(raw):\n"
        '    parts = ["<div>"]\n'
        "    def add():\n"
        "        parts.append(raw)\n"
        "    add()\n"
        '    return mark_safe("".join(parts))\n',
        ("<x>",),
    ),
    "B20_dict_keys_iter_assign": (
        "def f(raw):\n"
        "    d = {}\n"
        "    d[raw] = 1\n"
        '    return mark_safe("".join(f"<li>{k}</li>" for k in d))\n',
        ("<x>",),
    ),
    "B21_dict_keys_literal": (
        "def f(raw):\n"
        "    d = {raw: 1}\n"
        '    return mark_safe("".join(f"<li>{k}</li>" for k in d))\n',
        ("<x>",),
    ),
    "B22_dict_items_key": (
        "def f(raw):\n"
        "    d = {}\n"
        '    d[raw] = "x"\n'
        '    out = ""\n'
        "    for k, v in d.items():\n"
        '        out += f"<li>{k}</li>"\n'
        "    return mark_safe(out)\n",
        ("<x>",),
    ),
    "B23_dict_keys_sorted": (
        "def f(raw):\n"
        '    d = {raw: "x"}\n'
        '    return mark_safe("".join(f"<li>{k}</li>" for k in sorted(d)))\n',
        ("<x>",),
    ),
    "B24_dictcomp_keys": (
        "def f(raw):\n"
        '    d = {k: "x" for k in [raw]}\n'
        '    return mark_safe("".join(f"<li>{k}</li>" for k in d))\n',
        ("<x>",),
    ),
    "B25_counter_idiom": (
        "def f(raw):\n"
        "    counts = {}\n"
        "    counts[raw] = counts.get(raw, 0) + 1\n"
        '    return mark_safe("".join(f"<li>{k}:{v}</li>" for k, v in '
        "counts.items()))\n",
        ("<x>",),
    ),
    "B30_nested_list": (
        "def f(raw):\n"
        '    rows = [["<i>"]]\n'
        "    rows[0].append(raw)\n"
        '    return mark_safe("".join(rows[0]))\n',
        ("<x>",),
    ),
    "B31_dict_of_lists": (
        "def f(raw):\n"
        '    d = {"a": ["<i>"]}\n'
        '    d["a"].append(raw)\n'
        '    return mark_safe("".join(d["a"]))\n',
        ("<x>",),
    ),
    "B32_dict_val_list_setdefault": (
        "def f(raw):\n"
        "    d = {}\n"
        '    d.setdefault("a", []).append(raw)\n'
        '    return mark_safe("".join(d["a"]))\n',
        ("<x>",),
    ),
    "C26_star_arg": ('def f(raw):\n    return mark_safe(*[f"<b>{raw}</b>"])\n', ("<x>",)),
    "C32_format_html_join": (
        "from django.utils.html import format_html_join\n"
        "def f(raw):\n"
        '    return format_html_join(",", f"<b>{raw}</b>{{}}", [("a",)])\n',
        ("<x>",),
    ),
    "C36_await_wrapped": (
        "import asyncio\n"
        "async def ident(x):\n"
        "    return x\n"
        "async def _run(raw):\n"
        '    return mark_safe(await ident(f"<b>{raw}</b>"))\n'
        "def f(raw):\n"
        "    return asyncio.run(_run(raw))\n",
        ("<x>",),
    ),
    "C47_translate": (
        'def f(raw):\n    return mark_safe("<b>x</b>".translate({ord("x"): raw}))\n',
        ("<x>",),
    ),
    "C48_percent_mult": (
        'def f(raw, n=2):\n    return mark_safe(("<li>%s</li>" % raw) * n)\n',
        ("<x>",),
    ),
    "D58_html_escape_quote_false": (
        "import html\n"
        "def f(raw):\n"
        '    return mark_safe(f"<b>{html.escape(raw, quote=False)}</b>" + '
        '"<x>")\n',
        ("<x>",),
    ),
    "F02_self_alias": (
        "class C:\n"
        "    def __init__(self):\n"
        "        self._ms = mark_safe\n"
        "    def r(self, raw):\n"
        '        return self._ms(f"<b>{raw}</b>")\n'
        "def f(raw):\n"
        "    return C().r(raw)\n",
        ("<x>",),
    ),
    "F07_class_call": (
        "class Safe:\n"
        "    def __call__(self, v):\n"
        "        return mark_safe(v)\n"
        "_wrap = Safe()\n"
        "def f(raw):\n"
        '    return _wrap(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F08_ifexp_alias": (
        '_wrap = mark_safe if True else str\ndef f(raw):\n    return _wrap(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F09_dict_dispatch": (
        'FUNCS = {"safe": mark_safe}\ndef f(raw):\n    return FUNCS["safe"](f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F10_getattr_sink": (
        "import django.utils.safestring as ss\n"
        "def f(raw):\n"
        '    return getattr(ss, "mark_safe")(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F11_map_sink": (
        'def f(raw):\n    return list(map(mark_safe, [f"<b>{raw}</b>"]))[0]\n',
        ("<x>",),
    ),
    "F15_html_safe_decorator": (
        "from django.utils.html import html_safe\n"
        "@html_safe\n"
        "class W:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "    def __str__(self):\n"
        '        return f"<b>{self.v}</b>"\n'
        "def f(raw):\n"
        "    from django.utils.html import conditional_escape\n"
        "    return conditional_escape(W(raw))\n",
        ("<x>",),
    ),
    "F16__html__method": (
        "class W:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "    def __html__(self):\n"
        '        return f"<b>{self.v}</b>"\n'
        "def f(raw):\n"
        "    from django.utils.html import conditional_escape\n"
        "    return conditional_escape(W(raw))\n",
        ("<x>",),
    ),
    "F20_markupsafe_style_Markup": (
        'class Markup(str):\n    pass\ndef f(raw):\n    return Markup(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F21_str_new_safestring": (
        'def f(raw):\n    return str.__new__(SafeString, f"<b>{raw}</b>")\n',
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
    "F28_return_unsafe_via_str_subclass_html": (
        "def f(raw):\n"
        "    class H(str):\n"
        "        def __html__(self):\n"
        "            return str(self)\n"
        '    return conditional_escape(H(f"<b>{raw}</b>"))\n',
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
    "I15_nonlocal_closure_counter_leak": (
        "def f(raw):\n"
        '    state = {"v": "ok"}\n'
        "    def setv():\n"
        '        state["v"] = raw\n'
        "    setv()\n"
        "    return mark_safe(f\"<b>{state['v']}</b>\")\n",
        ("<x>",),
    ),
    "I16_closure_modifies_list_item": (
        "def f(raw):\n"
        '    box = ["ok"]\n'
        "    def setv():\n"
        "        box[0] = raw\n"
        "    setv()\n"
        '    return mark_safe(f"<b>{box[0]}</b>")\n',
        ("<x>",),
    ),
    "I18_for_target_is_subscript": (
        "def f(raw):\n"
        '    box = ["ok"]\n'
        "    for box[0] in [raw]:\n"
        "        pass\n"
        '    return mark_safe(f"<b>{box[0]}</b>")\n',
        ("<x>",),
    ),
    "I19_with_target_is_subscript": (
        "import contextlib\n"
        "def f(raw):\n"
        '    box = ["ok"]\n'
        "    with contextlib.nullcontext(raw) as box[0]:\n"
        "        pass\n"
        '    return mark_safe(f"<b>{box[0]}</b>")\n',
        ("<x>",),
    ),
    "I21_star_import_escape": (
        'from re import *\ndef f(raw):\n    return mark_safe(f"<b>{escape(raw)}</b>")\n',
        ("<x>",),
    ),
}
