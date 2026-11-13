# writing-auditor

Sandboxed MCP worker that grounds long-form writing in the user's own notes.

The vault (`AEGIS_VAULT_PATH`, mounted read-only) is split into chunks by heading and paragraph.
Each chunk has a stable ID built from its POSIX `vault://` path and the SHA-256 of its text, so
citations survive moving the vault between Windows and Linux and break visibly if the source
changes.

| Tool | What it does |
|---|---|
| `index_vault` | Rebuild the SQLite FTS5 index |
| `search` | BM25 full-text search; returns chunk IDs |
| `get_chunk` | Full chunk text, URI, and hash |
| `verify_quote` | Exact quote check after whitespace and quote-mark normalization |
| `audit_draft` | Sentences with no `[@chunk_id]` citation, and citations to chunks that don't exist |

No model is involved in any check. Planned (later milestones): Qdrant hybrid search and an
optional NLI model that scores whether a cited chunk actually supports the sentence citing it.
