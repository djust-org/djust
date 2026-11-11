"""Selected Django partial sources for LiveView dependency extraction.

The executable template remains a native ``{% include "file#name" %}``. This
module builds extraction-only source from the selected partial and its called
helpers, so unrelated definitions cannot widen the view's JIT field set.
"""

from __future__ import annotations

from django.template.base import DebugLexer, TokenType

from ._rust import compile_template, raw_block_end_tag, resolve_relative_template_includes
from .template_libraries import library_render_scope


def selected_partial_source(source: str, template_name: str, partial_name: str) -> str:
    """Return selected/called partial bodies for static variable extraction.

    Spans are produced by the real template parser, not by matching template
    syntax in HTML. Calls are expanded at their invocation sites so surrounding
    ``with`` and loop scopes remain visible to the variable extractor.
    """
    with library_render_scope():
        spans = compile_template(source, template_name, return_template=True)._partial_spans()
    encoded = source.encode("utf-8")
    character_spans = {
        name: (len(encoded[:start].decode("utf-8")), len(encoded[:end].decode("utf-8")))
        for name, bounds in spans.items()
        if bounds is not None
        for start, end in [bounds]
    }
    if partial_name not in character_spans:
        from django.template import TemplateDoesNotExist

        raise TemplateDoesNotExist(partial_name)

    def body_for(name: str, ancestors: frozenset[str]) -> str:
        if name in ancestors:
            # Keep a non-rendering marker in the extraction-only source so
            # JIT serialization can recognize recursive model traversal.
            return "{# djust:recursive-partial #}"
        if name not in character_spans:
            return ""
        start, end = character_spans[name]
        region = source[start:end]
        tokens = DebugLexer(region).tokenize()
        if (
            not tokens
            or tokens[0].token_type != TokenType.BLOCK
            or tokens[-1].token_type != TokenType.BLOCK
        ):
            raise ValueError("Named partial does not have parser-aligned definition tags")
        body_start = start + tokens[0].position[1]
        body_end = start + tokens[-1].position[0]
        body = source[body_start:body_end]

        # A nested non-inline definition produces no output unless called.
        # Remove its entire parser-validated region; inline definitions still
        # execute at their authored position and remain in the extraction text.
        exclusions = []
        for child, (left, right) in character_spans.items():
            if child == name or left < body_start or right > body_end:
                continue
            opening = DebugLexer(source[left:right]).tokenize()[0].split_contents()
            if len(opening) < 3 or opening[2] != "inline":
                exclusions.append((left - body_start, right - body_start))
        selected = []
        covered = -1
        for left, right in sorted(exclusions, key=lambda item: (item[0], -item[1])):
            if left >= covered:
                selected.append((left, right))
                covered = right
        for left, right in reversed(selected):
            body = body[:left] + body[right:]

        replacements = []
        in_comment = False
        raw_end = None
        for token in DebugLexer(body).tokenize():
            if token.token_type != TokenType.BLOCK:
                continue
            bits = token.split_contents()
            if not bits:
                continue
            if raw_end is not None:
                if bits[0] == raw_end:
                    raw_end = None
                continue
            if bits[0] == "comment":
                in_comment = True
            elif bits[0] == "endcomment":
                in_comment = False
            elif not in_comment and bits[0] == "partial" and len(bits) == 2:
                replacements.append((token.position, body_for(bits[1], ancestors | {name})))
            elif not in_comment:
                # Raw blocks own their body source; a partial-looking token in
                # that source is not executed by the outer template parser.
                raw_end = raw_block_end_tag(bits[0])
        for (left, right), replacement in reversed(replacements):
            body = body[:left] + replacement + body[right:]
        return body

    with library_render_scope():
        return resolve_relative_template_includes(
            body_for(partial_name, frozenset()), template_name
        )
