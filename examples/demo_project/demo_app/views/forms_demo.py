"""
Forms demo views - showcasing Django forms integration with LiveView
"""

from django_rust_live import LiveView, FormMixin
from ..forms import RegistrationForm, ContactForm, ProfileForm, SimpleContactForm


class FormsIndexView(LiveView):
    """
    Forms demo index page showing all available form examples
    """

    template_string = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>FormsIndex - Django Rust Live</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }
            .card { transition: transform 0.2s; }
            .card:hover { transform: translateY(-5px); }
            pre { overflow-x: auto; }
            code { font-size: 14px; }
        </style>
    </head>
    <body>
    <div class="container">
        <div class="row mt-5">
            <div class="col-12">
                <h1 class="display-4 mb-4">Django Forms Integration</h1>
                <p class="lead">
                    Comprehensive forms integration with Django Rust Live, featuring real-time validation,
                    error handling, and seamless LiveView integration.
                </p>
            </div>
        </div>

        <div class="row mt-4">
            <!-- Registration Form -->
            <div class="col-md-6 mb-4">
                <div class="card h-100 shadow-sm">
                    <div class="card-body">
                        <h3 class="card-title">
                            <span class="badge bg-primary">Demo</span>
                            Registration Form
                        </h3>
                        <p class="card-text">
                            User registration with password matching, username validation,
                            and terms acceptance. Demonstrates field-level validation.
                        </p>
                        <ul class="list-unstyled">
                            <li>✓ Real-time field validation</li>
                            <li>✓ Password matching</li>
                            <li>✓ Custom clean methods</li>
                            <li>✓ Checkbox validation</li>
                        </ul>
                        <a href="/forms/registration/" class="btn btn-primary">Try Demo</a>
                    </div>
                </div>
            </div>

            <!-- Contact Form -->
            <div class="col-md-6 mb-4">
                <div class="card h-100 shadow-sm">
                    <div class="card-body">
                        <h3 class="card-title">
                            <span class="badge bg-success">Demo</span>
                            Contact Form
                        </h3>
                        <p class="card-text">
                            Contact form with dropdowns, radio buttons, and text areas.
                            Shows various field types working together.
                        </p>
                        <ul class="list-unstyled">
                            <li>✓ Select dropdowns</li>
                            <li>✓ Radio buttons</li>
                            <li>✓ Text areas</li>
                            <li>✓ Spam detection</li>
                        </ul>
                        <a href="/forms/contact/" class="btn btn-success">Try Demo</a>
                    </div>
                </div>
            </div>

            <!-- Profile Form -->
            <div class="col-md-6 mb-4">
                <div class="card h-100 shadow-sm">
                    <div class="card-body">
                        <h3 class="card-title">
                            <span class="badge bg-info">Demo</span>
                            Profile Form
                        </h3>
                        <p class="card-text">
                            Profile editing with dates, URLs, and optional fields.
                            Demonstrates complex field types and validation.
                        </p>
                        <ul class="list-unstyled">
                            <li>✓ Date fields</li>
                            <li>✓ URL validation</li>
                            <li>✓ Phone validation</li>
                            <li>✓ Optional fields</li>
                        </ul>
                        <a href="/forms/profile/" class="btn btn-info text-white">Try Demo</a>
                    </div>
                </div>
            </div>

            <!-- Features -->
            <div class="col-md-6 mb-4">
                <div class="card h-100 shadow-sm bg-light">
                    <div class="card-body">
                        <h3 class="card-title">Features</h3>
                        <ul class="list-unstyled">
                            <li class="mb-2">
                                <strong>Real-time Validation:</strong> Fields validate as you type
                            </li>
                            <li class="mb-2">
                                <strong>Django Forms:</strong> Uses actual Django forms on backend
                            </li>
                            <li class="mb-2">
                                <strong>LiveView Integration:</strong> Seamless reactive updates
                            </li>
                            <li class="mb-2">
                                <strong>Error Display:</strong> Clear, Bootstrap-styled errors
                            </li>
                            <li class="mb-2">
                                <strong>Form Helpers:</strong> Easy field rendering
                            </li>
                            <li class="mb-2">
                                <strong>Custom Validation:</strong> Field and form-level validation
                            </li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>

        <div class="row mt-4 mb-5">
            <div class="col-12">
                <div class="card border-primary">
                    <div class="card-header bg-primary text-white">
                        <h4 class="mb-0">Documentation</h4>
                    </div>
                    <div class="card-body">
                        <h5>Using FormMixin in Your Views</h5>
                        <pre class="bg-light p-3 rounded"><code>from django_rust_live import LiveView, FormMixin
from django import forms

class MyForm(forms.Form):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()

class MyFormView(FormMixin, LiveView):
    form_class = MyForm

    def form_valid(self, form):
        # Handle valid form submission
        self.success_message = "Form submitted!"

    def form_invalid(self, form):
        # Handle errors
        self.error_message = "Please fix errors"</code></pre>

                        <h5 class="mt-4">Template Usage</h5>
                        <pre class="bg-light p-3 rounded"><code>&lt;form @submit="submit_form"&gt;
    &lt;input name="name" @change="validate_field" data-field="name" /&gt;
    {% if field_errors.name %}
        &lt;div class="error"&gt;{{ field_errors.name }}&lt;/div&gt;
    {% endif %}
    &lt;button type="submit"&gt;Submit&lt;/button&gt;
&lt;/form&gt;</code></pre>
                    </div>
                </div>
            </div>
        </div>
    </div>
    </body>
    </html>
    """


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

    template_string = """
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; background: #f5f5f5; }
        .card { border-radius: 10px; }
        .card-header { border-radius: 10px 10px 0 0 !important; }
        .form-label { font-weight: 500; }
        .invalid-feedback { display: block; }
    </style>
    <div class="container">
        <div class="row justify-content-center mt-5">
            <div class="col-md-6">
                <div class="card shadow">
                    <div class="card-header bg-primary text-white">
                        <h2 class="mb-0">Create Account</h2>
                    </div>
                    <div class="card-body">
                        <div class="alert alert-success alert-dismissible fade show{% if not success_message %} d-none{% endif %}" role="alert">
                            {{ success_message }}
                            <button type="button" class="btn-close" @click="clear_message"></button>
                        </div>

                        <div class="alert alert-danger alert-dismissible fade show{% if not error_message %} d-none{% endif %}" role="alert">
                            {{ error_message }}
                            <button type="button" class="btn-close" @click="clear_message"></button>
                        </div>

                        <form @submit="submit_form" class="needs-validation" novalidate>
                            <!-- Username -->
                            <div class="mb-3">
                                <label for="username" class="form-label">Username</label>
                                <input
                                    type="text"
                                    name="username"
                                    id="username"
                                    class="form-control {% if field_errors.username %}is-invalid{% endif %}"
                                    value="{{ form_data.username }}"
                                    @change="validate_field"
                                    data-field="username"
                                    required
                                />
                                <small class="form-text text-muted">Username must be 3-150 characters</small>
                                {% if field_errors.username %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.username %}
                                    <div>{{ error }}</div>
                                    {% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Email -->
                            <div class="mb-3">
                                <label for="email" class="form-label">Email</label>
                                <input
                                    type="email"
                                    name="email"
                                    id="email"
                                    class="form-control {% if field_errors.email %}is-invalid{% endif %}"
                                    value="{{ form_data.email }}"
                                    @change="validate_field"
                                    data-field="email"
                                    required
                                />
                                <small class="form-text text-muted">We'll never share your email</small>
                                {% if field_errors.email %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.email %}
                                    <div>{{ error }}</div>
                                    {% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Password -->
                            <div class="mb-3">
                                <label for="password" class="form-label">Password</label>
                                <input
                                    type="password"
                                    name="password"
                                    id="password"
                                    class="form-control {% if field_errors.password %}is-invalid{% endif %}"
                                    value="{{ form_data.password }}"
                                    @change="validate_field"
                                    data-field="password"
                                    required
                                />
                                <small class="form-text text-muted">Password must be at least 8 characters</small>
                                {% if field_errors.password %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.password %}
                                    <div>{{ error }}</div>
                                    {% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Confirm Password -->
                            <div class="mb-3">
                                <label for="password_confirm" class="form-label">Confirm Password</label>
                                <input
                                    type="password"
                                    name="password_confirm"
                                    id="password_confirm"
                                    class="form-control {% if field_errors.password_confirm %}is-invalid{% endif %}"
                                    value="{{ form_data.password_confirm }}"
                                    @change="validate_field"
                                    data-field="password_confirm"
                                    required
                                />
                                {% if field_errors.password_confirm %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.password_confirm %}
                                    <div>{{ error }}</div>
                                    {% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Terms and Conditions -->
                            <div class="mb-3 form-check">
                                <input
                                    type="checkbox"
                                    name="agree_terms"
                                    id="agree_terms"
                                    class="form-check-input {% if field_errors.agree_terms %}is-invalid{% endif %}"
                                    {% if form_data.agree_terms %}checked{% endif %}
                                    @change="validate_field"
                                    data-field="agree_terms"
                                    required
                                />
                                <label class="form-check-label" for="agree_terms">
                                    I agree to the Terms and Conditions
                                </label>
                                {% if field_errors.agree_terms %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.agree_terms %}
                                    <div>{{ error }}</div>
                                    {% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Non-field errors -->
                            {% if form_errors %}
                            <div class="alert alert-danger">
                                {% for error in form_errors %}
                                <div>{{ error }}</div>
                                {% endfor %}
                            </div>
                            {% endif %}

                            <!-- Submit Button -->
                            <div class="d-grid gap-2">
                                <button type="submit" class="btn btn-primary btn-lg">
                                    Create Account
                                </button>
                                <button type="button" class="btn btn-outline-secondary" @click="reset_form">
                                    Reset Form
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """

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

    template_string = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ContactForm - Django Rust Live</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }
            .card { border-radius: 10px; }
            .card-header { border-radius: 10px 10px 0 0 !important; }
            .form-label { font-weight: 500; }
        </style>
    </head>
    <body>
    <div class="container">
        <div class="row justify-content-center mt-5">
            <div class="col-md-8">
                <div class="card shadow">
                    <div class="card-header bg-success text-white">
                        <h2 class="mb-0">Contact Us</h2>
                    </div>
                    <div class="card-body">
                        {% if success_message %}
                        <div class="alert alert-success alert-dismissible fade show" role="alert">
                            {{ success_message }}
                            <button type="button" class="btn-close" @click="clear_message"></button>
                        </div>
                        {% endif %}

                        {% if error_message %}
                        <div class="alert alert-danger" role="alert">
                            {{ error_message }}
                        </div>
                        {% endif %}

                        <form @submit="submit_form" class="needs-validation" novalidate>
                            <div class="row">
                                <!-- Name -->
                                <div class="col-md-6 mb-3">
                                    <label for="name" class="form-label">Name</label>
                                    <input
                                        type="text"
                                        name="name"
                                        id="name"
                                        class="form-control {% if field_errors.name %}is-invalid{% endif %}"
                                        value="{{ form_data.name }}"
                                        @change="validate_field"
                                        data-field="name"
                                        required
                                    />
                                    {% if field_errors.name %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.name %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>

                                <!-- Email -->
                                <div class="col-md-6 mb-3">
                                    <label for="email" class="form-label">Email</label>
                                    <input
                                        type="email"
                                        name="email"
                                        id="email"
                                        class="form-control {% if field_errors.email %}is-invalid{% endif %}"
                                        value="{{ form_data.email }}"
                                        @change="validate_field"
                                        data-field="email"
                                        required
                                    />
                                    {% if field_errors.email %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.email %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>
                            </div>

                            <!-- Subject -->
                            <div class="mb-3">
                                <label for="subject" class="form-label">Subject</label>
                                <select
                                    name="subject"
                                    id="subject"
                                    class="form-control {% if field_errors.subject %}is-invalid{% endif %}"
                                    @change="validate_field"
                                    data-field="subject"
                                    required
                                >
                                    <option value="">Select a subject...</option>
                                    <option value="general" {% if form_data.subject == "general" %}selected{% endif %}>General Inquiry</option>
                                    <option value="support" {% if form_data.subject == "support" %}selected{% endif %}>Technical Support</option>
                                    <option value="billing" {% if form_data.subject == "billing" %}selected{% endif %}>Billing Question</option>
                                    <option value="feedback" {% if form_data.subject == "feedback" %}selected{% endif %}>Feedback</option>
                                    <option value="other" {% if form_data.subject == "other" %}selected{% endif %}>Other</option>
                                </select>
                                {% if field_errors.subject %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.subject %}{{ error }}{% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Priority -->
                            <div class="mb-3">
                                <label class="form-label">Priority</label>
                                <div class="form-check">
                                    <input class="form-check-input" type="radio" name="priority" id="priority_low" value="low"
                                           {% if form_data.priority == "low" %}checked{% endif %}
                                           @change="validate_field" data-field="priority">
                                    <label class="form-check-label" for="priority_low">Low</label>
                                </div>
                                <div class="form-check">
                                    <input class="form-check-input" type="radio" name="priority" id="priority_medium" value="medium"
                                           {% if form_data.priority == "medium" or not form_data.priority %}checked{% endif %}
                                           @change="validate_field" data-field="priority">
                                    <label class="form-check-label" for="priority_medium">Medium</label>
                                </div>
                                <div class="form-check">
                                    <input class="form-check-input" type="radio" name="priority" id="priority_high" value="high"
                                           {% if form_data.priority == "high" %}checked{% endif %}
                                           @change="validate_field" data-field="priority">
                                    <label class="form-check-label" for="priority_high">High</label>
                                </div>
                                <div class="form-check">
                                    <input class="form-check-input" type="radio" name="priority" id="priority_urgent" value="urgent"
                                           {% if form_data.priority == "urgent" %}checked{% endif %}
                                           @change="validate_field" data-field="priority">
                                    <label class="form-check-label" for="priority_urgent">Urgent</label>
                                </div>
                            </div>

                            <!-- Message -->
                            <div class="mb-3">
                                <label for="message" class="form-label">Message</label>
                                <textarea
                                    name="message"
                                    id="message"
                                    class="form-control {% if field_errors.message %}is-invalid{% endif %}"
                                    rows="5"
                                    @change="validate_field"
                                    data-field="message"
                                    required
                                >{{ form_data.message }}</textarea>
                                <small class="form-text text-muted">Please provide details (minimum 10 characters)</small>
                                {% if field_errors.message %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.message %}{{ error }}{% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Newsletter -->
                            <div class="mb-3 form-check">
                                <input
                                    type="checkbox"
                                    name="subscribe_newsletter"
                                    id="subscribe_newsletter"
                                    class="form-check-input"
                                    {% if form_data.subscribe_newsletter %}checked{% endif %}
                                    @change="validate_field"
                                    data-field="subscribe_newsletter"
                                />
                                <label class="form-check-label" for="subscribe_newsletter">
                                    Subscribe to newsletter
                                </label>
                            </div>

                            <!-- Submit -->
                            <div class="d-grid gap-2">
                                <button type="submit" class="btn btn-success btn-lg">
                                    Send Message
                                </button>
                                <button type="button" class="btn btn-outline-secondary" @click="reset_form">
                                    Reset Form
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
    </body>
    </html>
    """

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

    template_string = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ProfileForm - Django Rust Live</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }
            .card { border-radius: 10px; }
            .card-header { border-radius: 10px 10px 0 0 !important; }
            .form-label { font-weight: 500; }
        </style>
    </head>
    <body>
    <div class="container">
        <div class="row justify-content-center mt-5">
            <div class="col-md-8">
                <div class="card shadow">
                    <div class="card-header bg-info text-white">
                        <h2 class="mb-0">Edit Profile</h2>
                    </div>
                    <div class="card-body">
                        {% if success_message %}
                        <div class="alert alert-success alert-dismissible fade show" role="alert">
                            {{ success_message }}
                            <button type="button" class="btn-close" @click="clear_message"></button>
                        </div>
                        {% endif %}

                        <form @submit="submit_form" class="needs-validation" novalidate>
                            <div class="row">
                                <!-- First Name -->
                                <div class="col-md-6 mb-3">
                                    <label for="first_name" class="form-label">First Name</label>
                                    <input
                                        type="text"
                                        name="first_name"
                                        id="first_name"
                                        class="form-control {% if field_errors.first_name %}is-invalid{% endif %}"
                                        value="{{ form_data.first_name }}"
                                        @change="validate_field"
                                        data-field="first_name"
                                        required
                                    />
                                    {% if field_errors.first_name %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.first_name %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>

                                <!-- Last Name -->
                                <div class="col-md-6 mb-3">
                                    <label for="last_name" class="form-label">Last Name</label>
                                    <input
                                        type="text"
                                        name="last_name"
                                        id="last_name"
                                        class="form-control {% if field_errors.last_name %}is-invalid{% endif %}"
                                        value="{{ form_data.last_name }}"
                                        @change="validate_field"
                                        data-field="last_name"
                                        required
                                    />
                                    {% if field_errors.last_name %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.last_name %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>
                            </div>

                            <!-- Bio -->
                            <div class="mb-3">
                                <label for="bio" class="form-label">Bio</label>
                                <textarea
                                    name="bio"
                                    id="bio"
                                    class="form-control {% if field_errors.bio %}is-invalid{% endif %}"
                                    rows="4"
                                    @change="validate_field"
                                    data-field="bio"
                                >{{ form_data.bio }}</textarea>
                                <small class="form-text text-muted">Tell us about yourself (max 500 characters)</small>
                                {% if field_errors.bio %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.bio %}{{ error }}{% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <div class="row">
                                <!-- Birth Date -->
                                <div class="col-md-6 mb-3">
                                    <label for="birth_date" class="form-label">Birth Date</label>
                                    <input
                                        type="date"
                                        name="birth_date"
                                        id="birth_date"
                                        class="form-control {% if field_errors.birth_date %}is-invalid{% endif %}"
                                        value="{{ form_data.birth_date }}"
                                        @change="validate_field"
                                        data-field="birth_date"
                                    />
                                    {% if field_errors.birth_date %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.birth_date %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>

                                <!-- Country -->
                                <div class="col-md-6 mb-3">
                                    <label for="country" class="form-label">Country</label>
                                    <select
                                        name="country"
                                        id="country"
                                        class="form-control {% if field_errors.country %}is-invalid{% endif %}"
                                        @change="validate_field"
                                        data-field="country"
                                    >
                                        <option value="">Select country...</option>
                                        <option value="US" {% if form_data.country == "US" %}selected{% endif %}>United States</option>
                                        <option value="UK" {% if form_data.country == "UK" %}selected{% endif %}>United Kingdom</option>
                                        <option value="CA" {% if form_data.country == "CA" %}selected{% endif %}>Canada</option>
                                        <option value="AU" {% if form_data.country == "AU" %}selected{% endif %}>Australia</option>
                                        <option value="DE" {% if form_data.country == "DE" %}selected{% endif %}>Germany</option>
                                        <option value="FR" {% if form_data.country == "FR" %}selected{% endif %}>France</option>
                                        <option value="JP" {% if form_data.country == "JP" %}selected{% endif %}>Japan</option>
                                        <option value="other" {% if form_data.country == "other" %}selected{% endif %}>Other</option>
                                    </select>
                                    {% if field_errors.country %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.country %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>
                            </div>

                            <div class="row">
                                <!-- Phone -->
                                <div class="col-md-6 mb-3">
                                    <label for="phone" class="form-label">Phone</label>
                                    <input
                                        type="text"
                                        name="phone"
                                        id="phone"
                                        class="form-control {% if field_errors.phone %}is-invalid{% endif %}"
                                        value="{{ form_data.phone }}"
                                        @change="validate_field"
                                        data-field="phone"
                                        placeholder="+1 (555) 123-4567"
                                    />
                                    <small class="form-text text-muted">Optional contact number</small>
                                    {% if field_errors.phone %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.phone %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>

                                <!-- Website -->
                                <div class="col-md-6 mb-3">
                                    <label for="website" class="form-label">Website</label>
                                    <input
                                        type="url"
                                        name="website"
                                        id="website"
                                        class="form-control {% if field_errors.website %}is-invalid{% endif %}"
                                        value="{{ form_data.website }}"
                                        @change="validate_field"
                                        data-field="website"
                                        placeholder="https://yourwebsite.com"
                                    />
                                    {% if field_errors.website %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.website %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>
                            </div>

                            <!-- Receive Updates -->
                            <div class="mb-3 form-check">
                                <input
                                    type="checkbox"
                                    name="receive_updates"
                                    id="receive_updates"
                                    class="form-check-input"
                                    {% if form_data.receive_updates %}checked{% endif %}
                                    @change="validate_field"
                                    data-field="receive_updates"
                                />
                                <label class="form-check-label" for="receive_updates">
                                    Receive email updates
                                </label>
                            </div>

                            <!-- Submit -->
                            <div class="d-grid gap-2">
                                <button type="submit" class="btn btn-info btn-lg text-white">
                                    Save Profile
                                </button>
                                <button type="button" class="btn btn-outline-secondary" @click="reset_form">
                                    Reset Form
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
    </body>
    </html>
    """

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
