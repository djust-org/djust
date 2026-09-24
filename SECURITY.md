# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in djust, please **do not** open a public
GitHub issue. Report it through either channel:

- **GitHub Private Vulnerability Reporting (preferred):** the
  [Report a vulnerability](https://github.com/djust-org/djust/security/advisories/new)
  button on the repository's Security tab. The report becomes a draft advisory
  that you and the maintainers work on privately, and you are credited
  automatically.
- **Email:** **security@djust.org**, if you can't use GitHub.

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

## Advisory publication runbook (maintainers)

The two intake channels keep their records in different places, so check the
right one before publishing anything (#2878):

- **Private Vulnerability Reporting:** repository → Security → Advisories →
  the advisory's *Reported* entry. The submission is the advisory's original
  description and author.
- **Email:** the security@djust.org mailbox.

Before publishing an advisory:

1. **Read the original submission** in the channel it arrived through. It is
   the primary source: the reported affected range, the reproduction and the
   reporter's identity all come from it. Check the affected range against the
   code too; a report can get the floor wrong.
2. **Verify every `credits` entry against that submission.** A credit is a
   public statement about who found the issue. Publish only credits you can tie
   to a real report.
3. **After any edit to `credits`, check `collaborating_users`.** Through the API,
   a `PATCH` of `credits` can also clear `collaborating_users`, and restoring
   `credits` does not restore them.
4. **Treat `collaborating_users` as an access grant, not an attribution.** It
   gives an account access to the private advisory. Add or remove an entry only
   as a deliberate decision, never as a side effect of editing a credit.
