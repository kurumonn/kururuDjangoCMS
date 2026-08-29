# Security Policy

## Supported versions

Security fixes are provided for the latest release branch. Older snapshots and
unmerged feature branches are not treated as supported production releases.

## Reporting a vulnerability

Do not publish exploit details, personal data, credentials, or production host
information in a public issue. Use GitHub private vulnerability reporting for
this repository and include the affected revision, reproduction conditions,
impact, and a minimal proof of concept that does not access third-party data.

Do not test against a production site without the site owner's explicit written
authorization. Dependency reports should identify both the advisory source and
the exact installed or locked version; application authorization findings need
a server-side reproduction test.

## Release checks

Pull requests run the full Django test suite, migration drift and deployment
checks, Bandit, PyPI and OSV dependency audits, and a tracked-file secret scan.
External plugins must additionally verify a clean wheel installation and the
`kururucms.plugins` entry point before release.
