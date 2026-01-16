# djust - Demo Project

This demo project showcases three reactive LiveView examples.

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Rust 1.70+ (to build the Rust extension)

### Setup

#### Option 1: Using uv (Recommended)

```bash
# From the project root (djust/)
cd examples/demo_project

# Install uv if you haven't
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies and build Rust extension
cd ../..  # Back to project root
uv pip install maturin
maturin develop --release

# Back to demo project
cd examples/demo_project

# Install Django dependencies
uv pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Start development server
python manage.py runserver
```

#### Option 2: Using pip

```bash
# From the project root (djust/)
cd examples/demo_project

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies and build Rust extension
cd ../..  # Back to project root
pip install maturin
maturin develop --release

# Back to demo project
cd examples/demo_project

# Install Django dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Start development server
python manage.py runserver
```

### Access the Demo

Open your browser to: **http://localhost:8000**

You'll see a landing page with links to three demos:

## 📊 Demos

### 1. Counter (`/counter/`)
**Features:**
- Increment/Decrement buttons
- Reset functionality
- Instant reactive updates

**Code highlights:**
```python
class CounterView(LiveView):
    def mount(self, request, **kwargs):
        self.count = 0

    def increment(self):
        self.count += 1  # Auto-updates client!
```

### 2. Todo List (`/todo/`)
**Features:**
- Add new todos
- Toggle completion status
- Delete items
- Active/completed statistics

**Demonstrates:**
- List manipulation
- Form handling
- Conditional rendering
- Computed properties

### 3. Chat (`/chat/`)
**Features:**
- Send messages
- Timestamped messages
- User identification

**Demonstrates:**
- Real-time updates
- Message history
- Form submission

## 🔧 For Development

### Run with ASGI server (for WebSocket support)

```bash
# Install daphne
pip install daphne

# Run with daphne
daphne -b 0.0.0.0 -p 8000 demo_project.asgi:application
```

### Watch for changes

```bash
# Run with auto-reload
python manage.py runserver --noreload  # Or use daphne with watchfiles
```

## 📝 Notes

- The current implementation uses HTTP POST for events (WebSocket coming soon)
- State is maintained in memory (session storage would be production-ready)
- The Rust extension provides 10-100x faster rendering than pure Django

## 🎯 Next Steps

1. Check out the source code in `demo_app/views.py`
2. Modify the templates to see instant updates
3. Add your own LiveView components
4. Run benchmarks: `cd ../../benchmarks && python benchmark.py`

## 🐛 Troubleshooting

**"ModuleNotFoundError: No module named '_rust'"**
- Make sure you built the Rust extension: `maturin develop --release`
- Run from the project root, not the examples directory

**"No module named 'channels'"**
- Install requirements: `pip install -r requirements.txt`

**Port already in use**
- Try a different port: `python manage.py runserver 8001`

## 📚 Documentation

See the main [README.md](../../README.md) for full documentation.
