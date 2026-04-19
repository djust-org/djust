"""
Theme compatibility checker.

Scans a theme's overridden component templates and validates them against
the component contracts defined in contracts.py. Reports missing required
elements, missing required context variables, and unused slots.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .contracts import COMPONENT_CONTRACTS, ComponentContract


@dataclass
class CompatIssue:
    """A single compatibility issue found in a theme override."""

    component: str
    severity: str  # "error", "warning", "info"
    message: str


def check_theme_compat(theme_dir: Path) -> list[CompatIssue]:
    """Check all component overrides in a theme against contracts.

    Args:
        theme_dir: Path to the theme directory (containing components/).

    Returns:
        List of CompatIssue instances. Empty list means fully compatible.
    """
    theme_dir = Path(theme_dir)
    components_dir = theme_dir / "components"

    if not components_dir.is_dir():
        return []

    issues: list[CompatIssue] = []

    for html_file in sorted(components_dir.glob("*.html")):
        component_name = html_file.stem
        source = html_file.read_text()

        if component_name not in COMPONENT_CONTRACTS:
            issues.append(
                CompatIssue(
                    component=component_name,
                    severity="warning",
                    message=(
                        f"No contract found for '{component_name}'. "
                        f"This file will be ignored by the theming system."
                    ),
                )
            )
            continue

        contract = COMPONENT_CONTRACTS[component_name]
        issues.extend(_check_required_elements(component_name, source, contract))
        issues.extend(_check_required_context(component_name, source, contract))
        issues.extend(_check_slots(component_name, source, contract))

    return issues


def _check_required_elements(
    component_name: str, source: str, contract: ComponentContract
) -> list[CompatIssue]:
    """Check that all required HTML elements and their attrs are present."""
    issues: list[CompatIssue] = []

    for req in contract.required_elements:
        # Check for the tag itself
        tag_pattern = re.compile(rf"<{re.escape(req.tag)}[\s>]", re.IGNORECASE)
        if not tag_pattern.search(source):
            issues.append(
                CompatIssue(
                    component=component_name,
                    severity="error",
                    message=(f"{component_name}.html: missing required element <{req.tag}>"),
                )
            )
            continue  # No point checking attrs if tag is missing

        # Check required attributes on the tag
        for attr_name, attr_value in req.attrs.items():
            if attr_value is not None:
                # Check for attr="value" (with single or double quotes)
                attr_pattern = re.compile(
                    rf'{re.escape(attr_name)}\s*=\s*["\']' rf"{re.escape(attr_value)}" rf'["\']',
                    re.IGNORECASE,
                )
                if not attr_pattern.search(source):
                    issues.append(
                        CompatIssue(
                            component=component_name,
                            severity="error",
                            message=(
                                f"{component_name}.html: missing required attribute "
                                f'{attr_name}="{attr_value}" on <{req.tag}>'
                            ),
                        )
                    )
            else:
                # Just check attribute name is present
                attr_pattern = re.compile(rf"{re.escape(attr_name)}\s*=", re.IGNORECASE)
                if not attr_pattern.search(source):
                    issues.append(
                        CompatIssue(
                            component=component_name,
                            severity="error",
                            message=(
                                f"{component_name}.html: missing required attribute "
                                f"'{attr_name}' on <{req.tag}>"
                            ),
                        )
                    )

    return issues


def _check_required_context(
    component_name: str, source: str, contract: ComponentContract
) -> list[CompatIssue]:
    """Check that all required context variables are referenced in the template."""
    issues: list[CompatIssue] = []

    for ctx_var in contract.required_context:
        # Look for the variable name in template expressions:
        # {{ var }}, {{ var|filter }}, {% if var %}, {% with var as x %}, etc.
        var_pattern = re.compile(
            rf"(?:\{{\{{\s*{re.escape(ctx_var.name)})"  # {{ var
            rf"|(?:\{{% \s*\w+\s+{re.escape(ctx_var.name)})"  # {% tag var
        )
        if not var_pattern.search(source):
            issues.append(
                CompatIssue(
                    component=component_name,
                    severity="error",
                    message=(
                        f"{component_name}.html: missing required context variable '{ctx_var.name}'"
                    ),
                )
            )

    return issues


def _check_slots(
    component_name: str, source: str, contract: ComponentContract
) -> list[CompatIssue]:
    """Report slots from the contract not referenced in the template."""
    issues: list[CompatIssue] = []

    for slot_name in contract.available_slots:
        if slot_name not in source:
            issues.append(
                CompatIssue(
                    component=component_name,
                    severity="info",
                    message=(f"{component_name}.html: slot '{slot_name}' not used (optional)"),
                )
            )

    return issues
