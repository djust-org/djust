"""
djust Performance Benchmarks

This package contains pytest-benchmark tests for measuring performance
of key djust components:

- Template rendering
- Serialization (code generation and execution)
- Rust-Python interop (tag handlers)

Run with: pytest tests/benchmarks/ -v --benchmark-only
Compare: pytest tests/benchmarks/ -v --benchmark-compare
"""
