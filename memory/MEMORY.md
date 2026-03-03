# Claude Code Auto Memory

## Infrastructure

- **LLM/Embedding endpoint**: `https://10.6.0.1/v1` — llama-swap serving OpenAI-compatible API (50+ models)
- **SSL**: Endpoints use Vulcan Certificate Authority (self-signed). Combined cert bundle at `~/.config/ssl/ca-bundle-with-vulcan.pem`. Set `SSL_CERT_FILE` env var to this path for Python processes.
- **Qdrant**: `https://qdrant.vulcan.lan:443` — requires explicit `:443` port (qdrant-client URL parsing issue)
- **Network**: vulcan.lan (192.168.1.2) reachable; hera.lan (192.168.1.4) may not be

## Local LLM Usage Pattern

To use John's local models from any OpenAI-compatible client:
- **Base URL**: `https://10.6.0.1/v1`
- **API key**: `dummy-key` (no real auth required)
- **SSL cert**: `SSL_CERT_FILE=~/.config/ssl/ca-bundle-with-vulcan.pem`
- **Recommended LLM**: `Qwen3.5-27B-Instruct` — fast, no thinking mode, clean `content` responses
- **Recommended embeddings**: `bge-m3` (1024 dimensions)
- **Avoid for structured output**: `Qwen3.5-27B` (non-Instruct), `GLM-4.7-Flash`, `GLM-4.7-Flash-REAP-23B-A3B` — these use thinking/reasoning mode where responses go into `reasoning_content` with empty `content` field
- **Other available models**: Devstral-Small/Large, Llama-3.1-8B-Instruct, Mistral-7B, Mixtral-8x7B, DeepSeek-V3, Phi-4, and many more (run `curl -sk https://10.6.0.1/v1/models` to list)

## mem0 Setup

- **Config**: `~/.claude/.claude.json` (hardlinked to `~/.config/claude/personal/.claude.json`)
- **Local fork**: `~/.config/claude/mem0-mcp-selfhosted/` — patched config.py to support `openai` as LLM provider
- **LLM**: Qwen3.5-27B-Instruct via OpenAI-compatible API
- **Embeddings**: bge-m3 (1024 dims) via OpenAI-compatible API
- **Hooks wrapper**: `~/.config/claude/mem0-env.sh` — sets env vars and runs via uvx
- **Setup notes**: `~/.config/claude/mem0-setup-notes.md`
