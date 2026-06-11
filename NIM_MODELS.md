"""
0xAutoPR — Final NIM Model Verification
All 8 endpoints confirmed LIVE on 2026-06-11

FINAL VERIFIED MODEL MAP:
  orchestrator    -> moonshotai/kimi-k2.6                          ✅ 1.6s
  code_reader     -> qwen/qwen3.5-397b-a17b                       ✅ 2.9s
  review_agent    -> z-ai/glm-5.1                                  ✅ 2.6s
  fix_writer      -> mistralai/mistral-large-3-675b-instruct-2512  ✅ 0.5s
  patch_generator -> mistralai/mistral-nemotron                    ✅ 0.5s
  test_writer     -> nvidia/nemotron-3-super-120b-a12b             ✅ 5.8s
  pr_opener       -> stepfun-ai/step-3.7-flash                    ✅ 0.9s
  embeddings      -> nvidia/nv-embedcode-7b-v1                    ✅ 1.2s (dim=4096)

REJECTED MODELS:
  moonshotai/kimi-k2-instruct          -> 410 EOL 2026-05-12
  qwen/qwen3-coder-480b-a35b-instruct -> 410 EOL 2026-06-11
  deepseek-ai/deepseek-v3.1           -> not in catalog
  deepseek-ai/deepseek-v4-flash       -> timeout (93s)
  deepseek-ai/deepseek-v4-pro         -> timeout (93s)
  nvidia/llama-3.1-nemotron-ultra-253b -> 404
  nvidia/llama-3.1-nemotron-70b-instruct -> 404
  mistralai/mistral-medium-3.5-128b   -> timeout (92s)
  z-ai/glm-4.7                        -> not in catalog
  nvidia/nv-embedcode-7b-v2           -> not in catalog
"""
