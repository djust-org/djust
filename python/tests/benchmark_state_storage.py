"""
Benchmark script for state storage backends.

Tests:
- Memory backend vs Redis backend performance
- Serialization/deserialization costs
- Compression overhead
- Various state sizes (1KB, 10KB, 100KB, 1MB)
"""

import time
import json
import os
import sys
import statistics

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Check if zstd is available
try:
    import zstandard as zstd
    ZSTD_AVAILABLE = True
except ImportError:
    ZSTD_AVAILABLE = False

# Try to import Redis
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# Import test fixtures from djust
from djust._rust import RustLiveView


def generate_state(size_bytes: int) -> dict:
    """Generate a test state of approximately the specified size."""
    # Simple template that renders to roughly the desired size
    template = "<div>{{ content }}</div>"
    
    # Generate content that results in state of approximately the right size
    # Each character in content contributes roughly 1 byte to serialized state
    target_chars = max(100, size_bytes - 500)  # Account for overhead
    
    # Create content with some structure (like real app data)
    content_parts = []
    char_count = 0
    i = 0
    while char_count < target_chars:
        item = f"Item {i}: This is some sample content for testing purposes."
        content_parts.append(item)
        char_count += len(item)
        i += 1
    
    return {
        "template": template,
        "state": {
            "content": " ".join(content_parts),
            "items": [{"id": j, "name": f"Item {j}"} for j in range(min(100, i))],
            "metadata": {
                "created": "2024-01-15T10:30:00Z",
                "version": 1,
                "tags": ["test", "benchmark", "state"],
            }
        }
    }


def create_rust_view_with_state(state_data: dict) -> RustLiveView:
    """Create a RustLiveView with the given state."""
    view = RustLiveView(state_data["template"], [])
    view.update_state(state_data["state"])
    return view


def benchmark_memory_backend(iterations: int = 100, state_sizes: list = None):
    """Benchmark in-memory state backend operations."""
    from djust.state_backends.memory import InMemoryStateBackend
    
    if state_sizes is None:
        state_sizes = [1024, 10*1024, 100*1024, 1024*1024]  # 1KB, 10KB, 100KB, 1MB
    
    results = {}
    
    for size in state_sizes:
        size_label = f"{size // 1024}KB" if size >= 1024 else f"{size}B"
        
        # Generate test data
        state_data = generate_state(size)
        view = create_rust_view_with_state(state_data)
        
        # Measure serialized size
        serialized = view.serialize_msgpack()
        actual_size = len(serialized)
        
        backend = InMemoryStateBackend()
        
        # Benchmark SET operations
        set_times = []
        for i in range(iterations):
            key = f"test_key_{i}"
            start = time.perf_counter()
            backend.set(key, view, warn_on_large_state=False)
            set_times.append((time.perf_counter() - start) * 1000)  # ms
        
        # Benchmark GET operations  
        get_times = []
        for i in range(iterations):
            key = f"test_key_{i % iterations}"
            start = time.perf_counter()
            result = backend.get(key)
            get_times.append((time.perf_counter() - start) * 1000)  # ms
        
        results[size_label] = {
            "actual_size_bytes": actual_size,
            "actual_size_kb": round(actual_size / 1024, 2),
            "set_avg_ms": round(statistics.mean(set_times), 4),
            "set_p95_ms": round(sorted(set_times)[int(len(set_times) * 0.95)], 4),
            "get_avg_ms": round(statistics.mean(get_times), 4),
            "get_p95_ms": round(sorted(get_times)[int(len(get_times) * 0.95)], 4),
        }
        
        print(f"  {size_label}: set={results[size_label]['set_avg_ms']:.4f}ms, "
              f"get={results[size_label]['get_avg_ms']:.4f}ms, "
              f"actual_size={actual_size:,} bytes")
    
    return results


def benchmark_redis_backend(redis_url: str = "redis://localhost:6379/0",
                           iterations: int = 100, 
                           state_sizes: list = None):
    """Benchmark Redis state backend operations."""
    if not REDIS_AVAILABLE:
        print("  Redis not available - skipping")
        return None
    
    from djust.state_backends.redis import RedisStateBackend
    
    if state_sizes is None:
        state_sizes = [1024, 10*1024, 100*1024, 1024*1024]  # 1KB, 10KB, 100KB, 1MB
    
    # Test Redis connection
    try:
        backend = RedisStateBackend(redis_url, compression_enabled=True)
    except Exception as e:
        print(f"  Redis connection failed: {e}")
        return None
    
    results = {}
    
    for size in state_sizes:
        size_label = f"{size // 1024}KB" if size >= 1024 else f"{size}B"
        
        # Generate test data
        state_data = generate_state(size)
        view = create_rust_view_with_state(state_data)
        
        # Measure serialized size
        serialized = view.serialize_msgpack()
        actual_size = len(serialized)
        
        # Test compression manually
        compressed_size = actual_size
        if ZSTD_AVAILABLE and actual_size > 10 * 1024:  # > 10KB threshold
            compressor = zstd.ZstdCompressor(level=3)
            compressed = compressor.compress(serialized)
            compressed_size = len(compressed) + 1  # +1 for marker byte
        
        # Create fresh backend for each size test
        backend = RedisStateBackend(redis_url, compression_enabled=True)
        
        # Benchmark SET operations
        set_times = []
        for i in range(iterations):
            key = f"benchmark_test_{size}_{i}"
            start = time.perf_counter()
            backend.set(key, view)
            set_times.append((time.perf_counter() - start) * 1000)  # ms
        
        # Benchmark GET operations  
        get_times = []
        for i in range(iterations):
            key = f"benchmark_test_{size}_{i % iterations}"
            start = time.perf_counter()
            result = backend.get(key)
            get_times.append((time.perf_counter() - start) * 1000)  # ms
        
        # Cleanup test keys
        for i in range(iterations):
            backend.delete(f"benchmark_test_{size}_{i}")
        
        results[size_label] = {
            "actual_size_bytes": actual_size,
            "actual_size_kb": round(actual_size / 1024, 2),
            "compressed_size_bytes": compressed_size,
            "compressed_size_kb": round(compressed_size / 1024, 2),
            "compression_ratio": round(actual_size / compressed_size, 2) if compressed_size < actual_size else 1.0,
            "set_avg_ms": round(statistics.mean(set_times), 4),
            "set_p95_ms": round(sorted(set_times)[int(len(set_times) * 0.95)], 4),
            "get_avg_ms": round(statistics.mean(get_times), 4),
            "get_p95_ms": round(sorted(get_times)[int(len(get_times) * 0.95)], 4),
        }
        
        print(f"  {size_label}: set={results[size_label]['set_avg_ms']:.4f}ms, "
              f"get={results[size_label]['get_avg_ms']:.4f}ms, "
              f"compression={results[size_label]['compression_ratio']:.2f}x, "
              f"actual_size={actual_size:,}, compressed={compressed_size:,}")
    
    return results


def benchmark_serialization_only(iterations: int = 1000):
    """Benchmark just the serialization/deserialization overhead."""
    state_sizes = [1024, 10*1024, 100*1024, 1024*1024]  # 1KB, 10KB, 100KB, 1MB
    
    results = {}
    
    for size in state_sizes:
        size_label = f"{size // 1024}KB" if size >= 1024 else f"{size}B"
        
        # Generate test data
        state_data = generate_state(size)
        view = create_rust_view_with_state(state_data)
        
        # Benchmark serialization
        serialize_times = []
        for _ in range(iterations):
            start = time.perf_counter()
            serialized = view.serialize_msgpack()
            serialize_times.append((time.perf_counter() - start) * 1000)  # ms
        
        # Benchmark deserialization
        serialized = view.serialize_msgpack()
        actual_size = len(serialized)
        
        deserialize_times = []
        for _ in range(iterations):
            start = time.perf_counter()
            restored = RustLiveView.deserialize_msgpack(serialized)
            deserialize_times.append((time.perf_counter() - start) * 1000)  # ms
        
        results[size_label] = {
            "actual_size_bytes": actual_size,
            "serialize_avg_ms": round(statistics.mean(serialize_times), 4),
            "serialize_p95_ms": round(sorted(serialize_times)[int(len(serialize_times) * 0.95)], 4),
            "deserialize_avg_ms": round(statistics.mean(deserialize_times), 4),
            "deserialize_p95_ms": round(sorted(deserialize_times)[int(len(deserialize_times) * 0.95)], 4),
        }
        
        print(f"  {size_label}: serialize={results[size_label]['serialize_avg_ms']:.4f}ms, "
              f"deserialize={results[size_label]['deserialize_avg_ms']:.4f}ms, "
              f"size={actual_size:,} bytes")
    
    return results


def benchmark_compression_overhead(iterations: int = 1000):
    """Benchmark compression overhead vs space savings."""
    if not ZSTD_AVAILABLE:
        print("  zstd not available - skipping")
        return None
    
    state_sizes = [10*1024, 50*1024, 100*1024, 500*1024, 1024*1024]  # 10KB to 1MB
    
    results = {}
    compressor = zstd.ZstdCompressor(level=3)
    decompressor = zstd.ZstdDecompressor()
    
    for size in state_sizes:
        size_label = f"{size // 1024}KB" if size >= 1024 else f"{size}B"
        
        # Generate test data
        state_data = generate_state(size)
        view = create_rust_view_with_state(state_data)
        serialized = view.serialize_msgpack()
        actual_size = len(serialized)
        
        # Benchmark compression
        compress_times = []
        compressed_data = None
        for _ in range(iterations):
            start = time.perf_counter()
            compressed_data = compressor.compress(serialized)
            compress_times.append((time.perf_counter() - start) * 1000)  # ms
        
        compressed_size = len(compressed_data)
        
        # Benchmark decompression
        decompress_times = []
        for _ in range(iterations):
            start = time.perf_counter()
            decompressed = decompressor.decompress(compressed_data)
            decompress_times.append((time.perf_counter() - start) * 1000)  # ms
        
        results[size_label] = {
            "original_size_bytes": actual_size,
            "compressed_size_bytes": compressed_size,
            "compression_ratio": round(actual_size / compressed_size, 2),
            "space_saved_percent": round((1 - compressed_size / actual_size) * 100, 1),
            "compress_avg_ms": round(statistics.mean(compress_times), 4),
            "compress_p95_ms": round(sorted(compress_times)[int(len(compress_times) * 0.95)], 4),
            "decompress_avg_ms": round(statistics.mean(decompress_times), 4),
            "decompress_p95_ms": round(sorted(decompress_times)[int(len(decompress_times) * 0.95)], 4),
        }
        
        print(f"  {size_label}: ratio={results[size_label]['compression_ratio']:.2f}x "
              f"({results[size_label]['space_saved_percent']:.1f}% saved), "
              f"compress={results[size_label]['compress_avg_ms']:.4f}ms, "
              f"decompress={results[size_label]['decompress_avg_ms']:.4f}ms")
    
    return results


def estimate_scaling(state_size_kb: int = 10, users: list = None):
    """Estimate memory requirements for concurrent users."""
    if users is None:
        users = [100, 1000, 10000, 100000]
    
    state_data = generate_state(state_size_kb * 1024)
    view = create_rust_view_with_state(state_data)
    serialized = view.serialize_msgpack()
    actual_size = len(serialized)
    
    # Check compressed size if available
    compressed_size = actual_size
    if ZSTD_AVAILABLE:
        compressor = zstd.ZstdCompressor(level=3)
        compressed = compressor.compress(serialized)
        compressed_size = len(compressed)
    
    results = {}
    for user_count in users:
        memory_mb = (actual_size * user_count) / (1024 * 1024)
        compressed_memory_mb = (compressed_size * user_count) / (1024 * 1024)
        
        results[user_count] = {
            "uncompressed_mb": round(memory_mb, 2),
            "uncompressed_gb": round(memory_mb / 1024, 3),
            "compressed_mb": round(compressed_memory_mb, 2),
            "compressed_gb": round(compressed_memory_mb / 1024, 3),
        }
        
        print(f"  {user_count:,} users: "
              f"uncompressed={memory_mb:.1f}MB ({memory_mb/1024:.2f}GB), "
              f"compressed={compressed_memory_mb:.1f}MB ({compressed_memory_mb/1024:.2f}GB)")
    
    return results


def main():
    print("=" * 70)
    print("STATE STORAGE BENCHMARK")
    print("=" * 70)
    
    print(f"\nEnvironment:")
    print(f"  zstd available: {ZSTD_AVAILABLE}")
    print(f"  redis available: {REDIS_AVAILABLE}")
    
    print("\n" + "-" * 70)
    print("1. SERIALIZATION OVERHEAD (Rust MessagePack)")
    print("-" * 70)
    serialization_results = benchmark_serialization_only(iterations=500)
    
    print("\n" + "-" * 70)
    print("2. COMPRESSION OVERHEAD (zstd level=3)")
    print("-" * 70)
    compression_results = benchmark_compression_overhead(iterations=500)
    
    print("\n" + "-" * 70)
    print("3. MEMORY BACKEND (set/get operations)")
    print("-" * 70)
    memory_results = benchmark_memory_backend(iterations=100)
    
    print("\n" + "-" * 70)
    print("4. REDIS BACKEND (set/get with compression)")
    print("-" * 70)
    redis_results = benchmark_redis_backend(iterations=100)
    
    print("\n" + "-" * 70)
    print("5. MEMORY SCALING ESTIMATES (10KB avg state)")
    print("-" * 70)
    scaling_results = estimate_scaling(state_size_kb=10)
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    # Summary analysis
    if serialization_results:
        print("\nSerialization (Rust MessagePack):")
        for size, data in serialization_results.items():
            throughput = data['actual_size_bytes'] / (data['serialize_avg_ms'] / 1000) / (1024 * 1024)
            print(f"  {size}: {throughput:.1f} MB/s serialize, "
                  f"{data['actual_size_bytes'] / (data['deserialize_avg_ms'] / 1000) / (1024 * 1024):.1f} MB/s deserialize")
    
    if compression_results:
        print("\nCompression (zstd level=3):")
        for size, data in compression_results.items():
            print(f"  {size}: {data['compression_ratio']:.2f}x ratio, "
                  f"{data['space_saved_percent']:.0f}% saved")
    
    if memory_results and redis_results:
        print("\nBackend Comparison (100KB state):")
        memory_100kb = memory_results.get("100KB", {})
        redis_100kb = redis_results.get("100KB", {}) if redis_results else {}
        
        if memory_100kb:
            print(f"  Memory: set={memory_100kb.get('set_avg_ms', 'N/A')}ms, "
                  f"get={memory_100kb.get('get_avg_ms', 'N/A')}ms")
        if redis_100kb:
            print(f"  Redis:  set={redis_100kb.get('set_avg_ms', 'N/A')}ms, "
                  f"get={redis_100kb.get('get_avg_ms', 'N/A')}ms, "
                  f"compression={redis_100kb.get('compression_ratio', 'N/A')}x")
    
    # Return all results for programmatic use
    return {
        "serialization": serialization_results,
        "compression": compression_results,
        "memory_backend": memory_results,
        "redis_backend": redis_results,
        "scaling": scaling_results,
    }


if __name__ == "__main__":
    results = main()
