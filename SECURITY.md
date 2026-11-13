# Security policy

Aegis-Local is a security boundary for AI tools, so bypasses of that boundary are security bugs
even when they need no traditional exploit. Examples:

- A way for model output or untrusted content to trigger a mutating tool call without approval
- A loop or budget the circuit breaker fails to stop
- A sandbox reaching the network, the host filesystem, or another container it shouldn't
- A service listening on a non-loopback interface by default
- Credential exposure in logs, the audit database, or error messages

## Reporting

Please **don't open a public issue** for these. Report privately through GitHub:
**Security → Report a vulnerability** on the
[repository page](https://github.com/madkatomega-sys/aegis-local/security/advisories/new).

Include what you did, what you expected, what happened, your OS and container runtime, and the
relevant section of the audit log if you have one. A minimal reproduction (for example a fixture
file containing an injection payload) helps a great deal.

## What to expect

- Acknowledgment within 7 days.
- An initial assessment, with severity, within 14 days.
- A fix or mitigation plan, and credit in the release notes if you'd like it.

## Supported versions

Until 1.0, only the latest release and `main` receive security fixes.

## Scope

In scope: everything in this repository. Out of scope: vulnerabilities in llama.cpp, OpenCode,
container runtimes, GPU drivers, or model weights themselves. Please report those upstream, though
we're glad to hear about them if they affect how Aegis-Local should be configured.
