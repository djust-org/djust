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
