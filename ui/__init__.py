"""Feature 008: a local, dev-only verification console for the ResortFlow graph.

Not part of the eval suite or the CI gate (eval/, graph/, agents/, mcp/ are
all untouched by this package) — it exists purely so a human can type a
message and watch the real supervisor graph run: node order, tool calls,
hold TTLs, guardrail verdicts, and (once configured) live groundedness and
per-call token cost. See ui/README.md for scope.
"""
