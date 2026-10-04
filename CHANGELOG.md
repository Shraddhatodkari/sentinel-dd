# Changelog

## [1.0.0] - 2026-10-04
### Added
- LangGraph multi-agent due-diligence workflow with parallel research, critic reflection loop and HITL interrupt/resume.
- Hybrid RAG (dense + BM25 + RRF + rerank + MMR) with claim-level grounding verification.
- GraphRAG: ownership/officer extraction, entity resolution, UBO, k-hop sanctions/PEP exposure, communities.
- ML risk bundle (calibrated GBM, IsolationForest, K-Means, adverse-media NLP) with registry, gates and PSI drift.
- From-scratch PyTorch Transformer + LoRA; QLoRA SFT script; SFT dataset builder from approved cases.
- DLP, guardrails, JWT/RBAC, maker-checker approvals, hash-chained audit log.
- Anthropic Claude client (prompt caching), native tool-use agent, MCP server, multi-provider router.
- FastAPI service, Docker, docker-compose, Kubernetes manifests, GitHub Actions CI with eval gates.
