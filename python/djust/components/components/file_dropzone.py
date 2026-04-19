"""FileDropzone component."""

import html
from djust import Component


class FileDropzone(Component):
    """Drag-and-drop file upload zone component.

    Args:
        name: file input name
        label: label text
        accept: accepted file types
        multiple: allow multiple files
        max_size_mb: max file size in MB"""

    def __init__(
        self,
        name: str = "file",
        label: str = "",
        accept: str = "",
        multiple: bool = False,
        max_size_mb: int = 10,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            label=label,
            accept=accept,
            multiple=multiple,
            max_size_mb=max_size_mb,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.label = label
        self.accept = accept
        self.multiple = multiple
        self.max_size_mb = max_size_mb
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the filedropzone HTML."""
        cls = "dropzone"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_name = html.escape(self.name)
        e_label = html.escape(self.label)
        e_accept = html.escape(self.accept)
        label_html = f'<label class="form-label">{e_label}</label>' if self.label else ""
        multiple_attr = " multiple" if self.multiple else ""
        accept_attr = f' accept="{e_accept}"' if self.accept else ""
        return (
            f"{label_html}"
            f'<div class="{cls}">'
            f'<input type="file" name="{e_name}" class="dropzone-input"'
            f"{accept_attr}{multiple_attr}>"
            f'<div class="dz-text">Drag files here or <span class="dz-browse">browse</span></div>'
            f'<div class="dz-hint">Max {self.max_size_mb} MB</div>'
            f"</div>"
        )
