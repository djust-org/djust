"""
LiveForm Demo — Standalone form validation with real-time feedback.

Demonstrates:
    - LiveForm for declarative validation rules
    - Real-time validation as user types (dj-change + dj-debounce)
    - Error messages shown inline
    - Submit button disabled until valid
    - Custom validators
    - Password strength indicator
"""

from djust import LiveView
from djust.decorators import event_handler
from djust.forms import LiveForm


class LiveFormDemoView(LiveView):
    """
    Registration form with comprehensive real-time validation.
    
    Features:
    - Required field validation
    - Email format validation
    - Password strength checking (custom validator)
    - Password confirmation matching
    - Terms acceptance
    - Success state after submission
    """
    
    template_name = "demos/live_form_demo.html"

    def mount(self, request, **kwargs):
        """Initialize the form with validation rules."""
        self.form = LiveForm({
            "username": {
                "required": True,
                "min_length": 3,
                "max_length": 20,
                "pattern": r"^[a-zA-Z0-9_]+$",
                "validators": [
                    lambda v: "Username already taken" if v and v.lower() in ["admin", "root", "user"] else None,
                ],
            },
            "email": {
                "required": True,
                "email": True,
            },
            "password": {
                "required": True,
                "min_length": 8,
                "validators": [
                    self._validate_password_strength,
                ],
            },
            "password_confirm": {
                "required": True,
                "validators": [
                    self._validate_password_match,
                ],
            },
            "age": {
                "required": True,
                "min": 13,
                "max": 120,
            },
            "website": {
                "url": True,  # Optional, but if provided must be valid URL
            },
            "bio": {
                "max_length": 500,
            },
            "terms": {
                "required": True,
                "validators": [
                    lambda v: "You must accept the terms" if not v or v == "false" else None,
                ],
            },
        })
        
        self.submitted = False
        self.submitted_data = {}
        self.password_strength = 0
        self.password_feedback = []

    def _validate_password_strength(self, password):
        """Check password strength and return error if too weak."""
        if not password:
            return None
        
        score = 0
        feedback = []
        
        # Length check
        if len(password) >= 8:
            score += 1
        else:
            feedback.append("At least 8 characters")
        
        # Uppercase
        if any(c.isupper() for c in password):
            score += 1
        else:
            feedback.append("Add uppercase letter")
        
        # Lowercase
        if any(c.islower() for c in password):
            score += 1
        else:
            feedback.append("Add lowercase letter")
        
        # Number
        if any(c.isdigit() for c in password):
            score += 1
        else:
            feedback.append("Add a number")
        
        # Special char
        if any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
            score += 1
        else:
            feedback.append("Add special character")
        
        # Store for template display
        self.password_strength = score
        self.password_feedback = feedback
        
        if score < 3:
            return "Password too weak"
        
        return None

    def _validate_password_match(self, confirm):
        """Validate that password confirmation matches."""
        if not confirm:
            return None
        password = self.form.data.get("password", "")
        if confirm != password:
            return "Passwords do not match"
        return None

    def get_context_data(self):
        ctx = super().get_context_data()
        ctx.update({
            "form": self.form,
            "submitted": self.submitted,
            "submitted_data": self.submitted_data,
            "password_strength": self.password_strength,
            "password_feedback": self.password_feedback,
        })
        return ctx

    @event_handler
    def validate(self, field=None, value=None, **kwargs):
        """
        Called on input change for live inline validation.
        
        The dj-change attribute triggers this, with field name and value
        automatically passed.
        """
        if field:
            # Handle checkbox specially
            if field == "terms":
                value = str(value).lower() in ("true", "on", "1", "yes")
            
            self.form.validate_field(field, value)
            
            # Re-validate password confirm if password changed
            if field == "password" and self.form.data.get("password_confirm"):
                self.form.validate_field("password_confirm")

    @event_handler
    def submit_form(self, **kwargs):
        """Handle form submission."""
        # Set all values from the submission
        self.form.set_values(kwargs)
        
        # Handle checkbox
        self.form.set_values({"terms": kwargs.get("terms", "false")})
        
        # Validate all fields
        if self.form.validate_all():
            # Form is valid - process submission
            self.submitted = True
            self.submitted_data = {
                "username": self.form.data["username"],
                "email": self.form.data["email"],
                "age": self.form.data["age"],
                "website": self.form.data.get("website", ""),
                "bio": self.form.data.get("bio", ""),
            }
            
            # Reset form after successful submission
            self.form.reset()
            self.password_strength = 0
            self.password_feedback = []
            
            # Push success event for toast notification
            self.push_event("toast", {
                "message": "Registration successful!",
                "type": "success",
            })

    @event_handler
    def reset_form(self, **kwargs):
        """Reset the form to initial state."""
        self.form.reset()
        self.submitted = False
        self.submitted_data = {}
        self.password_strength = 0
        self.password_feedback = []

    @event_handler  
    def dismiss_success(self, **kwargs):
        """Dismiss the success message."""
        self.submitted = False
        self.submitted_data = {}
