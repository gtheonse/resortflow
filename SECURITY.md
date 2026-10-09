# Security Policy

## Scope

ResortFlow is a reference implementation run against simulated vendor systems. It is not deployed
to production, and no real guest, payment, or gaming data is processed.

## Local development credentials

Every credential in `docker-compose.yml`, `.env.example`, and the simulators' source (database
passwords, the MCP development signing key, the vendor fixture tokens) is a throwaway value for
a local stack. The compose file binds all published ports to `127.0.0.1`.

- Do not expose the local stack to a network.
- Do not reuse any of these values elsewhere.
- Real credentials belong in `.env`, which is git-ignored. Never commit it.

`MCP_DEV_SIGNING_KEY` signs the development token issuer in `mcp/gateway/_shared/identity.py`.
`make seed` generates a random one into `.env`; the fixed fallback in the compose file exists
only so the stack starts before a key has been generated. In any real deployment, caller
identity must come from a production identity provider (Microsoft Entra ID in the reference
architecture), not the development issuer.

## Reporting a vulnerability

Please report security issues privately through GitHub's
["Report a vulnerability"](https://github.com/gtheonse/resortflow/security/advisories/new)
form rather than a public issue. Include the affected component, steps to reproduce, and the
impact you expect. I aim to acknowledge reports within a few days.

## Built-in safeguards worth knowing about

- Tenant identity is taken from the verified token claim and injected server-side, never from
  model output.
- PII and health data are redacted at the trace-write boundary, and conversation state is
  encrypted at the serializer layer.
- Retrieved content is screened for prompt injection before use, and compliance rules are
  enforced in code rather than by prompt.
