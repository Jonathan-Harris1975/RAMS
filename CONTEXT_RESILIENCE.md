# RAMS context resilience

RAMS is coding-focused, so its default context route is **LeanCTX**. The application does not depend on LeanCTX or Context Gateway as Python packages; both are external OpenAI-compatible proxies and are skipped immediately when their base URL is not configured.

Default order:

`LeanCTX -> Context Gateway -> Headroom -> OpenRouter context-compression -> deterministic local compaction -> direct OpenRouter`

## Primary selection

Set `RMS_CONTEXT_PRIMARY_PROVIDER` to one of:

`leanctx`, `context_gateway`, `headroom`, `openrouter`, `deterministic`, `direct`

The fallback list is controlled by `RMS_CONTEXT_FALLBACK_PROVIDERS`. Duplicates are removed and `direct` is always retained as the final break-glass path.

## External proxies

Set `RMS_LEANCTX_BASE_URL` and/or `RMS_CONTEXT_GATEWAY_BASE_URL` to the deployed OpenAI-compatible API root, normally including `/v1`. If a proxy requires client authentication, set `RMS_LEANCTX_API_KEY` or `RMS_CONTEXT_GATEWAY_API_KEY` respectively. Proxy credentials should be supplied through the deployment secret store.

LeanCTX should be configured with OpenRouter as its OpenAI-compatible upstream and with the upstream OpenRouter key injected by the proxy. Context Gateway should likewise be configured as a custom-agent proxy for the OpenRouter-compatible route. RAMS never sends its internal routing metadata upstream.

`RMS_CONTEXT_LOCAL_MAX_CHARS` bounds the dependency-free local fallback. Exact AnchorPatch contexts bypass lossy OpenRouter/local compaction and retain the existing fidelity-first behaviour.
