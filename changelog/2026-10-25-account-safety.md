# Invitations, password-reset links and authenticator-app sign-in

### Added
- **People** page (admins): everyone in the workspace, how they sign in, **Invite someone** and **Reset password**. Both produce a one-time link (invitations last seven days, resets 24 hours) to hand over privately; nothing is e-mailed. Using a link signs out every other session of that person.
- **Authenticator app** (Profile): optional six-digit codes at sign-in (RFC 6238, works with Google Authenticator, 1Password, Authy), eight one-time recovery codes shown once, turning it off needs the password and a code. Admins can remove a lost authenticator from People.
- Sign-in asks for the code as a second step; wrong codes are rate-limited and audited.

### Changed
- When registration is closed (`REPOLENS_ALLOW_REGISTRATION=false`) people join by invitation. The first workspace on a new install can always be created.

### Security
- Link tokens and recovery codes are stored only as hashes; a link works once, a newer link replaces an older one, and unknown, used and expired links all give the same answer. An authenticator code cannot be replayed within its 30-second step. Authenticator secrets are encrypted like other stored secrets.

### Upgrade notes
- Migration 0018 adds the authenticator columns and the `auth_tokens` table (development databases add them on start).
