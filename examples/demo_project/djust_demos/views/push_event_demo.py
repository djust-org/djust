"""
Demo: push_event() — Server → Client JS Events

Shows:
1. Button that triggers confetti animation via push_event
2. Form save that shows a flash notification
3. Chat-like auto-scroll to latest message
"""

import random
from djust import LiveView


class PushEventDemoView(LiveView):
    template_name = "demos/push_event_demo.html"

    def mount(self, request, **kwargs):
        self.messages = []
        self.note_text = ""
        self.message_counter = 0

    def handle_celebrate(self):
        """Push a confetti event to the client."""
        self.push_event("confetti", {
            "count": random.randint(50, 150),
            "colors": ["#ff0", "#0ff", "#f0f", "#0f0"],
        })

    def handle_save_note(self, text=""):
        """Save a note and flash a success message."""
        self.note_text = text
        self.push_event("djust:flash", {
            "message": f"Note saved! ({len(text)} chars)",
            "type": "success",
            "duration": 2500,
        })

    def handle_send_message(self, content=""):
        """Add a chat message and scroll to it."""
        if not content.strip():
            return
        self.message_counter += 1
        msg_id = f"msg-{self.message_counter}"
        self.messages.append({
            "id": msg_id,
            "content": content,
            "sender": "You",
        })
        # Scroll to the new message
        self.push_event("djust:scroll_to", {
            "selector": f"#{msg_id}",
            "behavior": "smooth",
            "block": "end",
        })

    def handle_copy_to_clipboard(self):
        """Copy some text to clipboard."""
        self.push_event("djust:clipboard", {
            "text": "Hello from djust push_event! 🎉",
        })
        self.push_event("djust:flash", {
            "message": "Copied to clipboard!",
            "type": "info",
            "duration": 2000,
        })

    def handle_focus_input(self):
        """Focus and select the note input."""
        self.push_event("djust:focus", {
            "selector": "#note-input",
            "select": True,
        })
