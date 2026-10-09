"""T017 -- the strict, single-pass `{{name}}` compiler (research R-04, FR-009/009a/009b/011).

One compiler renders BOTH sources (Git and registry), so they cannot render differently, and the SDK's
own `compile()` is never used (it cannot report which placeholders a template uses).

Design rules -- each is a Principle IV / fail-closed guarantee:

* **Single pass over the template's own placeholders.** Values are inserted verbatim and the output is
  never rescanned. Guest text, retrieved context and judge inputs may therefore contain `{{...}}`: that is
  a *value*, not a template. It is rendered literally, is never re-expanded, and never causes a rejection.
* **The unrendered check is on the template, before substituting.** `unrendered_placeholder` means "the
  template references a declared placeholder for which no value was supplied" -- never "the output
  contains braces".
* **Code-injected values win.** `required_vars` (the dietary vocabularies) are applied last, so a caller
  cannot override them (FR-011).

Check order (first failure wins): undeclared -> missing required -> unrendered.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from prompts.schema import PLACEHOLDER_RE, placeholders_in


class CompileError(Exception):
    """A template cannot be rendered safely. `reason` is one of `undeclared_var`,
    `missing_required_var`, `unrendered_placeholder`; `names` are the offending placeholders."""

    def __init__(self, reason: str, names: Sequence[str] = ()) -> None:
        self.reason = reason
        self.names = tuple(names)
        super().__init__(f"{reason}: {', '.join(self.names)}" if self.names else reason)


def _unique(items: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)


def compile_messages(
    messages: Sequence[Mapping[str, str]],
    *,
    required_vars: Mapping[str, str],
    runtime_vars: Mapping[str, str],
    tenant_vars: Mapping[str, str],
    declared: Collection[str],
) -> list[dict[str, str]]:
    """Render `messages` (`[{"role", "content"}]`) or raise `CompileError`.

    `declared` is the set of placeholder names the template is allowed to reference: the code-declared
    required names plus the template's own declared runtime/tenant variables."""
    used = _unique([name for m in messages for name in placeholders_in(m["content"])])

    undeclared = [name for name in used if name not in declared]
    if undeclared:
        raise CompileError("undeclared_var", undeclared)

    missing_required = [name for name in required_vars if name not in used]
    if missing_required:
        raise CompileError("missing_required_var", missing_required)

    values: dict[str, str] = {**runtime_vars, **tenant_vars, **required_vars}
    unsupplied = [name for name in used if name not in values]
    if unsupplied:
        raise CompileError("unrendered_placeholder", unsupplied)

    return [
        {
            "role": m["role"],
            # A callable replacement is inserted verbatim (no backslash processing) and is applied once.
            "content": PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], m["content"]),
        }
        for m in messages
    ]
