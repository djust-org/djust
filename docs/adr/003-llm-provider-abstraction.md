# ADR-003: LLM Provider Abstraction for AssistantMixin

**Status**: Proposed
**Date**: 2026-04-11
**Deciders**: Project maintainers
**Target version**: v0.5.x (lands with `AssistantMixin`)
**Related**: [ADR-002](002-backend-driven-ui-automation.md), [ADR-004](004-undo-for-llm-driven-actions.md)

---

## Summary

`AssistantMixin` needs to talk to an LLM. The LLM could be OpenAI's GPT-5, Anthropic's Claude, a locally-hosted model via Ollama, or something else entirely. Each provider has a different tool-calling API shape, different streaming semantics, different error models, and different pricing signals. djust apps should not be locked into any one of them.

This ADR proposes a thin `Provider` protocol in `djust.assistant.providers` that normalizes tool calling across OpenAI, Anthropic, and a local-model reference implementation, along with the conventions for third-party adapters. Adapters convert between djust's internal representation (tool schemas generated from `@event_handler`, plans represented as an ordered sequence of tool calls) and each vendor's native API. Apps configure a provider once at view-class level and never think about vendor differences again.

## Context

### Why we can't just pick one

The obvious alternative is "pick OpenAI, call it a day." This fails for several reasons:

1. **Strategic risk.** Tying djust's AI story to one vendor means every shift in that vendor's pricing, availability, or terms of service propagates directly to every djust app using the assistant layer. Phoenix framework doesn't lock users into any one LLM; we shouldn't either.
2. **Enterprise procurement.** Many orgs have pre-existing vendor relationships (Azure OpenAI for one, AWS Bedrock for another, self-hosted Llama for another). A framework that only works with direct-OpenAI requires those orgs to either switch vendors or abandon djust's assistant layer.
3. **Regulatory environments.** Some industries require on-prem inference (healthcare, government, EU data residency). A cloud-only story excludes those customers.
4. **Cost and latency tuning.** Different model tiers have different tradeoffs. An app might use a small local model for trivial intent classification and escalate to a frontier model for complex plans. Hard to do without a provider abstraction.
5. **Future-proofing.** The provider landscape is actively consolidating and re-fragmenting. Two years ago "function calling" meant OpenAI's syntax. Today there are three major vendor schemas plus a handful of local-model conventions. Two years from now there will be a different mix. An abstraction buys us time to follow the market without breaking apps.

### Why the abstraction is small

Despite all the provider differences, the *shape* of LLM tool calling has converged:

1. **Inputs** — a system prompt, a list of messages (user/assistant/tool roles), and a list of tool definitions with JSON-schema parameters.
2. **Outputs** — either a text response, or a list of tool calls (name + JSON args), or both.
3. **Streaming** — incremental token or tool-call delta events.
4. **Errors** — rate limits, auth failures, schema validation failures, content filter blocks.

Every major provider speaks this shape with minor syntactic differences. The abstraction is almost entirely a translator, not a semantic layer. We're not hiding meaningful capability differences; we're hiding field names and request envelope details.

### What's not in scope

- **Model capability discovery.** The abstraction does not pretend GPT-5 and a tiny local model are interchangeable. App authors pick the model; the framework passes it through. If the model can't handle tool calls at all, the provider adapter raises a clear error up front.
- **Prompt optimization.** The framework does not rewrite prompts for each provider. Apps write one prompt (generated from `describe_ui()` or hand-written) and pass it through. If a provider wants a slightly different system-prompt format, the adapter handles it invisibly.
- **Embeddings, moderation, fine-tuning.** Out of scope. This abstraction is specifically about the "chat with tools" API surface. Other LLM capabilities go through separate helpers or direct vendor SDK calls.
- **RAG / vector search.** Out of scope. If an app wants retrieval-augmented generation, that's an orthogonal concern it builds alongside the assistant.

## Proposed API

### Provider protocol

```python
# python/djust/assistant/providers/base.py
from typing import Protocol, runtime_checkable, List, Dict, Optional, AsyncIterator, Any
from dataclasses import dataclass, field


@dataclass
class Message:
    """One message in a conversation history."""
    role: str                                    # "system" | "user" | "assistant" | "tool"
    content: Optional[str] = None                # text content
    tool_calls: Optional[List["ToolCall"]] = None  # set when role == "assistant"
    tool_call_id: Optional[str] = None           # set when role == "tool" (the call being answered)
    name: Optional[str] = None                   # tool name for role == "tool"


@dataclass
class ToolCall:
    """A structured request from the model to invoke a tool."""
    id: str                                      # provider-issued unique id
    name: str                                    # handler name
    args: Dict[str, Any]                         # handler kwargs
    narration: Optional[str] = None              # model's explanation for this call


@dataclass
class Tool:
    """A djust-side tool definition (auto-generated from @event_handler)."""
    name: str
    description: str
    parameters: Dict[str, Any]                   # JSON Schema object
    destructive: bool = False                    # from @destructive decorator


@dataclass
class ChatResponse:
    """One completed LLM turn."""
    text: Optional[str]                          # the assistant's text response (may be None)
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = ""                      # "stop" | "tool_calls" | "length" | "content_filter"
    usage: Optional["Usage"] = None              # token / cost accounting


@dataclass
class Usage:
    """Token accounting, normalized across providers."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Cost attribution — providers that report cost directly fill this in;
    # for providers that don't, the app can estimate from token counts.
    estimated_cost_usd: Optional[float] = None


@dataclass
class StreamChunk:
    """One incremental update from a streaming chat response."""
    kind: str                                    # "text" | "tool_call_start" | "tool_call_delta" | "tool_call_end" | "done"
    text: Optional[str] = None                   # populated for "text" chunks
    tool_call: Optional[ToolCall] = None         # populated for tool_call_* chunks
    usage: Optional[Usage] = None                # populated on "done"
    finish_reason: Optional[str] = None          # populated on "done"


@runtime_checkable
class Provider(Protocol):
    """Every LLM provider adapter implements this protocol."""

    @property
    def name(self) -> str:
        """Stable identifier, e.g. 'openai', 'anthropic', 'ollama'."""
        ...

    def chat(
        self,
        *,
        system: str,
        messages: List[Message],
        tools: Optional[List[Tool]] = None,
        model: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> ChatResponse:
        """Blocking chat call. Returns the full response."""
        ...

    def stream(
        self,
        *,
        system: str,
        messages: List[Message],
        tools: Optional[List[Tool]] = None,
        model: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[StreamChunk]:
        """Streaming chat call. Yields chunks as they arrive."""
        ...
```

That's the entire surface. Every provider adapter implements `chat` and `stream`. Everything else — OpenAI's `functions` vs `tools` parameter, Anthropic's `input_schema` field naming, Ollama's lack of tool calling on small models — is adapter-internal.

### App-facing API

```python
# python/djust/assistant/mixin.py
from djust.assistant.providers import Provider, OpenAIProvider

class AssistantMixin:
    """Mixed into a LiveView to enable LLM-driven interaction."""

    assistant_provider: Provider = None          # set by app at class level
    assistant_model: str = "gpt-5"                # provider-specific model string
    assistant_max_steps: int = 10                 # safety cap on plan length
    assistant_step_delay: float = 0.6             # seconds between tool calls
    assistant_destructive_confirm: bool = True    # require UI confirm for destructive ops

    async def handle_speech(self, transcript: str, **kwargs):
        """Default entry point. Apps can override or call directly."""
        ...
```

Configuring is a one-liner at the view:

```python
from djust import LiveView
from djust.assistant import AssistantMixin
from djust.assistant.providers import OpenAIProvider, AnthropicProvider
from django.conf import settings

class ProjectView(LiveView, AssistantMixin):
    # Pick one. No other changes needed in the app code.
    assistant_provider = OpenAIProvider(api_key=settings.OPENAI_API_KEY)
    assistant_model = "gpt-5"
    # ...
```

Or swap Anthropic in with no other changes:

```python
class ProjectView(LiveView, AssistantMixin):
    assistant_provider = AnthropicProvider(api_key=settings.ANTHROPIC_API_KEY)
    assistant_model = "claude-opus-4-6"
```

### Shipping adapters

The djust core package ships **two reference adapters** plus a **mock adapter for tests**:

| Adapter | Package | Dependencies | Use case |
|---|---|---|---|
| `OpenAIProvider` | `djust[assistant-openai]` | `openai>=1.50` | Direct OpenAI, Azure OpenAI (via endpoint override), OpenRouter (via endpoint override) |
| `AnthropicProvider` | `djust[assistant-anthropic]` | `anthropic>=0.40` | Direct Anthropic, Bedrock Claude (via bedrock-runtime override) |
| `MockProvider` | always available | none | unit tests, deterministic plans |

Additional adapters live in third-party packages and register via entry points:

```toml
# In the third-party package's pyproject.toml
[project.entry-points."djust.assistant.providers"]
ollama = "djust_assistant_ollama:OllamaProvider"
```

At runtime, `djust.assistant.providers.registry` discovers entry points and exposes them via `get_provider("ollama")`. Apps can then use providers without importing their implementation modules directly.

### Tool schema generation

The framework auto-generates tool schemas from `@event_handler` decorators via `self.get_handler_schema()` (defined in [ADR-002](002-backend-driven-ui-automation.md)). The output is a list of `Tool` dataclasses — provider-agnostic. Each adapter translates the list into its vendor's format:

- **OpenAI adapter** → `[{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]`
- **Anthropic adapter** → `[{"name": ..., "description": ..., "input_schema": ...}]`
- **Ollama adapter** → depends on model capability; for Llama 3.1+ with native tool support, uses the standard OpenAI-compatible format; for others, falls back to JSON-in-prompt with adapter-level parsing.

The adapter is the only place that cares about vendor field names. App code and the mixin only see `Tool`.

### Tool call execution

```python
# Inside AssistantMixin.execute_plan
for call in response.tool_calls:
    tool = self._find_handler(call.name)
    if tool is None:
        self._narrate(f"I don't know how to do '{call.name}'. Skipping.")
        continue
    if tool.destructive and self.assistant_destructive_confirm:
        confirmed = await self._confirm_destructive(tool, call.args, call.narration)
        if not confirmed:
            self._narrate(f"Cancelled '{call.name}'.")
            continue
    if call.narration:
        self._narrate(call.narration)
    # Execute — this is a regular djust @event_handler call, so everything
    # that normally happens on an event (optimistic updates, rate limiting,
    # undo logging) happens automatically.
    await self._call_handler(tool.name, call.args)
    # Respond to the model with the result (for multi-turn plans)
    result_message = Message(
        role="tool",
        content=self._serialize_state_for_model(),
        tool_call_id=call.id,
        name=call.name,
    )
    self._conversation.append(result_message)
```

The tool-result message is important: after every handler call, the LLM gets a snapshot of the new view state so it can plan follow-up calls based on what actually happened (not what it predicted would happen). This is the standard multi-turn tool calling pattern.

## Provider differences worth handling explicitly

The abstraction is deliberately thin, but a few provider differences are substantial enough that the adapter has to handle them rather than paper over them:

### 1. Tool call result format

- **OpenAI** expects tool results as messages with `role: "tool"`, `tool_call_id`, and `content` (string).
- **Anthropic** expects tool results as user messages with a specific content block shape: `{"type": "tool_result", "tool_use_id": ..., "content": ...}`.

The adapter handles the translation. From the app's perspective, it's always `Message(role="tool", tool_call_id=..., content=...)`.

### 2. System prompt placement

- **OpenAI** accepts a `system` role message in the `messages` array.
- **Anthropic** takes the system prompt as a separate top-level `system` parameter, not in `messages`.

The `chat()` signature accepts `system` as its own argument so apps don't think about where it lives on the wire.

### 3. Streaming tool call semantics

- **OpenAI** streams tool calls as incremental deltas on `choices[0].delta.tool_calls[i]` — you have to accumulate the `function.arguments` string across multiple chunks before parsing it as JSON.
- **Anthropic** streams tool calls as `content_block_start` with a `tool_use` block, followed by `input_json_delta` events on that block, ending with `content_block_stop`.

The adapter normalizes both into the framework's `StreamChunk` stream:
- `kind="tool_call_start"` with the tool call's `id` and `name`
- `kind="tool_call_delta"` with partial args (as they arrive)
- `kind="tool_call_end"` with the fully-accumulated `ToolCall`

Apps that want to start executing a tool call before the LLM finishes streaming subsequent ones can inspect `tool_call_end` chunks as they arrive. Simpler apps can just `async for chunk in ...` until `done` and then look at `chunk.tool_call` on each end chunk.

### 4. Error normalization

Every provider reports errors differently. The adapter raises a small set of djust-internal exceptions regardless of source:

```python
# python/djust/assistant/errors.py
class ProviderError(Exception): ...
class ProviderAuthError(ProviderError): ...          # invalid api key, expired token
class ProviderRateLimitError(ProviderError): ...     # 429, token bucket exhausted
class ProviderTimeoutError(ProviderError): ...       # request timeout
class ProviderSchemaError(ProviderError): ...        # model returned invalid tool args
class ProviderContentFilterError(ProviderError): ...  # model refused on safety grounds
class ProviderUnavailableError(ProviderError): ...   # 503, provider down
class ProviderConfigError(ProviderError): ...        # missing api key, bad model name
```

The `AssistantMixin` handles these centrally: rate-limit errors trigger backoff, auth errors surface a one-time admin notification, content filter errors narrate "I can't help with that" to the user, timeouts fall back to a simpler plan (or abort gracefully).

Apps that need provider-specific error handling can still catch the raw vendor exceptions inside a handler, but the default flow normalizes to the djust exceptions above.

### 5. Cost attribution

Every provider reports usage differently (and some don't report it at all). The adapter normalizes to `Usage(prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd)`. For providers that don't report cost directly, the adapter maintains a per-model price table and estimates. The price table is a plain Python dict that apps can override to reflect their actual billing:

```python
from djust.assistant.providers.pricing import set_price

set_price("gpt-5", prompt_per_1k=0.005, completion_per_1k=0.015)
set_price("claude-opus-4-6", prompt_per_1k=0.015, completion_per_1k=0.075)
```

Framework system checks warn if `assistant_provider` is set but no pricing entry exists for the chosen model, so cost tracking isn't silently off-by-default.

### 6. Content safety / moderation

Some providers inline content safety checks; others require a separate API call. The abstraction does not prescribe a moderation step — apps that need it add it before or after `chat()` using whatever provider API they prefer. A future ADR could address moderation as a framework concern if it becomes a repeat request, but baking it into the core abstraction now would overcommit.

## What this looks like for app authors

Three code samples covering the common cases:

### Simple case: one provider, single turn

```python
from djust import LiveView
from djust.assistant import AssistantMixin
from djust.assistant.providers import AnthropicProvider
from django.conf import settings

class ProjectView(LiveView, AssistantMixin):
    template_name = "projects/detail.html"
    assistant_provider = AnthropicProvider(api_key=settings.ANTHROPIC_API_KEY)
    assistant_model = "claude-opus-4-6"

    # Handlers that the LLM can call
    @event_handler
    def create_phase(self, name: str, **kwargs):
        """Create a new phase."""
        Phase.objects.create(project=self.project, name=name)
        self.phases = list(self.project.phases.all())
```

That's it. The app gets speech-to-LLM-to-handler execution, narration bubble, destructive confirmation, rate limit handling, and cost accounting — all for three lines of configuration plus its existing handler definitions. No adapter code, no prompt template, no schema conversion.

### Provider-per-request: pick based on complexity

```python
class ProjectView(LiveView, AssistantMixin):
    # Pick the provider dynamically based on the user's tier or the
    # complexity of the request.
    def get_provider(self, transcript: str) -> Provider:
        if self._is_trivial(transcript):
            return self._local_provider      # fast, cheap local model
        return self._remote_provider         # GPT-5 for the hard cases

    async def handle_speech(self, transcript: str, **kwargs):
        provider = self.get_provider(transcript)
        # Override the instance attribute for this call only
        saved = self.assistant_provider
        self.assistant_provider = provider
        try:
            await super().handle_speech(transcript, **kwargs)
        finally:
            self.assistant_provider = saved
```

### Custom provider: third-party LLM via entry point

```python
# In a third-party djust_assistant_ollama package:
from djust.assistant.providers import Provider, ChatResponse, StreamChunk

class OllamaProvider:
    name = "ollama"

    def __init__(self, endpoint: str = "http://localhost:11434", model: str = "llama3.1"):
        self.endpoint = endpoint
        self.default_model = model

    def chat(self, *, system, messages, tools=None, model=None, **kwargs):
        # Call local Ollama API, translate response to ChatResponse
        ...

    async def stream(self, *, system, messages, tools=None, model=None, **kwargs):
        # Stream from local Ollama, yield StreamChunks
        ...
```

App installs `djust_assistant_ollama` from PyPI, imports `OllamaProvider`, and uses it exactly like the built-in adapters. Zero framework code changes required.

## Minimum viable adapter implementation

For reference, here's what the OpenAI adapter looks like in full — ~200 lines. The Anthropic adapter is about the same size. Both are well within a week of implementation + testing.

```python
# python/djust/assistant/providers/openai_provider.py
from typing import List, Optional, Dict, Any, AsyncIterator
import json

from djust.assistant.providers.base import (
    Provider, Message, Tool, ToolCall, ChatResponse, StreamChunk, Usage,
)
from djust.assistant.errors import (
    ProviderAuthError, ProviderRateLimitError, ProviderTimeoutError,
    ProviderSchemaError, ProviderContentFilterError, ProviderError,
)


class OpenAIProvider:
    """Adapter for OpenAI's chat completions API with tool calling."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,          # override for Azure / OpenRouter
        organization: Optional[str] = None,
        default_timeout: float = 60.0,
    ):
        # Lazy-import so the OpenAI SDK is an optional dep
        from openai import OpenAI, AsyncOpenAI
        self._sync = OpenAI(api_key=api_key, base_url=base_url, organization=organization)
        self._async = AsyncOpenAI(api_key=api_key, base_url=base_url, organization=organization)
        self._default_timeout = default_timeout

    def chat(self, *, system, messages, tools=None, model, **kwargs):
        try:
            response = self._sync.chat.completions.create(
                model=model,
                messages=self._to_openai_messages(system, messages),
                tools=self._to_openai_tools(tools) if tools else None,
                tool_choice="auto" if tools else None,
                max_tokens=kwargs.get("max_tokens"),
                temperature=kwargs.get("temperature"),
                timeout=kwargs.get("timeout") or self._default_timeout,
            )
        except Exception as exc:
            raise self._normalize_error(exc) from exc

        choice = response.choices[0]
        return ChatResponse(
            text=choice.message.content,
            tool_calls=self._from_openai_tool_calls(choice.message.tool_calls or []),
            finish_reason=choice.finish_reason or "",
            usage=self._usage_from_response(response, model),
        )

    async def stream(self, *, system, messages, tools=None, model, **kwargs):
        try:
            stream = await self._async.chat.completions.create(
                model=model,
                messages=self._to_openai_messages(system, messages),
                tools=self._to_openai_tools(tools) if tools else None,
                tool_choice="auto" if tools else None,
                stream=True,
                **{k: v for k, v in kwargs.items() if k in ("max_tokens", "temperature")},
            )
        except Exception as exc:
            raise self._normalize_error(exc) from exc

        # Accumulate tool call fragments; OpenAI streams them as deltas
        tool_call_accum: Dict[int, Dict[str, Any]] = {}

        async for event in stream:
            choice = event.choices[0]
            delta = choice.delta

            if delta.content:
                yield StreamChunk(kind="text", text=delta.content)

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_call_accum:
                        tool_call_accum[idx] = {
                            "id": tc_delta.id or f"tc_{idx}",
                            "name": tc_delta.function.name or "",
                            "args_str": "",
                        }
                        yield StreamChunk(
                            kind="tool_call_start",
                            tool_call=ToolCall(
                                id=tool_call_accum[idx]["id"],
                                name=tool_call_accum[idx]["name"],
                                args={},
                            ),
                        )
                    if tc_delta.function.arguments:
                        tool_call_accum[idx]["args_str"] += tc_delta.function.arguments
                        yield StreamChunk(
                            kind="tool_call_delta",
                            tool_call=ToolCall(
                                id=tool_call_accum[idx]["id"],
                                name=tool_call_accum[idx]["name"],
                                args={"_partial": tool_call_accum[idx]["args_str"]},
                            ),
                        )

            if choice.finish_reason:
                # Emit completed tool calls
                for idx, accum in tool_call_accum.items():
                    try:
                        parsed_args = json.loads(accum["args_str"]) if accum["args_str"] else {}
                    except json.JSONDecodeError:
                        parsed_args = {"_invalid_json": accum["args_str"]}
                    yield StreamChunk(
                        kind="tool_call_end",
                        tool_call=ToolCall(
                            id=accum["id"],
                            name=accum["name"],
                            args=parsed_args,
                        ),
                    )
                yield StreamChunk(
                    kind="done",
                    finish_reason=choice.finish_reason,
                    usage=None,  # OpenAI stream doesn't include usage in all modes
                )

    # --- Translation helpers ---

    def _to_openai_messages(self, system: str, messages: List[Message]) -> List[Dict]:
        out = [{"role": "system", "content": system}]
        for msg in messages:
            if msg.role == "tool":
                out.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content or "",
                })
            elif msg.role == "assistant" and msg.tool_calls:
                out.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.args)},
                        }
                        for tc in msg.tool_calls
                    ],
                })
            else:
                out.append({"role": msg.role, "content": msg.content or ""})
        return out

    def _to_openai_tools(self, tools: List[Tool]) -> List[Dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def _from_openai_tool_calls(self, calls) -> List[ToolCall]:
        out = []
        for c in calls:
            try:
                args = json.loads(c.function.arguments) if c.function.arguments else {}
            except json.JSONDecodeError:
                args = {"_invalid_json": c.function.arguments}
            out.append(ToolCall(id=c.id, name=c.function.name, args=args))
        return out

    def _usage_from_response(self, response, model: str) -> Usage:
        from djust.assistant.providers.pricing import estimate_cost
        u = response.usage
        return Usage(
            prompt_tokens=u.prompt_tokens,
            completion_tokens=u.completion_tokens,
            total_tokens=u.total_tokens,
            estimated_cost_usd=estimate_cost(model, u.prompt_tokens, u.completion_tokens),
        )

    def _normalize_error(self, exc: Exception) -> ProviderError:
        from openai import AuthenticationError, RateLimitError, APITimeoutError, BadRequestError, APIStatusError
        if isinstance(exc, AuthenticationError):
            return ProviderAuthError(str(exc))
        if isinstance(exc, RateLimitError):
            return ProviderRateLimitError(str(exc))
        if isinstance(exc, APITimeoutError):
            return ProviderTimeoutError(str(exc))
        if isinstance(exc, BadRequestError):
            return ProviderSchemaError(str(exc))
        if isinstance(exc, APIStatusError) and exc.status_code == 503:
            return ProviderUnavailableError(str(exc))
        return ProviderError(str(exc))
```

The Anthropic adapter has the same shape with different field names. Both can share ~50 lines of helper code for message-to-vendor translation so we're not duplicating serialization logic.

## Testing strategy

### Provider adapter tests

Every adapter gets a standard test suite that verifies the protocol contract:

```python
# tests/assistant/test_provider_contract.py
import pytest
from djust.assistant.providers import Message, Tool, ToolCall

PROVIDERS = [
    pytest.param("mock", marks=pytest.mark.always),
    pytest.param("openai", marks=pytest.mark.requires_openai_key),
    pytest.param("anthropic", marks=pytest.mark.requires_anthropic_key),
]


@pytest.mark.parametrize("provider_name", PROVIDERS)
def test_chat_returns_tool_call_for_simple_intent(provider_name):
    provider = get_provider(provider_name)
    tools = [Tool(
        name="create_project",
        description="Create a new project.",
        parameters={
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    )]
    response = provider.chat(
        system="You help manage projects.",
        messages=[Message(role="user", content="Create a project called 'Q3 Planning'")],
        tools=tools,
        model=get_test_model(provider_name),
    )
    assert response.tool_calls
    assert response.tool_calls[0].name == "create_project"
    assert response.tool_calls[0].args.get("title") == "Q3 Planning"


@pytest.mark.parametrize("provider_name", PROVIDERS)
def test_chat_returns_text_for_non_tool_intent(provider_name):
    provider = get_provider(provider_name)
    response = provider.chat(
        system="You're a helpful assistant.",
        messages=[Message(role="user", content="Say hello.")],
        tools=None,
        model=get_test_model(provider_name),
    )
    assert response.text
    assert not response.tool_calls


@pytest.mark.parametrize("provider_name", PROVIDERS)
async def test_stream_yields_tool_call_end_for_simple_plan(provider_name):
    provider = get_provider(provider_name)
    tools = [Tool(name="test_tool", description="A test.", parameters={})]
    events = []
    async for chunk in provider.stream(
        system="You're a tester.",
        messages=[Message(role="user", content="Call test_tool.")],
        tools=tools,
        model=get_test_model(provider_name),
    ):
        events.append(chunk)

    end_events = [e for e in events if e.kind == "tool_call_end"]
    assert end_events
    assert end_events[0].tool_call.name == "test_tool"
```

The `mock` provider runs in every CI build (no network). The OpenAI and Anthropic adapters run in a nightly integration job that has API keys. Third-party adapters register their own test suites; they can opt in to the contract tests by importing `djust.assistant.providers.contract_tests`.

### Mock provider for app tests

```python
# python/djust/assistant/providers/mock.py
class MockProvider:
    """A deterministic provider for unit tests.

    Scripted to return specific ChatResponses in order. Lets app tests
    exercise AssistantMixin without hitting a real LLM.
    """
    name = "mock"

    def __init__(self, script: List[ChatResponse]):
        self.script = list(script)
        self.calls = []  # captured for assertions

    def chat(self, *, system, messages, tools=None, **kwargs):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if not self.script:
            raise RuntimeError("MockProvider ran out of scripted responses")
        return self.script.pop(0)

    async def stream(self, *, system, messages, tools=None, **kwargs):
        response = self.chat(system=system, messages=messages, tools=tools, **kwargs)
        if response.text:
            yield StreamChunk(kind="text", text=response.text)
        for tc in response.tool_calls:
            yield StreamChunk(kind="tool_call_end", tool_call=tc)
        yield StreamChunk(kind="done", finish_reason=response.finish_reason, usage=response.usage)
```

App tests use it like:

```python
def test_assistant_creates_project_from_speech():
    view = ProjectView()
    view.assistant_provider = MockProvider(script=[
        ChatResponse(
            text=None,
            tool_calls=[ToolCall(id="tc1", name="create_project", args={"title": "Q3"})],
            finish_reason="tool_calls",
        ),
    ])
    view.handle_speech("create a new project called Q3")
    assert Project.objects.filter(title="Q3").exists()
    # Verify the assistant asked the LLM exactly once with the right tools
    assert len(view.assistant_provider.calls) == 1
    tools = view.assistant_provider.calls[0]["tools"]
    assert any(t.name == "create_project" for t in tools)
```

Tests run fast, are fully deterministic, and don't need an API key.

## Security considerations

### API key storage

API keys are passed to provider constructors as plain strings. The framework doesn't take responsibility for secrets management — apps use their existing secrets story (`django-environ`, `os.environ`, AWS Parameter Store, etc). System checks warn if a provider is instantiated with a literal string that looks like an API key in source code (`sk-...`, `sk-ant-...`).

### Prompt injection

Covered in [ADR-002](002-backend-driven-ui-automation.md#safety-and-guardrails). The abstraction doesn't weaken any of those mitigations: user-generated content always rides in `Message(role="user")` or `role="tool"` positions, never concatenated into the system prompt.

### Provider-controlled inputs reaching the UI

The LLM's response text (narration) is rendered in the user's narration bubble. A hostile provider or a model that's been jailbroken could emit HTML/script in that text. Mitigation: the narration bubble auto-escapes via the same `format_html` path every other djust template uses. The framework never calls `mark_safe` on LLM output. A system check warns if an app template uses `{{ assistant_response|safe }}`.

### Tool calls as a privilege escalation surface

A hostile (or just buggy) LLM can plan tool calls the user didn't intend. Mitigations are in [ADR-002](002-backend-driven-ui-automation.md) (destructive confirmation, handler validation, step limit, permission inheritance). The provider abstraction doesn't change any of that — it just makes sure whichever provider the app picks, the same mitigations apply.

### Cost abuse

A bad actor who controls user input can run up LLM costs. Mitigations at the abstraction layer:

1. **Per-session rate limit** on `AssistantMixin.handle_speech` — default 20 calls/minute, configurable per view.
2. **Per-session monthly cap** — default `$10/month/user` for free-tier apps, configurable. When exceeded, the mixin narrates "You've reached this month's assistant quota" and stops.
3. **Circuit breaker** on provider errors — five consecutive `ProviderRateLimitError` / `ProviderUnavailableError` in 60 seconds trips the breaker and the mixin falls back to a non-LLM error message for 5 minutes.
4. **Budget guard rails** via `Usage.estimated_cost_usd` — if a single plan's estimated cost exceeds `assistant_max_cost_per_plan` (default $0.50), the mixin aborts the plan mid-execution.

All four are centralized in the mixin and apply regardless of which provider the app chose.

## Open questions

1. **Should `chat` and `stream` be one method or two?** Several vendor SDKs support both modes on the same call with a `stream=True` flag. The rationale for splitting is that streaming returns an async iterator and blocking returns a dataclass — two different return types. A single method with overloaded return types is awkward in Python typing. Keep as two.
2. **How do we handle providers that don't support tools at all?** (Small local models, older GPT-3.5, etc.) Two options: (a) adapter raises `ProviderConfigError("model X doesn't support tool calling")` at construction time; (b) adapter falls back to "emit a prompt asking the model to produce JSON, parse the JSON, validate against the tool schema, synthesize tool calls." (b) is more work but unlocks a larger provider space. My lean: do (a) for v0.5.0, consider (b) in a follow-up ADR.
3. **Should the framework ship a provider for Google's Gemini?** Probably yes, probably as a third-party package rather than core. Same shape as OpenAI / Anthropic. Doesn't need a separate ADR.
4. **Bedrock / Azure passthrough.** Both are supported via `base_url` override on the core providers. Do we also want explicit `BedrockProvider` / `AzureOpenAIProvider` classes for ergonomic parity? My lean: no — the `base_url` override is clear enough and documented. Override if a clear request comes in later.
5. **Local-model adapter in core or extra?** Shipping Ollama support in core means taking a transitive dep on `httpx` (already present) and a `localhost:11434` assumption. Shipping in a third-party package keeps core lean but requires users to find and install it. My lean: keep Ollama in a `djust-assistant-ollama` package under the djust-org organization, with a prominent pointer from the docs.
6. **Caching for idempotent tool calls.** If the same plan runs twice with identical input, should the abstraction cache the LLM response? Probably not — idempotency is a handler-level concern, and caching at the provider layer risks hiding non-determinism bugs. Apps that want caching can wrap their provider in a decorator.

## Decision

**Recommendation**: accept as Proposed, schedule implementation for v0.5.0 alongside `AssistantMixin` (Phase 5 of [ADR-002](002-backend-driven-ui-automation.md)). Implementation order:

1. Protocol definitions (`Message`, `Tool`, `ToolCall`, `ChatResponse`, `StreamChunk`, `Usage`, `Provider`) — 1 day.
2. Mock provider + contract test suite — 2 days.
3. OpenAI adapter + chat + stream — 4 days.
4. Anthropic adapter + chat + stream — 4 days.
5. Pricing table + cost estimation — 1 day.
6. Error normalization + circuit breaker — 2 days.
7. Entry-point registry for third-party providers — 1 day.
8. Integration with `AssistantMixin` — 3 days.
9. Documentation and examples — 3 days.

Total: ~3 weeks of focused work, fits comfortably in the Phase 5 window.

## Changelog

- **2026-04-11**: Initial draft. Proposed.
