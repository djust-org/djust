"""#3723 -- ``_blank_template_comments`` must return the same text once its scan is linear.

``_reference_blank_template_comments`` is the function as it stood before #3723, kept
verbatim as the oracle: correct and quadratic. The one in ``djust.checks.utils`` must
return exactly what it returns on every input. Two corpora: a hand-written one (every
shape the docstring promises, plus the ones a cursor could get wrong: an unterminated
opener before a terminated one, a ``%}`` inside a note, comments inside ``verbatim``),
and a seeded random one built from fragments that interact.

The growth claim itself is pinned in ``test_template_checks_skip_comments_3283.py``
(``test_blanking_is_linear_in_the_number_of_openers``).
"""

import random
import re

import pytest

from djust.checks.utils import _blank_template_comments

_REF_COMMENT_OPENER = re.compile(r"\{#|\{%\s*comment\b")
_REF_ENDCOMMENT = re.compile(r"\{%\s*endcomment\s*%\}")
_REF_LINE_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85  ")


def _reference_blank_template_comments(content: str) -> str:
    if "{#" not in content and "{%" not in content:
        return content
    pieces: list[str] = []
    done = 0
    position = 0
    no_endcomment = False
    dead_until = {True: -1, False: -1}
    while True:
        opener = _REF_COMMENT_OPENER.search(content, position)
        if opener is None:
            break
        position = opener.end()
        inline = opener.group(0) == "{#"
        if position <= dead_until[inline]:
            continue
        line_end = content.find("\n", position)
        if line_end == -1:
            line_end = len(content)
        closer = content.find("#}" if inline else "%}", position, line_end)
        if closer == -1:
            dead_until[inline] = line_end
            continue
        end = closer + 2
        if not inline:
            if no_endcomment:
                continue
            endcomment = _REF_ENDCOMMENT.search(content, end)
            if endcomment is None:
                no_endcomment = True
                continue
            end = endcomment.end()
        pieces.append(content[done : opener.start()])
        pieces.append(
            "".join(ch if ch in _REF_LINE_BREAKS else " " for ch in content[opener.start() : end])
        )
        done = position = end
    if not pieces:
        return content
    pieces.append(content[done:])
    return "".join(pieces)


_CORPUS = [
    "",
    "plain text",
    "{{ x }} {% if a %}b{% endif %}",
    "{# x #}",
    "a {# x #} b {# y #} c",
    "{# unterminated",
    "{# one\nline #}",
    "{# a #}{# b #}",
    "{% comment %}x{% endcomment %}",
    "{% comment %}x{% endcomment %}{% comment %}y{% endcomment %}",
    '{% comment "note" %}x{% endcomment %}',
    '{% comment "100% sure" %}<input>{% endcomment %}',
    "{% comment 'a' %}x{% endcomment %} tail",
    "{% comment %}\nmulti\nline\n{% endcomment %}\nafter",
    "{% comment %}no end",
    "{% comment %}no end {% comment %}again",
    "{% comment",
    "{% comment %",
    "{% commentary %}x{% endcomment %}",
    "{%comment%}x{%endcomment%}",
    "{%   comment   %}x{%  endcomment  %}",
    "{% comment %}{% comment %}inner{% endcomment %}outer{% endcomment %}",
    "{# {% comment %} #}x{% endcomment %}",
    "{% comment %}{# x #}{% endcomment %}",
    "{# x {% comment %}y{% endcomment %} #}",
    "{% comment %}x{% endcomment %}{# y #}{% comment 'n' %}z{% endcomment %}",
    "{% verbatim %}{% comment %}x{% endcomment %}{% endverbatim %}",
    "{% verbatim %}{# x #}{% endverbatim %}{# y #}",
    "{% raw %}{# x #}{% endraw %}",
    "{# x\r\ny #} {% comment %}\x0b\r\n{% endcomment %}",
    "{% comment %}x{% endcomment %}" * 50,
    "{% comment %}" * 50,
    "{% comment %}" * 50 + "x{% endcomment %}",
    "{% comment %}x{% endcomment %}\n" * 30,
    "{% comment %}x{% endcomment %}" * 30 + "\n" + "{% comment %}y{% endcomment %}" * 30,
    "{% comment ",
    "{% comment %}x{% endcomment " * 20,
    "{# x " * 20,
    "{# x\n" * 20 + "#}",
    "{# x {% comment %}" * 20,
    "{% comment %}" * 20 + "\n" + "{% comment %}x{% endcomment %}",
    "no newline {% comment %}x{% endcomment %} " * 20 + "\n",
    "\n{% comment %}x{% endcomment %}\n",
    # A newline that sits between two openers: the cached "next newline" must be
    # re-found once the cursor has moved past it.
    "{% comment %}a{% endcomment %}\n{% comment %}b{% endcomment %}",
    "{% comment %}a\n{% endcomment %}{% comment %}b{% endcomment %}",
    "{% comment 'x\n' %}a{% endcomment %}{% comment %}b{% endcomment %}",
    "{% comment %}a{% endcomment %}" * 5 + "\n" + "{% comment 'n' %}b{% endcomment %}" * 5,
]

_FRAGMENTS = [
    "{#",
    "#}",
    "{% comment %}",
    "{% endcomment %}",
    '{% comment "n" %}',
    "{% comment",
    "{% comment 'a%}b' %}",
    "%}",
    "{%",
    "\n",
    "\r\n",
    " ",
    "x",
    " ",
    "{{ v }}",
    "{% verbatim %}",
    "{% endverbatim %}",
]


def _random_corpus(count=4000, seed=3723):
    rng = random.Random(seed)
    return [
        "".join(rng.choice(_FRAGMENTS) for _ in range(rng.randint(0, 14))) for _ in range(count)
    ]


@pytest.mark.parametrize("source", _CORPUS)
def test_blanking_matches_the_reference_on_the_corpus(source):
    assert _blank_template_comments(source) == _reference_blank_template_comments(source)


def test_blanking_matches_the_reference_on_random_templates():
    # One test over thousands of inputs, reporting the first disagreement. Seeded, so
    # a failure here reproduces.
    for source in _random_corpus():
        assert _blank_template_comments(source) == _reference_blank_template_comments(source), (
            repr(source)
        )


def test_the_corpus_reaches_the_branches_the_fix_touches():
    # A differential is worthless on inputs that never reach the code it guards: the
    # corpus must blank something, leave something alone, and reach an opener both on
    # a template that has a newline after it and on one that has none.
    inputs = _CORPUS + _random_corpus()
    blanked = [s for s in inputs if _reference_blank_template_comments(s) != s]
    untouched = [
        s
        for s in inputs
        if ("{#" in s or "{%" in s) and _reference_blank_template_comments(s) == s
    ]
    assert len(blanked) > 200 and len(untouched) > 20
    assert any("\n" in s and "{% comment %}" in s for s in blanked)
    assert any("\n" not in s and "{% comment %}" in s for s in blanked)
