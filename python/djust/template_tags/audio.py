"""Native renderer entry point for the shared audio control template tag."""

from . import TagHandler, register


@register("djust_audio")
class DjustAudioTagHandler(TagHandler):
    def render(self, args, context):
        from djust.templatetags.live_tags import djust_audio

        if args:
            raise ValueError("djust_audio takes no arguments")
        return str(djust_audio(context))
