"""
Presence Tracking Demo - Shows real-time presence and live cursors

This demo showcases djust's presence tracking system, including:
- Real-time presence tracking ("3 users viewing this page")
- User avatars with colors
- Live cursor tracking (bonus feature)
- Presence join/leave notifications
"""

from djust import LiveView, PresenceMixin, LiveCursorMixin
from djust_shared.components.ui import CodeBlock
import random


class PresenceDemo:
    """
    Presence tracking demo with real-time user awareness.
    """

    PYTHON_CODE = '''class DocumentView(LiveView, PresenceMixin):
    presence_key = "document:{doc_id}"  # Group key
    
    def mount(self, request, **kwargs):
        self.doc_id = kwargs.get("doc_id", "demo")
        # Auto-track this user's presence
        self.track_presence(meta={
            "name": request.user.username if request.user.is_authenticated else "Anonymous",
            "color": self.get_random_color()
        })
    
    def get_context_data(self):
        ctx = super().get_context_data()
        ctx["presences"] = self.list_presences()
        ctx["presence_count"] = self.presence_count()
        return ctx
    
    def handle_presence_join(self, presence):
        self.push_event("flash", {
            "message": f"{presence['meta']['name']} joined", 
            "type": "info"
        })
    
    def handle_presence_leave(self, presence):
        self.push_event("flash", {
            "message": f"{presence['meta']['name']} left", 
            "type": "info"
        })'''

    HTML_CODE = '''<div class="presence-demo">
    <div class="presence-bar">
        <h3>{{ presence_count }} user{{ presence_count|pluralize }} online</h3>
        <div class="user-avatars">
            {% for p in presences %}
                <span class="avatar" 
                      style="background: {{ p.meta.color }}"
                      title="{{ p.meta.name }}">
                    {{ p.meta.name.0|upper }}
                </span>
            {% endfor %}
        </div>
    </div>
    
    <div class="demo-content">
        <h4>Collaborative Space</h4>
        <p>Open this page in multiple tabs or browsers to see presence tracking in action!</p>
        
        <div class="editor-mockup" style="position: relative;">
            <textarea placeholder="Start typing to simulate collaboration..."
                      style="width: 100%; height: 200px; padding: 10px;"></textarea>
            <!-- Live cursors would appear here -->
        </div>
    </div>
</div>'''

    CSS_CODE = '''<style>
.presence-bar {
    background: #f8f9fa;
    padding: 1rem;
    border-radius: 8px;
    margin-bottom: 1rem;
    border-left: 4px solid #007bff;
}

.user-avatars {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.5rem;
}

.avatar {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    font-weight: bold;
    font-size: 12px;
    border: 2px solid #fff;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.editor-mockup {
    background: #fff;
    border: 1px solid #ddd;
    border-radius: 4px;
    position: relative;
}

.live-cursor {
    position: absolute;
    width: 2px;
    height: 20px;
    z-index: 10;
    pointer-events: none;
}

.live-cursor::before {
    content: '';
    position: absolute;
    top: -4px;
    left: -3px;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: inherit;
}

.live-cursor::after {
    content: attr(data-user);
    position: absolute;
    top: -6px;
    left: 8px;
    font-size: 11px;
    background: inherit;
    color: white;
    padding: 2px 6px;
    border-radius: 3px;
    white-space: nowrap;
}

@keyframes presence-join {
    0% { transform: scale(0); opacity: 0; }
    50% { transform: scale(1.1); }
    100% { transform: scale(1); opacity: 1; }
}

.avatar.new {
    animation: presence-join 0.3s ease-out;
}

.demo-content h4 {
    margin: 0 0 0.5rem;
    color: #333;
}

.demo-content p {
    color: #666;
    margin-bottom: 1rem;
}
</style>'''

    COLORS = [
        "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7",
        "#DDA0DD", "#98D8C8", "#F7DC6F", "#BB8FCE", "#85C1E9"
    ]

    def __init__(self, view):
        """Initialize presence demo"""
        self.view = view

        # Initialize demo state
        if not hasattr(view, 'demo_doc_id'):
            view.demo_doc_id = "demo"
        
        if not hasattr(view, 'flash_messages'):
            view.flash_messages = []

    def mount(self, request, **kwargs):
        """Mount the presence demo"""
        # Track presence for this user
        user_name = request.user.username if request.user.is_authenticated else f"User{random.randint(1000, 9999)}"
        color = random.choice(self.COLORS)
        
        # Set up presence tracking
        if hasattr(self.view, 'track_presence'):
            self.view.track_presence(meta={
                "name": user_name,
                "color": color
            })

    def handle_presence_join(self, presence):
        """Handle when a user joins"""
        if hasattr(self.view, 'push_event'):
            self.view.push_event("flash", {
                "message": f"{presence['meta']['name']} joined the demo",
                "type": "success"
            })

    def handle_presence_leave(self, presence):
        """Handle when a user leaves"""
        if hasattr(self.view, 'push_event'):
            self.view.push_event("flash", {
                "message": f"{presence['meta']['name']} left the demo",
                "type": "info"
            })

    def simulate_activity(self):
        """Simulate collaborative activity"""
        activities = [
            "Someone is typing...",
            "Document saved automatically",
            "New comment added",
            "Changes synchronized"
        ]
        
        message = random.choice(activities)
        if hasattr(self.view, 'push_event'):
            self.view.push_event("activity", {"message": message})

    def get_context(self):
        """Return context for template rendering"""
        # Create code block components
        code_python = CodeBlock(
            code=self.PYTHON_CODE,
            language="python",
            filename="views.py",
            show_header=False
        )
        
        code_html = CodeBlock(
            code=self.HTML_CODE,
            language="html",
            filename="template.html",
            show_header=False
        )
        
        code_css = CodeBlock(
            code=self.CSS_CODE,
            language="css",
            filename="styles.css",
            show_header=False
        )

        # Get presence data
        presences = []
        presence_count = 0
        
        if hasattr(self.view, 'list_presences'):
            presences = self.view.list_presences()
            presence_count = len(presences)

        return {
            'presences': presences,
            'presence_count': presence_count,
            'demo_doc_id': self.view.demo_doc_id,
            'presence_code_python': code_python,
            'presence_code_html': code_html,
            'presence_code_css': code_css,
            'flash_messages': getattr(self.view, 'flash_messages', []),
        }


class LiveCursorPresenceDemo(PresenceDemo):
    """
    Enhanced presence demo with live cursor tracking.
    """

    LIVE_CURSOR_JS = '''<script>
// Live cursor tracking
let lastCursorUpdate = 0;
const CURSOR_THROTTLE = 100; // ms

document.addEventListener('mousemove', (e) => {
    const now = Date.now();
    if (now - lastCursorUpdate < CURSOR_THROTTLE) return;
    
    lastCursorUpdate = now;
    
    // Send cursor position to server
    if (window.djustWebSocket) {
        window.djustWebSocket.send(JSON.stringify({
            type: 'cursor_move',
            x: e.clientX,
            y: e.clientY
        }));
    }
});

// Handle cursor updates from other users
document.addEventListener('djust:cursor_move', (e) => {
    const { user_id, x, y, meta } = e.detail.payload;
    
    // Skip our own cursor
    if (user_id === window.currentUserId) return;
    
    // Update or create cursor element
    let cursor = document.getElementById(`cursor-${user_id}`);
    if (!cursor) {
        cursor = document.createElement('div');
        cursor.id = `cursor-${user_id}`;
        cursor.className = 'live-cursor';
        cursor.style.backgroundColor = meta.color;
        cursor.setAttribute('data-user', meta.name);
        document.body.appendChild(cursor);
    }
    
    // Update position
    cursor.style.left = x + 'px';
    cursor.style.top = y + 'px';
    
    // Auto-hide after inactivity
    clearTimeout(cursor.hideTimeout);
    cursor.hideTimeout = setTimeout(() => {
        if (cursor.parentNode) {
            cursor.parentNode.removeChild(cursor);
        }
    }, 5000);
});

// Clean up cursors when users leave
document.addEventListener('djust:presence_leave', (e) => {
    const { user_id } = e.detail.payload;
    const cursor = document.getElementById(`cursor-${user_id}`);
    if (cursor && cursor.parentNode) {
        cursor.parentNode.removeChild(cursor);
    }
});
</script>'''

    def handle_cursor_move(self, x, y):
        """Handle cursor movement"""
        if hasattr(self.view, 'update_cursor_position'):
            self.view.update_cursor_position(x, y)

    def get_context(self):
        """Return context with live cursor support"""
        context = super().get_context()
        
        # Add live cursor JavaScript
        context['live_cursor_js'] = self.LIVE_CURSOR_JS
        
        # Get cursor data
        cursors = {}
        if hasattr(self.view, 'get_cursors'):
            cursors = self.view.get_cursors()
        
        context['cursors'] = cursors
        
        return context


# Demo view class for the demo project
class PresenceDemoView(LiveView, PresenceMixin):
    """Demo view showing presence tracking"""
    
    template_name = 'presence_demo.html'
    presence_key = "presence_demo"
    
    def mount(self, request, **kwargs):
        self.demo = PresenceDemo(self)
        self.demo.mount(request, **kwargs)
    
    def handle_presence_join(self, presence):
        self.demo.handle_presence_join(presence)
    
    def handle_presence_leave(self, presence):
        self.demo.handle_presence_leave(presence)
    
    def simulate_activity(self):
        """Handler for simulating activity"""
        self.demo.simulate_activity()
    
    def get_context_data(self):
        ctx = super().get_context_data()
        ctx.update(self.demo.get_context())
        return ctx


class LiveCursorDemoView(LiveView, LiveCursorMixin):
    """Demo view with live cursors"""
    
    template_name = 'live_cursor_demo.html'
    presence_key = "live_cursor_demo"
    
    def mount(self, request, **kwargs):
        self.demo = LiveCursorPresenceDemo(self)
        self.demo.mount(request, **kwargs)
    
    def handle_presence_join(self, presence):
        self.demo.handle_presence_join(presence)
    
    def handle_presence_leave(self, presence):
        self.demo.handle_presence_leave(presence)
    
    def handle_cursor_move(self, x, y):
        self.demo.handle_cursor_move(x, y)
    
    def get_context_data(self):
        ctx = super().get_context_data()
        ctx.update(self.demo.get_context())
        return ctx