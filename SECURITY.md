# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| < 0.1 | No |

## Reporting a vulnerability

Please report vulnerabilities privately to **security@kabzhanov.com**. Include a
clear description, reproduction steps, affected version, and any suggested
mitigation. Do not publish credentials, database dumps, or exploit details in a
public issue.

We will acknowledge a report within five business days and will coordinate a
fix and disclosure timeline with the reporter.

## Credential handling

Database settings are read from `METRICS_DB_*` environment variables. Never
commit `.env`, passwords, tokens, or private connection strings. Use
`.env.example` only as a non-secret configuration template.
