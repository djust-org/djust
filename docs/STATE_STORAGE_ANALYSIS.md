# State Storage Analysis

## Executive Summary

This document analyzes the current state storage architecture in djust, benchmarks performance characteristics, and provides recommendations for different deployment scenarios.

**Key Findings:**
- Current architecture is well-designed with pluggable backends (memory/Redis)
- Rust MessagePack serialization achieves **4000-10000 MB/s throughput**
- Memory backend operations are sub-microsecond (~0.0002ms)
- zstd compression provides **2-4x size reduction** with minimal overhead
- For 10,000 concurrent users with 10KB avg state: **~100MB memory required**

**Recommendations:**
1. Memory backend is excellent for single-server deployments up to ~50,000 concurrent users
2. Redis backend is essential for horizontal scaling and persistence
3. Client-side state is viable for non-sensitive UI state via signed cookies
4. Hybrid approach (memory cache + Redis persistence) recommended for high-traffic production

---

## 1. Current Architecture Overview

### 1.1 What State Is Stored

The `RustLiveView` class (Rust/PyO3) stores:

```rust
struct RustLiveViewBackend {
    template_source: String,       // Django template source
    state: HashMap<String, Value>, // Template context variables
    last_vdom: Option<VNode>,      // Previous render's Virtual DOM (for diffing)
    version: u64,                  // Render version counter
    timestamp: f64,                // Last access time (for TTL)
    template_dirs: Vec<PathBuf>,   // NOT serialized (restored on load)
}
```

**Serialized via MessagePack:**
- Template source string
- State dictionary (context variables)
- VDOM tree (for efficient patching)
- Version and timestamp

**NOT serialized (reconstructed on load):**
- `template_dirs` - restored via `set_template_dirs()`
- Template cache - global static cache, shared across sessions

### 1.2 Backend Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      StateBackend ABC                        │
│  get(), set(), delete(), cleanup_expired(), health_check()  │
└─────────────────────────────────────────────────────────────┘
                    ▲                       ▲
                    │                       │
    ┌───────────────┴──────┐    ┌──────────┴───────────────┐
    │ InMemoryStateBackend │    │    RedisStateBackend     │
    │  - Dict storage      │    │  - Redis storage         │
    │  - Thread-safe       │    │  - MessagePack + zstd    │
    │  - No persistence    │    │  - TTL auto-expiry       │
    └──────────────────────┘    └──────────────────────────┘
```

### 1.3 Data Flow

```
HTTP/WebSocket Request
        │
        ▼
┌─────────────────────────┐
│  LiveView.get/handle()  │
└───────────┬─────────────┘
            │ get_backend()
            ▼
┌─────────────────────────┐
│    backend.get(key)     │ ◄── Redis: msgpack + zstd decompress
└───────────┬─────────────┘     Memory: direct dict lookup
            │
            ▼
┌─────────────────────────┐
│ RustLiveView.update_state() │
│ RustLiveView.render_with_diff() │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│    backend.set(key)     │ ◄── Redis: msgpack + zstd compress
└─────────────────────────┘     Memory: direct dict store
```

---

## 2. Benchmark Results

### 2.1 Serialization Performance (Rust MessagePack)

| State Size | Serialized Size | Serialize | Deserialize |
|------------|-----------------|-----------|-------------|
| 1KB input  | 1KB             | 0.002ms (603 MB/s) | 0.013ms (80 MB/s) |
| 10KB input | 9KB             | 0.002ms (4,178 MB/s) | 0.036ms (266 MB/s) |
| 100KB input| 97KB            | 0.010ms (9,087 MB/s) | 0.282ms (338 MB/s) |
| 500KB input| 488KB           | 0.048ms (10,023 MB/s) | 1.316ms (362 MB/s) |

**Key Observations:**
- Serialization is extremely fast (streaming, minimal overhead)
- Deserialization is ~10-30x slower (must reconstruct full object graph)
- Performance scales linearly with state size
- 500KB state can still be deserialized in ~1.3ms

### 2.2 Memory Backend Performance

| State Size | SET Operation | GET Operation |
|------------|---------------|---------------|
| 1KB        | 0.0002ms      | 0.0001ms      |
| 10KB       | 0.0002ms      | 0.0001ms      |
| 100KB      | 0.0002ms      | 0.0001ms      |

**Key Observations:**
- Sub-microsecond operations regardless of state size
- No serialization overhead (stores reference directly)
- Limited only by Python dict operations

### 2.3 Compression Analysis (zstd level=3)

| Original Size | Compressed | Ratio | Space Saved |
|---------------|------------|-------|-------------|
| 10KB          | ~6KB       | 1.7x  | 40%         |
| 50KB          | ~15KB      | 3.3x  | 70%         |
| 100KB         | ~28KB      | 3.6x  | 72%         |
| 500KB         | ~125KB     | 4.0x  | 75%         |

**Compression Overhead (estimated):**
- Compress: 0.05-0.3ms for 10KB-500KB
- Decompress: 0.02-0.1ms for 10KB-500KB
- Threshold at 10KB is well-chosen (smaller states don't benefit much)

### 2.4 Memory Scaling Estimates

For average 10KB state per user (typical LiveView):

| Concurrent Users | Memory (Uncompressed) | Memory (Compressed) |
|-----------------|----------------------|---------------------|
| 100             | 1.0 MB               | ~0.5 MB             |
| 1,000           | 9.6 MB               | ~4.8 MB             |
| 10,000          | 96.4 MB              | ~48 MB              |
| 100,000         | 963.8 MB             | ~480 MB             |

**Key Observations:**
- Memory usage is very manageable even at scale
- Compression roughly halves memory usage for Redis
- 100K concurrent users = ~1GB memory (uncompressed)

---

## 3. Analysis of Alternative Approaches

### 3.1 Can Template Context Be Reconstructed from DB?

**Current State:**
```python
# What's stored:
{
    "leases": [...],      # Serialized queryset results
    "user_name": "John",  # Cached values
    "count": 42,          # UI state
}
```

**Reconstruction Approach:**
```python
class LeaseView(LiveView):
    # Instead of storing entire queryset, store query parameters
    _query_params: dict = {}
    
    def mount(self, request):
        self._query_params = {"status": "active", "user_id": request.user.id}
        
    def get_context_data(self):
        # Reconstruct from DB on each request
        return {
            "leases": Lease.objects.filter(**self._query_params),
        }
```

**Trade-offs:**

| Aspect | Store Full State | Reconstruct from DB |
|--------|-----------------|---------------------|
| Memory | Higher (data duplication) | Lower (just query params) |
| DB Load | Low (cached) | Higher (queries each request) |
| Latency | Faster (no DB) | Slower (DB roundtrip) |
| Staleness | Possible (cached data) | Always fresh |
| Complexity | Simpler | More complex |

**Recommendation:** 
- Use reconstruction for **large collections** (100+ items)
- Keep **small UI state** in session (counts, flags, selections)
- Use `temporary_assigns` and Streams API for best of both

### 3.2 Client-Side State (Signed Cookies, JWT)

**What's Safe to Store Client-Side:**
```javascript
// Safe: Non-sensitive UI state
{
    "current_tab": "details",
    "sort_order": "name_asc",
    "collapsed_sections": ["billing", "history"],
    "preferences": {"theme": "dark", "page_size": 25}
}
```

**What Should NEVER Be Client-Side:**
```javascript
// NEVER: Security-sensitive data
{
    "user_id": 123,           // Can be manipulated
    "permissions": ["admin"], // Can be manipulated
    "session_secret": "...",  // Exposes internals
    "db_results": [...]       // Data leakage
}
```

**Implementation Options:**

1. **Signed Cookies (Django's signing framework)**
```python
from django.core import signing

class ClientStateMixin:
    def get_client_state(self, request):
        try:
            return signing.loads(request.COOKIES.get("djust_ui_state", ""))
        except signing.BadSignature:
            return {}
    
    def set_client_state(self, response, state):
        signed = signing.dumps(state)
        response.set_signed_cookie("djust_ui_state", signed, max_age=86400)
```

2. **JWT Tokens (for API-heavy apps)**
```python
import jwt

def encode_client_state(state: dict, secret: str) -> str:
    return jwt.encode({"ui": state, "exp": time.time() + 3600}, secret)

def decode_client_state(token: str, secret: str) -> dict:
    return jwt.decode(token, secret)["ui"]
```

**Limitations:**
- Cookie size limit: 4KB (after encoding/signing)
- Adds ~0.5KB per request overhead
- State visible to client (even if signed)

**Recommendation:**
- Good for: UI preferences, form wizard position, collapse states
- Implement as optional mixin for views that need it
- Keep primary state server-side for security

### 3.3 Hybrid Approach: Memory + Redis

**Proposed Architecture:**
```
                       ┌─────────────────┐
                       │   L1: Memory    │ ◄── Hot data, fast access
                       │  (process-local)│     TTL: 60 seconds
                       └────────┬────────┘
                                │ miss?
                                ▼
                       ┌─────────────────┐
                       │   L2: Redis     │ ◄── Warm data, shared
                       │   (compressed)  │     TTL: 1 hour
                       └────────┬────────┘
                                │ miss?
                                ▼
                       ┌─────────────────┐
                       │ Reconstruct     │ ◄── Cold start
                       │  from DB        │
                       └─────────────────┘
```

**Implementation Sketch:**
```python
class HybridStateBackend(StateBackend):
    def __init__(self, redis_url, memory_ttl=60, redis_ttl=3600):
        self._memory = InMemoryStateBackend(default_ttl=memory_ttl)
        self._redis = RedisStateBackend(redis_url, default_ttl=redis_ttl)
        
    def get(self, key):
        # Try memory first
        result = self._memory.get(key)
        if result:
            return result
            
        # Fall back to Redis
        result = self._redis.get(key)
        if result:
            # Promote to memory cache
            view, timestamp = result
            self._memory.set(key, view)
            return result
            
        return None
        
    def set(self, key, view, ttl=None):
        # Write to both
        self._memory.set(key, view)
        self._redis.set(key, view, ttl)
```

**Benefits:**
- Fast local access for active sessions (~0.0002ms)
- Horizontal scaling via Redis
- Graceful degradation if Redis is slow/unavailable
- Reduced Redis load (only misses and writes)

**Trade-offs:**
- More complex consistency model
- Memory overhead on each server
- Potential stale data between servers (acceptable for ~60s)

---

## 4. Recommendations by Deployment Scenario

### 4.1 Development / Single Server (< 1,000 users)

**Use:** `InMemoryStateBackend`

```python
DJUST_CONFIG = {
    "STATE_BACKEND": "memory",
    "SESSION_TTL": 3600,
    "STATE_SIZE_WARNING_KB": 100,
}
```

**Rationale:**
- Simplest setup
- Fastest performance
- No external dependencies
- Acceptable memory usage (< 10MB for 1K users)

### 4.2 Small Production (1,000-10,000 users, single server)

**Use:** `InMemoryStateBackend` with monitoring

```python
DJUST_CONFIG = {
    "STATE_BACKEND": "memory",
    "SESSION_TTL": 1800,  # 30 min
    "STATE_SIZE_WARNING_KB": 50,  # Tighter warning
}

# Add monitoring
LOGGING = {
    "handlers": {"djust": {"class": "..."}},
    "loggers": {"djust.state_backends": {"level": "WARNING"}},
}
```

**Rationale:**
- Still simple and fast
- ~100MB memory for 10K users
- Monitor for large state warnings
- Consider Redis if state size grows

### 4.3 Medium Production (10,000-100,000 users, multiple servers)

**Use:** `RedisStateBackend` with compression

```python
DJUST_CONFIG = {
    "STATE_BACKEND": "redis",
    "REDIS_URL": "redis://redis-cluster:6379/0",
    "SESSION_TTL": 1800,
    "COMPRESSION_ENABLED": True,
    "COMPRESSION_THRESHOLD_KB": 10,
    "COMPRESSION_LEVEL": 3,
}
```

**Rationale:**
- Horizontal scaling
- Persistent state (survives deploys)
- ~500MB Redis memory for 100K users (compressed)
- Use Redis Cluster for HA

### 4.4 Large Production (100,000+ users)

**Use:** Hybrid approach + state reduction

```python
DJUST_CONFIG = {
    "STATE_BACKEND": "hybrid",  # Proposed new backend
    "REDIS_URL": "redis://redis-cluster:6379/0",
    "MEMORY_TTL": 60,
    "REDIS_TTL": 1800,
    "COMPRESSION_ENABLED": True,
}
```

**Additional optimizations:**
1. Use `temporary_assigns` for large collections
2. Use Streams API for real-time data
3. Implement query reconstruction for large querysets
4. Consider client-side state for UI preferences

---

## 5. Future Improvement Opportunities

### 5.1 Short-term (v0.5-v0.6)

1. **Implement HybridStateBackend**
   - Memory L1 + Redis L2
   - Configurable TTLs
   - Automatic promotion/demotion

2. **Client-side state mixin**
   - Signed cookie storage
   - Configurable per-view
   - Size limit enforcement

3. **State size monitoring dashboard**
   - Per-view statistics
   - Largest sessions alerts
   - Memory trending

### 5.2 Medium-term (v0.7+)

1. **Selective state serialization**
   - Only serialize changed fields
   - Delta compression between versions
   - Lazy loading of large state sections

2. **State reconstruction framework**
   - Declarative query definitions
   - Automatic cache invalidation
   - Hybrid fresh + cached data

3. **Alternative backends**
   - PostgreSQL (for existing DB infrastructure)
   - KeyDB (Redis-compatible, multi-threaded)
   - In-memory distributed (Hazelcast, Ignite)

### 5.3 Long-term

1. **WebAssembly state management**
   - Move more state to client
   - Signed state blobs
   - Server validates, client renders

2. **Event sourcing option**
   - Store events, not state
   - Rebuild state on demand
   - Better for audit/debugging

---

## 6. Appendix: Benchmark Code

See `python/tests/benchmark_state_storage.py` for the full benchmark suite.

Quick run:
```bash
cd djust-experimental
uv run python python/tests/benchmark_state_storage.py
```

---

## 7. Conclusion

The current state storage architecture is well-designed and performant. The main optimization opportunities are:

1. **For most deployments:** Memory backend is sufficient and fastest
2. **For scaling:** Redis with compression provides excellent horizontal scaling
3. **For large state:** Use temporary_assigns, Streams, and query reconstruction
4. **For future:** Hybrid backend and client-side state provide additional options

The framework already handles most use cases well. The proposed enhancements (hybrid backend, client-side state mixin) would provide additional flexibility for edge cases without complicating the common path.
