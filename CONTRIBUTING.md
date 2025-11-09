# Contributing to djust

Thank you for your interest in contributing! We welcome contributions from everyone.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/djust.git`
3. Create a branch: `git checkout -b feature/your-feature-name`
4. Make your changes
5. Run tests: `cargo test && pytest`
6. Commit: `git commit -m "Add your feature"`
7. Push: `git push origin feature/your-feature-name`
8. Open a Pull Request

## Development Setup

### Prerequisites

- Python 3.8+
- Rust 1.70+
- Django 3.2+

### Environment Setup

```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements-dev.txt
pip install maturin

# Build the Rust extension
maturin develop
```

## Code Style

### Python
- Follow PEP 8
- Use type hints where possible
- Run `black` for formatting
- Use `ruff` for linting

```bash
black python/
ruff check python/
mypy python/
```

### Rust
- Follow standard Rust conventions
- Run `cargo fmt` before committing
- Use `cargo clippy` for linting

```bash
cargo fmt
cargo clippy -- -D warnings
```

## Testing

### Rust Tests
```bash
cargo test
cargo test --release  # Run with optimizations
```

### Python Tests
```bash
pytest
pytest --cov=djust  # With coverage
```

### Integration Tests
```bash
cd examples/demo_project
python manage.py test
```

### Benchmarks
```bash
cd benchmarks
python benchmark.py
```

## Pull Request Guidelines

- Keep PRs focused on a single feature or fix
- Write clear commit messages
- Add tests for new features
- Update documentation as needed
- Ensure all tests pass
- Add yourself to CONTRIBUTORS.md

## Documentation

- Code comments for complex logic
- Docstrings for public APIs
- Update README.md for new features
- Add examples when appropriate

## Performance

- Profile before optimizing
- Run benchmarks to verify improvements
- Consider memory usage
- Document performance characteristics

## Areas for Contribution

### High Priority
- Additional Django template filters and tags
- More comprehensive test coverage
- Performance optimizations
- Documentation improvements

### Medium Priority
- Additional example applications
- Browser compatibility testing
- Error message improvements
- Accessibility features

### Future Features
- Template inheritance support
- Server-sent events fallback
- Redis session backend
- TypeScript definitions

## Questions?

- Open a discussion on GitHub
- Join our Discord: https://discord.gg/djust
- Email: dev@djust.org

## Code of Conduct

Be respectful, inclusive, and professional. We're all here to build great software together.

Thank you for contributing! 🚀
