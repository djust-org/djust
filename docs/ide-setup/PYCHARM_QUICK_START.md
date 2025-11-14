# PyCharm Quick Start - Essential Setup

**Time to complete**: ~10 minutes

## 🚨 Important Note About Rust Support

**PyCharm has limited Rust support.** For the best experience with this Rust + Python hybrid project:

**→ Use IntelliJ IDEA Ultimate instead** (see [IDE_SETUP_RUST.md](IDE_SETUP_RUST.md))

IntelliJ IDEA Ultimate includes:
- ✅ Full Rust plugin support (syntax, debugging, Cargo integration)
- ✅ Complete Python support (same as PyCharm Professional)
- ✅ Django support (templates, models, management commands)

**Quick start**: Run `./scripts/open_intellij.sh` to open the project in IntelliJ IDEA.

---

**If you prefer to continue with PyCharm**, follow the setup below:

## ⚡ Must-Do (5 minutes)

### 1. ~~Install Rust Plugin~~ (Not well-supported in PyCharm)
**Recommended**: Use IntelliJ IDEA for Rust files instead.

**Alternative**: Edit Rust files in PyCharm but use terminal for cargo commands.

### 2. Increase Memory (if 16GB+ RAM)
```
Help → Change Memory Settings
Set: 4096 MB
```

### 3. Enable Django Support (PyCharm Professional)
```
Settings → Languages & Frameworks → Django
☑ Enable Django Support
Django project root: examples/demo_project
Settings: demo_project/settings.py
```

### 4. Configure Django Run Config
```
Run → Edit Configurations → + → Django Server
Name: djust Demo
Port: 8002
Working directory: examples/demo_project
```

## 🎯 Should-Do (5 minutes)

### 5. Install TOML Plugin
```
Settings → Plugins → Search "TOML" → Install
```

### 6. Configure External Tool: Make Build
```
Settings → Tools → External Tools → +
Name: Make Build
Program: make
Arguments: build
Working directory: $ProjectFileDir$
```

### 7. Set Default Test Runner
```
Settings → Tools → Python Integrated Tools
Default test runner: pytest
```

## 🎨 Nice-to-Have (Optional)

### 8. Install GitToolBox
```
Settings → Plugins → Search "GitToolBox" → Install
```

### 9. Install Rainbow Brackets
```
Settings → Plugins → Search "Rainbow Brackets" → Install
```

### 10. Enable Font Ligatures
```
Settings → Editor → Font
Font: JetBrains Mono
☑ Enable ligatures
```

## ✅ Verify Setup

### Test Rust Plugin
1. Open `crates/djust_vdom/src/lib.rs`
2. Should see syntax highlighting
3. Hover over a function → should see documentation

### Test Django Config
1. Run → djust Demo
2. Should start server at http://localhost:8002
3. Should see output in Run panel

### Test Make Integration
1. Tools → External Tools → Make Build
2. Should compile Rust code
3. Should see cargo output

## 📖 Full Documentation

See [PYCHARM_SETUP.md](PYCHARM_SETUP.md) for complete configuration guide.

## 🚀 You're Ready!

With these essentials configured, you can now:
- ✅ Edit Rust code with full IDE support
- ✅ Run Django server with one click
- ✅ Build project from IDE
- ✅ Debug Python and Rust code
- ✅ Navigate between Python ↔ Rust seamlessly
