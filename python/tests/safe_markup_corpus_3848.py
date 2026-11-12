"""Unsafe code the safe-markup guard must flag (#3848), from the independent review's corpus.

Each case defines ``f(*args)``; ``test_safe_markup_guard_3848.py`` RUNS it (the execution is the
oracle: the payload ``<x>`` must come out raw) and checks the guard flags it. ``REGRESSIONS`` are the
27 that the first guard flagged and the head of #3848 did not; the rest are unsafe cases the head
missed. Generated from the review's three corpus files; edit the guard, not this list.
"""

CASES = {
    "A20_nonlocal_write_in_nested": (
        "def f(raw):\n"
        "    v = 1\n"
        "    def set_():\n"
        "        nonlocal v\n"
        "        v = raw\n"
        "    set_()\n"
        '    return mark_safe(f"<b>{v}</b>")\n',
        ("<x>",),
    ),
    "A21_global_write_other_fn": (
        'G = "ok"\n'
        "def setg(raw):\n"
        "    global G\n"
        "    G = raw\n"
        "def f(raw):\n"
        "    setg(raw)\n"
        '    return mark_safe(f"<b>{G}</b>")\n',
        ("<x>",),
    ),
    "A35_dunder_add": (
        'def f(raw):\n    s = "<b>".__add__(raw)\n    return mark_safe(s)\n',
        ("<x>",),
    ),
    "A36_dict_ior": (
        "def f(raw):\n"
        '    d = {"a": "<i>"}\n'
        '    d |= {"k": raw}\n'
        "    return mark_safe(f\"<b>{d['k']}</b>\")\n",
        ("<x>",),
    ),
    "B02_alias_append": (
        "def f(raw):\n"
        '    items = ["<i>"]\n'
        "    alias = items\n"
        "    alias.append(raw)\n"
        '    return mark_safe("".join(items))\n',
        ("<x>",),
    ),
    "B07_helper_mutates": (
        "def add(lst, v):\n"
        "    lst.append(v)\n"
        "def f(raw):\n"
        '    items = ["<i>"]\n'
        "    add(items, raw)\n"
        '    return mark_safe("".join(items))\n',
        ("<x>",),
    ),
    "B09_dict_update_kw": (
        "def f(raw):\n"
        '    d = {"a": "<i>"}\n'
        "    d.update(k=raw)\n"
        "    return mark_safe(f\"<b>{d['k']}</b>\")\n",
        ("<x>",),
    ),
    "B11_dict_setdefault": (
        "def f(raw):\n"
        '    d = {"a": "<i>"}\n'
        '    d.setdefault("k", raw)\n'
        "    return mark_safe(f\"<b>{d['k']}</b>\")\n",
        ("<x>",),
    ),
    "B33_iadd_non_display": (
        "def f(raw):\n"
        '    items = ["<i>"]\n'
        "    more = [raw]\n"
        "    items += more\n"
        '    return mark_safe("".join(items))\n',
        ("<x>",),
    ),
    "B39_str_mult": ('def f(raw, n=2):\n    return mark_safe(f"<b>{raw}</b>" * n)\n', ("<x>",)),
    "C05_replace_placeholder": (
        'TEMPLATE = "<b>@@</b>"\ndef f(raw):\n    return mark_safe(TEMPLATE.replace("@@", raw))\n',
        ("<x>",),
    ),
    "C06_str_add_dunder": (
        'def f(raw):\n    return mark_safe(str.__add__("<b>", raw))\n',
        ("<x>",),
    ),
    "C07_re_sub": (
        'import re\ndef f(raw):\n    return mark_safe(re.sub("X", raw, "<b>X</b>"))\n',
        ("<x>",),
    ),
    "D15_in_setdefault_mutation": (
        'ALLOWED = {"a": 1}\n'
        "def add(k):\n"
        "    ALLOWED.setdefault(k, 1)\n"
        "def f(raw):\n"
        "    add(raw)\n"
        "    if raw in ALLOWED:\n"
        '        return mark_safe(f"<b>{raw}</b>")\n'
        '    return ""\n',
        ("<x>",),
    ),
    "D18_in_alias_mutation": (
        'ALLOWED = ["a"]\n'
        "def add(k):\n"
        "    a = ALLOWED\n"
        "    a.append(k)\n"
        "def f(raw):\n"
        "    add(raw)\n"
        "    if raw in ALLOWED:\n"
        '        return mark_safe(f"<b>{raw}</b>")\n'
        '    return ""\n',
        ("<x>",),
    ),
    "D19_in_dict_update_kwonly": (
        'ALLOWED = {"a": 1}\n'
        "def add(**kw):\n"
        "    ALLOWED.update(**kw)\n"
        "def f(raw):\n"
        "    add(**{raw: 1})\n"
        "    if raw in ALLOWED:\n"
        '        return mark_safe(f"<b>{raw}</b>")\n'
        '    return ""\n',
        ("<x>",),
    ),
    "D21_in_clear_then_setitem_via_dunder": (
        'ALLOWED = {"a": 1}\n'
        "def f(raw):\n"
        "    ALLOWED.__setitem__(raw, 1)\n"
        "    if raw in ALLOWED:\n"
        '        return mark_safe(f"<b>{raw}</b>")\n'
        '    return ""\n',
        ("<x>",),
    ),
    "D25_in_local_via_alias_append": (
        "def f(raw):\n"
        '    ok = ["a"]\n'
        "    other = ok\n"
        "    other.append(raw)\n"
        "    if raw in ok:\n"
        '        return mark_safe(f"<b>{raw}</b>")\n'
        '    return ""\n',
        ("<x>",),
    ),
    "E03_helper_rebound_via_global_in_other_fn": (
        "from django.utils.html import escape\n"
        "def helper(x):\n"
        "    return escape(x)\n"
        "def patch():\n"
        "    global helper\n"
        "    helper = lambda v: v\n"
        "def f(raw):\n"
        "    patch()\n"
        '    return mark_safe("<b>" + helper(raw) + '
        '"</b>")\n',
        ("<x>",),
    ),
    "E04_helper_monkeypatched_dunder_dict": (
        "from django.utils.html import escape\n"
        "def helper(x):\n"
        "    return escape(x)\n"
        "def f(raw):\n"
        '    globals()["helper"] = lambda v: v\n'
        '    return mark_safe("<b>" + helper(raw) + "</b>")\n',
        ("<x>",),
    ),
    "E07_helper_class_scope_bare_name": (
        "from django.utils.html import escape\n"
        "def helper(x):\n"
        "    return x\n"
        "class K:\n"
        "    def helper(self, x):\n"
        "        return escape(x)\n"
        "    def m(self, raw):\n"
        '        return mark_safe("<b>" + helper(raw) + "</b>")\n'
        "def f(raw):\n"
        "    return K().m(raw)\n",
        ("<x>",),
    ),
    "E09_helper_method_setattr_instance": (
        "from django.utils.html import escape\n"
        "class Base:\n"
        "    def item(self, x):\n"
        "        return escape(x)\n"
        "    def m(self, raw):\n"
        '        return mark_safe("<b>" + self.item(raw) + '
        '"</b>")\n'
        "def f(raw):\n"
        "    o = Base()\n"
        '    setattr(o, "item", lambda x: x)\n'
        "    return o.m(raw)\n",
        ("<x>",),
    ),
    "E11_helper_mutates_outer_list": (
        "ITEMS = []\n"
        "def helper(x):\n"
        "    ITEMS.append(x)\n"
        '    return ""\n'
        "def f(raw):\n"
        "    helper(raw)\n"
        '    return mark_safe("".join(["<i>"] + ITEMS))\n',
        ("<x>",),
    ),
    "E12_helper_mutates_param_list": (
        "def helper(lst, x):\n"
        "    lst.append(x)\n"
        '    return ""\n'
        "def f(raw):\n"
        '    items = ["<i>"]\n'
        "    helper(items, raw)\n"
        '    return mark_safe("<b>" + "".join(items) + "</b>")\n',
        ("<x>",),
    ),
    "E13_helper_returns_global_set_elsewhere": (
        'CACHE = ""\n'
        "def put(x):\n"
        "    global CACHE\n"
        "    CACHE = x\n"
        "def get():\n"
        "    return CACHE\n"
        "def f(raw):\n"
        "    put(raw)\n"
        '    return mark_safe("<b>" + get() + "</b>")\n',
        ("<x>",),
    ),
    "E19_helper_clean_return_but_kwarg_side_effect": (
        "from django.utils.html import escape\n"
        "def helper(x, sink=None):\n"
        "    sink.append(x)\n"
        '    return ""\n'
        "def f(raw):\n"
        '    sink = ["<i>"]\n'
        '    helper("", sink)\n'
        '    sink.append("</i>")\n'
        "    helper(raw, sink)\n"
        '    return mark_safe("".join(sink))\n',
        ("<x>",),
    ),
    "E37_helper_returns_global_dict_lookup_mutated_in_fn": (
        'TABLE = {"a": "<i>"}\n'
        "def put(k, v):\n"
        "    TABLE[k] = v\n"
        "def helper(k):\n"
        "    return TABLE[k]\n"
        "def f(raw):\n"
        '    put("zz", raw)\n'
        '    return mark_safe("<b>" + '
        'helper("zz") + "</b>")\n',
        ("<x>",),
    ),
    "E38_method_helper_returns_class_dict_mutated": (
        "class K:\n"
        '    TABLE = {"a": "<i>"}\n'
        "    def put(self, k, v):\n"
        "        self.TABLE[k] = v\n"
        "    def get(self, k):\n"
        "        return self.TABLE[k]\n"
        "    def m(self, raw):\n"
        '        self.put("zz", raw)\n'
        '        return mark_safe("<b>" + self.get("zz") '
        '+ "</b>")\n'
        "def f(raw):\n"
        "    return K().m(raw)\n",
        ("<x>",),
    ),
    "F03_annotated_alias": (
        'ms: object = mark_safe\ndef f(raw):\n    return ms(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F04_def_wrapper_unnamed": (
        'def _wrap(v):\n    return mark_safe(v)\ndef f(raw):\n    return _wrap(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F05_lambda_wrapper": (
        '_wrap = lambda v: mark_safe(v)\ndef f(raw):\n    return _wrap(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F06_partial": (
        "import functools\n"
        "_wrap = functools.partial(mark_safe)\n"
        "def f(raw):\n"
        '    return _wrap(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F12_wrapper_3_stmts": (
        "def _wrap(v):\n"
        "    x = v.strip()\n"
        "    y = x\n"
        "    return mark_safe(y)\n"
        "def f(raw):\n"
        '    return _wrap(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F13_wrapper_str_arg": (
        "def _wrap(v):\n"
        "    return mark_safe(str(v))\n"
        "def f(raw):\n"
        '    return _wrap(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F14_wrapper_args_index": (
        "def _wrap(*a):\n"
        "    return mark_safe(a[0])\n"
        "def f(raw):\n"
        '    return _wrap(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F32_alias_walrus": (
        'def f(raw):\n    (ms := mark_safe)\n    return ms(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F33_alias_tuple_assign": (
        'a, b = mark_safe, str\ndef f(raw):\n    return a(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F35_alias_multi_target": (
        'x = y = mark_safe\ndef f(raw):\n    return y(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F36_alias_for_target": (
        'for ms in (mark_safe,):\n    pass\ndef f(raw):\n    return ms(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "F37_alias_with_target": (
        "import contextlib\n"
        "with contextlib.nullcontext(mark_safe) as ms:\n"
        "    pass\n"
        "def f(raw):\n"
        '    return ms(f"<b>{raw}</b>")\n',
        ("<x>",),
    ),
    "H01_setattr_in_init": (
        "class C:\n"
        '    LABEL = "ok"\n'
        "    def __init__(self, **kw):\n"
        "        for k, v in kw.items():\n"
        "            setattr(self, k, v)\n"
        "    def r(self):\n"
        '        return mark_safe(f"<b>{self.LABEL}</b>")\n'
        "def f(raw):\n"
        "    return C(LABEL=raw).r()\n",
        ("<x>",),
    ),
    "H02_dict_update": (
        "class C:\n"
        '    LABEL = "ok"\n'
        "    def __init__(self, **kw):\n"
        "        self.__dict__.update(kw)\n"
        "    def r(self):\n"
        '        return mark_safe(f"<b>{self.LABEL}</b>")\n'
        "def f(raw):\n"
        "    return C(LABEL=raw).r()\n",
        ("<x>",),
    ),
    "H03_self_table_setitem": (
        "class C:\n"
        '    ICONS = {"a": "<svg/>"}\n'
        "    def register(self, name, svg):\n"
        "        self.ICONS[name] = svg\n"
        "    def r(self, name):\n"
        '        return mark_safe(f"<i>{self.ICONS[name]}</i>")\n'
        "def f(raw):\n"
        "    c = C()\n"
        '    c.register("zz", raw)\n'
        '    return c.r("zz")\n',
        ("<x>",),
    ),
    "H04_self_list_append": (
        "class C:\n"
        '    ITEMS = ["<li>a</li>"]\n'
        "    def add(self, x):\n"
        "        self.ITEMS.append(x)\n"
        "    def r(self):\n"
        '        return mark_safe("<ul>" + "".join(self.ITEMS) + "</ul>")\n'
        "def f(raw):\n"
        "    c = C()\n"
        "    c.add(raw)\n"
        "    return c.r()\n",
        ("<x>",),
    ),
    "H05_self_dict_update": (
        "class C:\n"
        '    ICONS = {"a": "<svg/>"}\n'
        "    def register(self, **kw):\n"
        "        self.ICONS.update(kw)\n"
        "    def r(self, name):\n"
        '        return mark_safe(f"<i>{self.ICONS[name]}</i>")\n'
        "def f(raw):\n"
        "    c = C()\n"
        "    c.register(zz=raw)\n"
        '    return c.r("zz")\n',
        ("<x>",),
    ),
    "H06_object_setattr": (
        "class C:\n"
        '    LABEL = "ok"\n'
        "    def __init__(self, v):\n"
        '        object.__setattr__(self, "LABEL", v)\n'
        "    def r(self):\n"
        '        return mark_safe(f"<b>{self.LABEL}</b>")\n'
        "def f(raw):\n"
        "    return C(raw).r()\n",
        ("<x>",),
    ),
    "H07_vars_self": (
        "class C:\n"
        '    LABEL = "ok"\n'
        "    def __init__(self, v):\n"
        '        vars(self)["LABEL"] = v\n'
        "    def r(self):\n"
        '        return mark_safe(f"<b>{self.LABEL}</b>")\n'
        "def f(raw):\n"
        "    return C(raw).r()\n",
        ("<x>",),
    ),
    "H08_cls_table_setitem_classmethod": (
        "class C:\n"
        '    ICONS = {"a": "<svg/>"}\n'
        "    @classmethod\n"
        "    def register(cls, name, svg):\n"
        "        cls.ICONS[name] = svg\n"
        "    def r(self, name):\n"
        '        return mark_safe(f"<i>{self.ICONS[name]}</i>")\n'
        "def f(raw):\n"
        '    C.register("zz", raw)\n'
        '    return C().r("zz")\n',
        ("<x>",),
    ),
    "H09_narrow_self_valid_mutated_via_self": (
        "class C:\n"
        '    VALID = ["a"]\n'
        "    def allow(self, x):\n"
        "        self.VALID.append(x)\n"
        "    def r(self, mode):\n"
        "        if mode in self.VALID:\n"
        '            return mark_safe(f"<b>{mode}</b>")\n'
        '        return ""\n'
        "def f(raw):\n"
        "    c = C()\n"
        "    c.allow(raw)\n"
        "    return c.r(raw)\n",
        ("<x>",),
    ),
    "H10_narrow_self_valid_mutated_via_setitem": (
        "class C:\n"
        '    VALID = {"a": 1}\n'
        "    def allow(self, x):\n"
        "        self.VALID[x] = 1\n"
        "    def r(self, mode):\n"
        "        if mode in self.VALID:\n"
        '            return mark_safe(f"<b>{mode}</b>")\n'
        '        return ""\n'
        "def f(raw):\n"
        "    c = C()\n"
        "    c.allow(raw)\n"
        "    return c.r(raw)\n",
        ("<x>",),
    ),
    "H11_narrow_self_valid_setattr_instance": (
        "class C:\n"
        '    VALID = ("a",)\n'
        "    def __init__(self, **kw):\n"
        "        for k, v in kw.items():\n"
        "            setattr(self, k, v)\n"
        "    def r(self, mode):\n"
        "        if mode in self.VALID:\n"
        '            return mark_safe(f"<b>{mode}</b>")\n'
        '        return ""\n'
        "def f(raw):\n"
        "    return C(VALID=(raw,)).r(raw)\n",
        ("<x>",),
    ),
    "H12_class_attr_via_ClassName_other_instance": (
        "class C:\n"
        '    LABEL = "ok"\n'
        "    def r(self):\n"
        '        return mark_safe(f"<b>{C.LABEL}</b>")\n'
        "def f(raw):\n"
        '    setattr(C, "LABEL", raw)\n'
        "    return C().r()\n",
        ("<x>",),
    ),
    "H13_mixin_pattern_props": (
        "class Component:\n"
        '    CSS_CLASS = "btn"\n'
        "    def __init__(self, **props):\n"
        "        for key, value in props.items():\n"
        "            setattr(self, key, value)\n"
        "    def render(self):\n"
        "        return mark_safe(f'<button "
        'class="{self.CSS_CLASS}">x</button>\')\n'
        "def f(raw):\n"
        "    return Component(CSS_CLASS=raw).render()\n",
        ("<x>",),
    ),
    "H14_class_attr_dataclass_like_init_default": (
        "class C:\n"
        '    LABEL = "ok"\n'
        "    def __init__(self, label=None):\n"
        "        if label is not None:\n"
        '            self.__dict__["LABEL"] = label\n'
        "    def r(self):\n"
        '        return mark_safe(f"<b>{self.LABEL}</b>")\n'
        "def f(raw):\n"
        "    return C(raw).r()\n",
        ("<x>",),
    ),
    "H20_getattr_dynamic": (
        "class C:\n"
        '    LABEL = "ok"\n'
        "    def __getattribute__(self, name):\n"
        '        if name == "LABEL":\n'
        '            return "<x>"\n'
        "        return object.__getattribute__(self, name)\n"
        "    def r(self):\n"
        '        return mark_safe(f"<i>{self.LABEL}</i>")\n'
        "def f(raw):\n"
        "    return C().r()\n",
        ("<x>",),
    ),
    "I13_global_name_set_by_exec_or_globals": (
        'v = "ok"\ndef f(raw):\n    globals()["v"] = raw\n    return mark_safe(f"<b>{v}</b>")\n',
        ("<x>",),
    ),
    "I14_module_v_set_by_other_fn_global": (
        'v = "ok"\n'
        "def setv(x):\n"
        "    global v\n"
        "    v = x\n"
        "def f(raw):\n"
        "    setv(raw)\n"
        '    return mark_safe(f"<b>{v}</b>")\n',
        ("<x>",),
    ),
    # --- false negatives the review of #3848 pinned, caught since #3850's follow-up drain ---
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
    "F21_str_new_safestring": (
        'def f(raw):\n    return str.__new__(SafeString, f"<b>{raw}</b>")\n',
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
    # --- the data twins of the literal-payload misses left in safe_markup_known_misses_3848 ---
    # The same positions with a value from outside the file (an environment variable stands in for
    # request data): flagged, so a kept miss is the literal, not the position.
    "F23_sink_in_default_arg_data": (
        "import os\n"
        "def f(raw, out=mark_safe(f\"<b>{os.environ.get('DJ_X', '<x>')}</b>\")):\n"
        "    return out\n",
        ("<x>",),
    ),
    "F24_sink_in_decorator_arg_data": (
        "import os\n"
        "def deco(v):\n"
        "    def d(fn):\n"
        "        fn.out = v\n"
        "        return fn\n"
        "    return d\n"
        "@deco(mark_safe(f\"<b>{os.environ.get('DJ_X', '<x>')}</b>\"))\n"
        "def f(raw):\n"
        "    return f.out\n",
        ("<x>",),
    ),
    "F25_sink_in_class_body_data": (
        "import os\n"
        "class K:\n"
        "    out = mark_safe(f\"<b>{os.environ.get('DJ_X', '<x>')}</b>\")\n"
        "def f(raw):\n"
        "    return K.out\n",
        ("<x>",),
    ),
    "I01_module_name_seen_by_method_data": (
        "import os\n"
        "LABEL = os.environ.get('DJ_X', '<x>')\n"
        "class C:\n"
        '    LABEL = "ok"\n'
        "    def r(self, raw):\n"
        '        return mark_safe(f"<b>{LABEL}</b>")\n'
        "def f(raw):\n"
        "    return C().r(raw)\n",
        ("<x>",),
    ),
    "I07_module_level_sink_data": (
        "import os\n"
        "OUT = mark_safe(f\"<b>{os.environ.get('DJ_X', '<x>')}</b>\")\n"
        "def f(raw):\n"
        "    return OUT\n",
        ("<x>",),
    ),
    "I08_module_level_loop_data": (
        "import os\n"
        'OUT = ""\n'
        "for RAW in [os.environ.get('DJ_X', '<x>')]:\n"
        '    OUT = mark_safe(f"<b>{RAW}</b>")\n'
        "def f(raw):\n"
        "    return OUT\n",
        ("<x>",),
    ),
    "I09_class_body_loop_data": (
        "import os\n"
        "class K:\n"
        '    OUT = ""\n'
        "    for RAW in [os.environ.get('DJ_X', '<x>')]:\n"
        '        OUT = mark_safe(f"<b>{RAW}</b>")\n'
        "def f(raw):\n"
        "    return K.OUT\n",
        ("<x>",),
    ),
    "I12_global_name_assigned_in_module_loop_data": (
        "import os\n"
        "for v in [os.environ.get('DJ_X', '<x>')]:\n"
        "    pass\n"
        "def f(raw):\n"
        '    return mark_safe(f"<b>{v}</b>")\n',
        ("<x>",),
    ),
}

REGRESSIONS = [
    "D15_in_setdefault_mutation",
    "D18_in_alias_mutation",
    "D19_in_dict_update_kwonly",
    "D21_in_clear_then_setitem_via_dunder",
    "D25_in_local_via_alias_append",
    "E03_helper_rebound_via_global_in_other_fn",
    "E04_helper_monkeypatched_dunder_dict",
    "E07_helper_class_scope_bare_name",
    "E09_helper_method_setattr_instance",
    "E13_helper_returns_global_set_elsewhere",
    "E37_helper_returns_global_dict_lookup_mutated_in_fn",
    "E38_method_helper_returns_class_dict_mutated",
    "H01_setattr_in_init",
    "H02_dict_update",
    "H03_self_table_setitem",
    "H04_self_list_append",
    "H05_self_dict_update",
    "H06_object_setattr",
    "H07_vars_self",
    "H08_cls_table_setitem_classmethod",
    "H09_narrow_self_valid_mutated_via_self",
    "H10_narrow_self_valid_mutated_via_setitem",
    "H11_narrow_self_valid_setattr_instance",
    "H12_class_attr_via_ClassName_other_instance",
    "H13_mixin_pattern_props",
    "H14_class_attr_dataclass_like_init_default",
    "H20_getattr_dynamic",
]
