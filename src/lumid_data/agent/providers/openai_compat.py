"""OpenAI-compatible adapter — speaks Chat Completions against any base URL.

Works with vLLM, Ollama (``http://host:11434/v1``), Together, Groq, or
anything else that implements the OpenAI API surface. Behaviour is the
same as the native OpenAI adapter; the only difference is the
configurable ``base_url``.
"""

from .openai import OpenAIAdapter


class OpenAICompatAdapter(OpenAIAdapter):
    name = "openai_compat"
