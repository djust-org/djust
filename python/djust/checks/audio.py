"""Validate declared audio files without fetching media or creating a view."""

from django.contrib.staticfiles import finders
from django.core.checks import Error, Warning, register

from .components import _routed_liveview_classes


@register("djust")
def check_audio(app_configs, **kwargs):
    from djust.audio import AudioMixin, SoundBank

    messages = []
    for view in set(_routed_liveview_classes()):
        if not issubclass(view, AudioMixin):
            continue
        for bank in view.audio_banks.values():
            if not isinstance(bank, SoundBank):
                messages.append(
                    Error(
                        "audio_banks values must be SoundBank instances",
                        obj=view,
                        id="djust.audio.E001",
                    )
                )
                continue
            for sound in bank.sounds.values():
                if not finders.find(sound.path):
                    messages.append(
                        Warning(
                            f"Declared sound asset not found: {sound.path}",
                            obj=view,
                            hint="Add the file to a staticfiles directory before collectstatic.",
                            id="djust.audio.W001",
                        )
                    )
    return messages
