"""T026a -- what the adapter passes to the SDK, and what it never does (FR-006, ADR-053).

Caching is owned by the SDK: the adapter's job is to pass the configured TTL and timeout through and to
convert every failure into a typed error rather than an exception. With no explicit `LANGFUSE_HOST` it must
construct no SDK client and contact no host (the SDK's default is Langfuse Cloud).
"""

from __future__ import annotations

from typing import Any

from prompts.langfuse_adapter import AdapterError, LangfuseAdapter, RegistryPrompt

ENV = {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk", "LANGFUSE_HOST": "http://langfuse.local:3000"}


class FakeChatPrompt:
    def __init__(self, prompt: Any, *, version: int = 4, config: Any = None, labels: Any = None) -> None:
        self.prompt = prompt
        self.version = version
        self.config = {"temperature": 0} if config is None else config
        self.labels = ["production"] if labels is None else labels


class FakeSdkClient:
    def __init__(self, *, result: Any = None, boom: Exception | None = None) -> None:
        self.result = result
        self.boom = boom
        self.get_prompt_kwargs: list[dict[str, Any]] = []

    def get_prompt(self, name: str, **kwargs: Any) -> Any:
        self.get_prompt_kwargs.append({"name": name, **kwargs})
        if self.boom is not None:
            raise self.boom
        return self.result


class Factory:
    def __init__(self, client: FakeSdkClient) -> None:
        self.client = client
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeSdkClient:
        self.calls.append(kwargs)
        return self.client


CHAT = [{"type": "chatmessage", "role": "system", "content": "S {{x}}"}, {"type": "chatmessage", "role": "user", "content": "U"}]


def _adapter(client: FakeSdkClient, env: dict[str, str] | None = None) -> tuple[LangfuseAdapter, Factory]:
    factory = Factory(client)
    return LangfuseAdapter(env=ENV if env is None else env, client_factory=factory), factory


def test_passes_ttl_timeout_label_and_chat_type_to_get_prompt() -> None:
    client = FakeSdkClient(result=FakeChatPrompt(CHAT))
    adapter, _ = _adapter(client)
    got = adapter.fetch_prompt("dining_intent_extraction", "staging", 3, 45)
    (call,) = client.get_prompt_kwargs
    assert call["name"] == "dining_intent_extraction"
    assert call["label"] == "staging"
    assert call["type"] == "chat"
    assert call["cache_ttl_seconds"] == 45
    assert call["fetch_timeout_seconds"] == 3
    assert isinstance(got, RegistryPrompt)
    assert got.version == 4 and got.labels == ["production"] and got.config == {"temperature": 0}
    assert got.messages == [{"role": "system", "content": "S {{x}}"}, {"role": "user", "content": "U"}]


def test_client_is_built_with_tracing_disabled_and_the_explicit_host() -> None:
    client = FakeSdkClient(result=FakeChatPrompt(CHAT))
    adapter, factory = _adapter(client)
    adapter.fetch_prompt("p", "production", 2, 60)
    (kwargs,) = factory.calls
    assert kwargs["tracing_enabled"] is False
    assert kwargs["host"] == "http://langfuse.local:3000"
    assert kwargs["public_key"] == "pk" and kwargs["secret_key"] == "sk"


def test_base_url_is_accepted_as_a_host_alias() -> None:
    env = {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk", "LANGFUSE_BASE_URL": "http://alias:3000"}
    adapter, factory = _adapter(FakeSdkClient(result=FakeChatPrompt(CHAT)), env)
    adapter.fetch_prompt("p", "production", 2, 60)
    assert factory.calls[0]["host"] == "http://alias:3000"


def test_unset_host_constructs_no_client_and_contacts_nothing() -> None:
    env = {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"}  # no LANGFUSE_HOST
    client = FakeSdkClient(result=FakeChatPrompt(CHAT))
    adapter, factory = _adapter(client, env)
    got = adapter.fetch_prompt("p", "production", 2, 60)
    assert got == AdapterError("no_credentials")
    assert factory.calls == [] and client.get_prompt_kwargs == []  # the SDK's cloud default is never reached


def test_missing_keys_construct_no_client() -> None:
    for env in ({"LANGFUSE_HOST": "http://h"}, {"LANGFUSE_HOST": "http://h", "LANGFUSE_PUBLIC_KEY": "pk"}, {}):
        adapter, factory = _adapter(FakeSdkClient(), env)
        assert adapter.fetch_prompt("p", "production", 2, 60) == AdapterError("no_credentials")
        assert factory.calls == []


def test_timeout_and_404_become_typed_errors_never_exceptions() -> None:
    for boom in (TimeoutError("timed out"), Exception("404 not found"), ConnectionError("down")):
        adapter, _ = _adapter(FakeSdkClient(boom=boom))
        assert adapter.fetch_prompt("p", "production", 2, 60) == AdapterError("fetch_error")


def test_text_prompt_and_placeholder_messages_are_wrong_shape() -> None:
    text_prompt = FakeChatPrompt("plain text prompt")  # a text-type prompt: .prompt is a str
    adapter, _ = _adapter(FakeSdkClient(result=text_prompt))
    assert adapter.fetch_prompt("p", "production", 2, 60) == AdapterError("wrong_shape")

    with_placeholder = FakeChatPrompt([{"type": "placeholder", "name": "history"}])
    adapter, _ = _adapter(FakeSdkClient(result=with_placeholder))
    assert adapter.fetch_prompt("p", "production", 2, 60) == AdapterError("wrong_shape")

    bad_role = FakeChatPrompt([{"type": "chatmessage", "role": "assistant", "content": "x"}])
    adapter, _ = _adapter(FakeSdkClient(result=bad_role))
    assert adapter.fetch_prompt("p", "production", 2, 60) == AdapterError("wrong_shape")


def test_malformed_config_is_wrong_shape() -> None:
    adapter, _ = _adapter(FakeSdkClient(result=FakeChatPrompt(CHAT, config="not a mapping")))
    assert adapter.fetch_prompt("p", "production", 2, 60) == AdapterError("wrong_shape")
