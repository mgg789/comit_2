# Security Policy

## Reporting

Report suspected vulnerabilities privately to the CTO, GitHub **@mgg789** (Telegram **@mschail**). Do not post credentials, private resumes, recording URLs or actionable exploit payloads in public issues. Include the affected revision, prerequisite access, expected boundary and a minimal reproduction using synthetic data. No response-time guarantee is currently defined.

## Scope and ownership

Comit exposes four browser applications and the public API through nginx. Python engines, internal API routes, PostgreSQL, provider credentials and stored media belong to the internal service boundary. Ownership is in `.github/CODEOWNERS`; product and UI responsibilities are documented in `docs/team.md`.

## Security invariants

- Candidate identity and employer ownership come from authenticated sessions. Browser-supplied owner IDs, scores, verification evidence or billing totals are not authority.
- Profile contacts are hidden until invitation acceptance. Public search, PDFs, explanations and exports must not leak hidden data, including through search filters.
- Consent for processing, publication, training and recording is explicit and separate. Withdrawal must fence pending work and subsequent access. Recording retention is 90 days when authorized recording is enabled.
- Internal routes are unavailable through the public nginx prefix. Service authentication and owner checks are still required internally; missing credentials fail closed.
- Mutations validate Origin where applicable, use transaction/revision boundaries and handle retries without duplicate charges, invitations, grades or rewards.
- Uploaded PDF/images and provider responses are untrusted. Parsing has byte/page/pixel/time bounds; extracted text is neither executable instruction nor verified profile evidence.
- SMTP, model keys, session secrets, original media and private resume contents must not enter Git, public logs, fixtures or generated documentation.
- Production does not expose development login codes or synthetic FSP authority. Demo fixtures keep explicit provenance. LLM output cannot decide hiring or bypass strict eligibility.

## Assessment and limitations

Report reachable authorization bypass, private-data disclosure, token exposure, replay causing duplicate effects, unsafe file parsing, consent bypass and resource exhaustion with concrete prerequisites and impact. A green test suite alone is not proof that a control holds in deployment.

There are no blanket exclusions or owner-accepted vulnerability suppressions. Local synthetic fixtures do not authorize attacks against the public deployment. Client camera/focus telemetry is a signal, not proof of honest testing. Real FSP credentials/API are not supplied by the hackathon; the demo adapter cannot establish a real-world identity. Production recording is disabled by default and requires explicit configured consent/retention operation before activation.
