"""
Forms demo views - showcasing Django forms integration with LiveView
"""

from djust import LiveView, FormMixin
from ..forms import RegistrationForm, ContactForm, ProfileForm, SimpleContactForm
from .base import BaseTemplateView


class FormsIndexView(BaseTemplateView):
    """
    Forms demo index page showing all available form examples.

    Note: This inherits from BaseTemplateView (not LiveView) because it's
    a static page, but it gets the navbar component automatically.
    """
    template_name = 'forms/index.html'



class RegistrationFormView(FormMixin, LiveView):
    """
    User registration form with real-time validation

    Demonstrates:
    - Field-level validation on change
    - Password matching validation
    - Custom clean methods
    - Success/error messaging
    """

    form_class = RegistrationForm
    template_name = "forms/registration.html"

    def form_valid(self, form):
        """Handle successful registration"""
        self.success_message = f"Account created successfully for {form.cleaned_data['username']}!"
        # In real app: save user, send email, etc.

    def form_invalid(self, form):
        """Handle validation errors"""
        self.error_message = "Please correct the errors below"

    def clear_message(self, **kwargs):
        """Clear success/error messages"""
        self.success_message = ""
        self.error_message = ""


class ContactFormView(FormMixin, LiveView):
    """
    Contact form with various field types

    Demonstrates:
    - Text, email, textarea fields
    - Select dropdowns
    - Radio buttons
    - Checkboxes
    - Custom validation
    """

    form_class = ContactForm
    template_name = "forms/contact.html"

    def form_valid(self, form):
        """Handle successful submission"""
        self.success_message = f"Thank you {form.cleaned_data['name']}! Your message has been sent."
        # In real app: send email, save to database, etc.

    def form_invalid(self, form):
        """Handle validation errors"""
        self.error_message = "Please correct the errors below"

    def clear_message(self, **kwargs):
        """Clear messages"""
        self.success_message = ""
        self.error_message = ""


class ProfileFormView(FormMixin, LiveView):
    """
    Profile form demonstrating various field types

    Demonstrates:
    - Date fields
    - URL fields
    - Phone fields
    - Optional fields
    - Field help text
    """

    form_class = ProfileForm
    template_name = "forms/profile.html"

    def form_valid(self, form):
        """Handle successful submission"""
        self.success_message = "Profile updated successfully!"
        # In real app: save to database

    def clear_message(self, **kwargs):
        """Clear messages"""
        self.success_message = ""


class SimpleContactFormView(FormMixin, LiveView):
    """
    Simple contact form demo with LiveView integration.

    This demonstrates Django Forms integration with real-time validation.
    """
    form_class = SimpleContactForm
    template_name = 'forms/simple.html'

    def form_valid(self, form):
        """Handle successful form submission"""
        self.success_message = f"Thanks {form.cleaned_data['name']}! We received your message."
        self.reset_form()

    def form_invalid(self, form):
        """Handle failed form submission"""
        self.error_message = "Please correct the errors below."

    def clear_message(self, **kwargs):
        """Clear success/error messages"""
        self.success_message = ""
        self.error_message = ""


class AutoContactFormView(FormMixin, LiveView):
    """
    Auto-rendered contact form using as_live() method with Bootstrap 5.

    Demonstrates automatic form rendering by calling as_live() in Python
    and passing the HTML to the template context.
    """
    form_class = SimpleContactForm
    template_name = 'forms/auto.html'

    def get_context_data(self, **kwargs):
        """Add pre-rendered form HTML to context"""
        context = super().get_context_data(**kwargs)
        # Render the form using as_live() and add to context
        from django.utils.safestring import mark_safe
        context['auto_form_html'] = mark_safe(self.as_live())  # Default: Bootstrap 5
        return context

    def form_valid(self, form):
        """Handle successful form submission"""
        self.success_message = f"Thanks {form.cleaned_data['name']}! We received your message."
        self.reset_form()

    def form_invalid(self, form):
        """Handle failed form submission"""
        self.error_message = "Please correct the errors below."

    def clear_message(self, **kwargs):
        """Clear success/error messages"""
        self.success_message = ""
        self.error_message = ""


class AutoContactFormTailwindView(FormMixin, LiveView):
    """
    Auto-rendered contact form using Tailwind CSS framework.
    """
    form_class = SimpleContactForm
    template_name = 'forms/auto_tailwind.html'

    def get_context_data(self, **kwargs):
        """Add pre-rendered form HTML to context using Tailwind adapter"""
        context = super().get_context_data(**kwargs)
        from django.utils.safestring import mark_safe
        context['auto_form_html'] = mark_safe(self.as_live(framework='tailwind'))
        return context

    def form_valid(self, form):
        """Handle successful form submission"""
        self.success_message = f"Thanks {form.cleaned_data['name']}! We received your message."
        self.reset_form()

    def form_invalid(self, form):
        """Handle failed form submission"""
        self.error_message = "Please correct the errors below."

    def clear_message(self, **kwargs):
        """Clear success/error messages"""
        self.success_message = ""
        self.error_message = ""


class AutoContactFormPlainView(FormMixin, LiveView):
    """
    Auto-rendered contact form using plain HTML (minimal styling).
    """
    form_class = SimpleContactForm
    template_name = 'forms/auto_plain.html'

    def get_context_data(self, **kwargs):
        """Add pre-rendered form HTML to context using Plain adapter"""
        context = super().get_context_data(**kwargs)
        from django.utils.safestring import mark_safe
        context['auto_form_html'] = mark_safe(self.as_live(framework='plain'))
        return context

    def form_valid(self, form):
        """Handle successful form submission"""
        self.success_message = f"Thanks {form.cleaned_data['name']}! We received your message."
        self.reset_form()

    def form_invalid(self, form):
        """Handle failed form submission"""
        self.error_message = "Please correct the errors below."

    def clear_message(self, **kwargs):
        """Clear success/error messages"""
        self.success_message = ""
        self.error_message = ""


class AutoFormComparisonView(LiveView):
    """
    Framework comparison page showing all three CSS framework adapters.
    """
    template_name = 'forms/auto_comparison.html'
