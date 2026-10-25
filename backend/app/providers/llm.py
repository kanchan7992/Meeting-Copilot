"""Chat model factory: any LangChain-supported provider, chosen by environment variables."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import ProfileSettings

# Providers that speak the OpenAI API at a custom base URL (vLLM, LiteLLM, TGI, LM Studio, ...).
_OPENAI_COMPATIBLE = {"openai_compatible", "vllm", "litellm"}


# Builds the chat model described by a profile. — pradeep.singh@rodicconsultants.com
def build_chat_model(profile: ProfileSettings) -> BaseChatModel:
    provider = profile.llm_provider.lower()
    if provider == "fake":
        return _fake_model()

    from langchain.chat_models import init_chat_model

    kwargs: dict = {
        "temperature": profile.llm_temperature,
        "max_tokens": profile.llm_max_tokens,
        "streaming": True,
        **profile.llm_kwargs_dict(),
    }
    if provider in _OPENAI_COMPATIBLE:
        if not profile.llm_base_url:
            raise ValueError("LLM_BASE_URL is required for an OpenAI-compatible provider")
        kwargs["base_url"] = profile.llm_base_url
        kwargs["api_key"] = profile.llm_api_key or "not-needed"
        provider = "openai"
    else:
        if profile.llm_base_url:
            kwargs["base_url"] = profile.llm_base_url
        if profile.llm_api_key:
            kwargs["api_key"] = profile.llm_api_key
    return init_chat_model(profile.llm_model, model_provider=provider, **kwargs)


# Returns a deterministic streaming model for tests and offline demos. — pradeep.singh@rodicconsultants.com
def _fake_model() -> BaseChatModel:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    # Yields the same canned answer forever. — pradeep.singh@rodicconsultants.com
    def answers():
        while True:
            yield AIMessage(content='Q: Test question\n• First point\n• Second point\nSay: "Happy to follow up."')

    return GenericFakeChatModel(messages=answers())
