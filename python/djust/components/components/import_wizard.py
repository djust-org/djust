"""Import wizard component for multi-step data import."""

import html

from djust import Component


class ImportWizard(Component):
    """Style-agnostic import wizard component.

    Multi-step import flow with file upload, mapping, and preview.

    Usage in a LiveView::

        self.importer = ImportWizard(
            accepted_formats=".csv,.xlsx",
            model_fields=[
                {"id": "name", "label": "Name"},
                {"id": "email", "label": "Email"},
            ],
            event="import_data",
        )

    In template::

        {{ importer|safe }}

    Args:
        accepted_formats: Comma-separated accepted file extensions
        model_fields: List of field dicts with id, label
        event: djust event for triggering import
        step: Current wizard step (upload, map, preview)
        upload_event: djust event for file upload
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        accepted_formats: str = ".csv",
        model_fields: list = None,
        event: str = "import_data",
        step: str = "upload",
        upload_event: str = "upload_file",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            accepted_formats=accepted_formats,
            model_fields=model_fields,
            event=event,
            step=step,
            upload_event=upload_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.accepted_formats = accepted_formats
        self.model_fields = model_fields or []
        self.event = event
        self.step = step
        self.upload_event = upload_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-import-wizard"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_event = html.escape(self.event)
        e_formats = html.escape(self.accepted_formats)

        # Step indicator
        steps = ["upload", "map", "preview"]
        step_labels = {"upload": "Upload", "map": "Map Fields", "preview": "Preview"}
        active_idx = steps.index(self.step) if self.step in steps else 0

        step_items = []
        for i, s in enumerate(steps):
            step_cls = "dj-import-wizard__step"
            if i < active_idx:
                step_cls += " dj-import-wizard__step--completed"
            elif i == active_idx:
                step_cls += " dj-import-wizard__step--active"
            step_items.append(
                f'<div class="{step_cls}">'
                f'<span class="dj-import-wizard__step-number">{i + 1}</span>'
                f'<span class="dj-import-wizard__step-label">'
                f"{html.escape(step_labels[s])}</span></div>"
            )

        nav = f'<div class="dj-import-wizard__nav">{"".join(step_items)}</div>'

        # Step content
        if self.step == "upload":
            e_upload = html.escape(self.upload_event)
            body = (
                f'<div class="dj-import-wizard__upload">'
                f'<div class="dj-import-wizard__dropzone">'
                f"<p>Drag & drop or click to upload</p>"
                f'<input type="file" accept="{e_formats}" '
                f'class="dj-import-wizard__file-input" '
                f'dj-change="{e_upload}">'
                f'<p class="dj-import-wizard__formats">Accepted: {e_formats}</p>'
                f"</div></div>"
            )
        elif self.step == "map":
            field_rows = []
            for field in self.model_fields:
                e_id = html.escape(str(field.get("id", "")))
                e_label = html.escape(str(field.get("label", "")))
                field_rows.append(
                    f'<div class="dj-import-wizard__field-row">'
                    f'<span class="dj-import-wizard__field-label">{e_label}</span>'
                    f'<select class="dj-import-wizard__field-select" name="map_{e_id}">'
                    f'<option value="">-- Skip --</option>'
                    f"</select></div>"
                )
            body = f'<div class="dj-import-wizard__mapping">{"".join(field_rows)}</div>'
        else:  # preview
            body = (
                f'<div class="dj-import-wizard__preview">'
                f"<p>Preview your data before importing.</p>"
                f'<button class="dj-import-wizard__import-btn" '
                f'dj-click="{e_event}">Import</button></div>'
            )

        return f'<div class="{class_str}">{nav}{body}</div>'
