# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in djust, please **do not** open a public
GitHub issue. Instead, report it by emailing:

**security@djust.org**

Please include:
- A description of the vulnerability
- Steps to reproduce it
- Potential impact
- Any suggested fixes (optional)

We will acknowledge your report within **48 hours** and work with you to
understand and resolve the issue promptly. We aim to release a patch within
30 days of disclosure for critical vulnerabilities.

## Supported Versions

Security patches are applied to the latest stable release.

| Version | Supported |
|---------|-----------|
| Latest  | ✓         |
| Older   | No        |

## Security Updates

Security patches are released as patch versions and announced in
[CHANGELOG.md](CHANGELOG.md). Critical patches are also noted in the
GitHub release.

## Scope

The following are in scope for security reports:

- Remote code execution
- Authentication or authorization bypass
- Cross-site scripting (XSS) or injection vulnerabilities in the framework
- CSRF bypass in djust-provided utilities
- Sensitive data exposure from framework internals

The following are **out of scope**:

- Security issues in example apps or demo code
- Issues in dependencies (report upstream)
- Theoretical vulnerabilities without a proof of concept
