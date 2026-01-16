"""
Demo forms for showcasing Django Rust Live forms integration
"""

from django import forms
from djust import LiveViewForm


class RegistrationForm(LiveViewForm):
    """User registration form with real-time validation"""

    username = forms.CharField(
        max_length=150,
        min_length=3,
        required=True,
        help_text="Username must be 3-150 characters",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Choose a username'
        })
    )

    email = forms.EmailField(
        required=True,
        help_text="We'll never share your email",
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'your.email@example.com'
        })
    )

    password = forms.CharField(
        min_length=8,
        required=True,
        help_text="Password must be at least 8 characters",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter password'
        })
    )

    password_confirm = forms.CharField(
        min_length=8,
        required=True,
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm password'
        })
    )

    agree_terms = forms.BooleanField(
        required=True,
        label="I agree to the Terms and Conditions",
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )

    def clean_username(self):
        """Validate username"""
        username = self.cleaned_data.get('username')
        if username:
            # Check for invalid characters
            if not username.isalnum() and '_' not in username:
                raise forms.ValidationError(
                    "Username can only contain letters, numbers, and underscores"
                )
            # Simulate checking if username exists
            if username.lower() in ['admin', 'root', 'test', 'user']:
                raise forms.ValidationError(
                    "This username is already taken"
                )
        return username

    def clean_email(self):
        """Validate email"""
        email = self.cleaned_data.get('email')
        if email:
            # Simulate checking if email exists
            if email.lower() in ['admin@example.com', 'test@example.com']:
                raise forms.ValidationError(
                    "This email is already registered"
                )
        return email

    def clean(self):
        """Validate entire form"""
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')

        if password and password_confirm:
            if password != password_confirm:
                raise forms.ValidationError(
                    "Passwords do not match"
                )

        return cleaned_data


class ContactForm(LiveViewForm):
    """Contact form with various field types"""

    name = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Your name'
        })
    )

    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'your.email@example.com'
        })
    )

    subject = forms.ChoiceField(
        choices=[
            ('', 'Select a subject...'),
            ('general', 'General Inquiry'),
            ('support', 'Technical Support'),
            ('billing', 'Billing Question'),
            ('feedback', 'Feedback'),
            ('other', 'Other'),
        ],
        required=True,
        widget=forms.Select(attrs={
            'class': 'form-control'
        })
    )

    priority = forms.ChoiceField(
        choices=[
            ('low', 'Low'),
            ('medium', 'Medium'),
            ('high', 'High'),
            ('urgent', 'Urgent'),
        ],
        initial='medium',
        required=True,
        widget=forms.RadioSelect(attrs={
            'class': 'form-check-input'
        })
    )

    message = forms.CharField(
        min_length=10,
        required=True,
        help_text="Please provide details (minimum 10 characters)",
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 5,
            'placeholder': 'Enter your message here...'
        })
    )

    subscribe_newsletter = forms.BooleanField(
        required=False,
        initial=True,
        label="Subscribe to newsletter",
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )

    def clean_message(self):
        """Validate message"""
        message = self.cleaned_data.get('message')
        if message:
            # Check for spam-like content
            spam_words = ['spam', 'viagra', 'casino', 'lottery']
            if any(word in message.lower() for word in spam_words):
                raise forms.ValidationError(
                    "Your message appears to contain spam content"
                )
        return message


class ProfileForm(LiveViewForm):
    """User profile form with various field types"""

    first_name = forms.CharField(
        max_length=50,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'First name'
        })
    )

    last_name = forms.CharField(
        max_length=50,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Last name'
        })
    )

    bio = forms.CharField(
        required=False,
        max_length=500,
        help_text="Tell us about yourself (max 500 characters)",
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Your bio...'
        })
    )

    birth_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'class': 'form-control',
            'type': 'date'
        })
    )

    country = forms.ChoiceField(
        choices=[
            ('', 'Select country...'),
            ('US', 'United States'),
            ('UK', 'United Kingdom'),
            ('CA', 'Canada'),
            ('AU', 'Australia'),
            ('DE', 'Germany'),
            ('FR', 'France'),
            ('JP', 'Japan'),
            ('other', 'Other'),
        ],
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-control'
        })
    )

    phone = forms.CharField(
        max_length=20,
        required=False,
        help_text="Optional contact number",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '+1 (555) 123-4567'
        })
    )

    website = forms.URLField(
        required=False,
        widget=forms.URLInput(attrs={
            'class': 'form-control',
            'placeholder': 'https://yourwebsite.com'
        })
    )

    receive_updates = forms.BooleanField(
        required=False,
        initial=True,
        label="Receive email updates",
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )

    def clean_phone(self):
        """Validate phone number"""
        phone = self.cleaned_data.get('phone')
        if phone:
            # Remove common formatting characters
            cleaned = ''.join(c for c in phone if c.isdigit() or c == '+')
            if len(cleaned) < 10:
                raise forms.ValidationError(
                    "Please enter a valid phone number"
                )
        return phone


class SimpleContactForm(LiveViewForm):
    """Simplified contact form with just name, email, and message"""

    name = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Your name'
        })
    )

    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'your.email@example.com'
        })
    )

    message = forms.CharField(
        min_length=10,
        required=True,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Enter your message here...'
        })
    )

    def clean_message(self):
        """Validate message"""
        message = self.cleaned_data.get('message')
        if message:
            # Check for spam-like content
            spam_words = ['spam', 'viagra', 'casino', 'lottery']
            if any(word in message.lower() for word in spam_words):
                raise forms.ValidationError(
                    "Your message appears to contain spam content"
                )
        return message


class SearchForm(LiveViewForm):
    """Simple search form"""

    query = forms.CharField(
        max_length=200,
        required=True,
        label="Search",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search...'
        })
    )

    category = forms.ChoiceField(
        choices=[
            ('all', 'All Categories'),
            ('products', 'Products'),
            ('articles', 'Articles'),
            ('users', 'Users'),
        ],
        initial='all',
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-control'
        })
    )

    sort_by = forms.ChoiceField(
        choices=[
            ('relevance', 'Relevance'),
            ('date', 'Date'),
            ('popularity', 'Popularity'),
            ('rating', 'Rating'),
        ],
        initial='relevance',
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-control'
        })
    )
