"""
LiveForm Demo — Contact form with live inline validation.

Shows how to use LiveForm for Phoenix-style changeset validation
without requiring Django Forms.

Usage:
    # In your views.py
    from djust import LiveView
    from djust.decorators import event_handler
    from djust.forms import LiveForm

    class ContactView(LiveView):
        template_name = "contact.html"

        def mount(self, request, **kwargs):
            self.form = LiveForm({
                "name": {"required": True, "min_length": 2},
                "email": {"required": True, "email": True},
                "subject": {"required": True, "choices": ["general", "support", "sales"]},
                "message": {
                    "required": True,
                    "min_length": 10,
                    "max_length": 500,
                    "validators": [
                        lambda v: "Please don't include URLs" if v and "http" in v.lower() else None,
                    ],
                },
            })
            self.submitted = False

        @event_handler
        def validate(self, field=None, value=None, **kwargs):
            \"\"\"Called on blur/change for live inline validation.\"\"\"
            self.form.validate_field(field, value)

        @event_handler
        def submit_form(self, **kwargs):
            \"\"\"Handle form submission.\"\"\"
            self.form.set_values(kwargs)
            if self.form.validate_all():
                # Process the form data
                print(f"Form submitted: {self.form.data}")
                self.submitted = True
                self.form.reset()
                self.push_event("flash", {"message": "Message sent!", "type": "success"})
            # Errors auto-display via template


# Template: contact.html
TEMPLATE = '''
<div class="max-w-lg mx-auto p-6">
    <h1 class="text-2xl font-bold mb-6">Contact Us</h1>

    {% if submitted %}
        <div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded mb-4">
            Your message has been sent!
        </div>
    {% endif %}

    <form dj-submit="submit_form" class="space-y-4">
        <!-- Name -->
        <div>
            <label class="block text-sm font-medium mb-1">Name</label>
            <input name="name" type="text"
                   dj-change="validate" dj-debounce="300"
                   class="w-full px-3 py-2 border rounded {% if form.errors.name %}border-red-500{% else %}border-gray-300{% endif %}"
                   value="{{ form.data.name }}" />
            {% if form.errors.name %}
                <span class="text-red-400 text-sm">{{ form.errors.name }}</span>
            {% endif %}
        </div>

        <!-- Email -->
        <div>
            <label class="block text-sm font-medium mb-1">Email</label>
            <input name="email" type="email"
                   dj-change="validate" dj-debounce="300"
                   class="w-full px-3 py-2 border rounded {% if form.errors.email %}border-red-500{% else %}border-gray-300{% endif %}"
                   value="{{ form.data.email }}" />
            {% if form.errors.email %}
                <span class="text-red-400 text-sm">{{ form.errors.email }}</span>
            {% endif %}
        </div>

        <!-- Subject -->
        <div>
            <label class="block text-sm font-medium mb-1">Subject</label>
            <select name="subject"
                    dj-change="validate"
                    class="w-full px-3 py-2 border rounded {% if form.errors.subject %}border-red-500{% else %}border-gray-300{% endif %}">
                <option value="">Select a subject...</option>
                <option value="general" {% if form.data.subject == "general" %}selected{% endif %}>General Inquiry</option>
                <option value="support" {% if form.data.subject == "support" %}selected{% endif %}>Support</option>
                <option value="sales" {% if form.data.subject == "sales" %}selected{% endif %}>Sales</option>
            </select>
            {% if form.errors.subject %}
                <span class="text-red-400 text-sm">{{ form.errors.subject }}</span>
            {% endif %}
        </div>

        <!-- Message -->
        <div>
            <label class="block text-sm font-medium mb-1">Message</label>
            <textarea name="message" rows="4"
                      dj-change="validate" dj-debounce="500"
                      class="w-full px-3 py-2 border rounded {% if form.errors.message %}border-red-500{% else %}border-gray-300{% endif %}">{{ form.data.message }}</textarea>
            {% if form.errors.message %}
                <span class="text-red-400 text-sm">{{ form.errors.message }}</span>
            {% endif %}
        </div>

        <!-- Submit -->
        <button type="submit"
                class="px-4 py-2 rounded text-white {% if form.valid %}bg-blue-600 hover:bg-blue-700{% else %}bg-gray-400 cursor-not-allowed{% endif %}"
                {% if not form.valid %}disabled{% endif %}>
            Send Message
        </button>
    </form>
</div>
'''
