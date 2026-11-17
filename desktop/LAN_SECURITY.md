# Local-network access design (not enabled)

The current local interface binds to 127.0.0.1 and checks the Host and Origin
headers. Do not change that binding to 0.0.0.0: the current local GET handler
returns a shared form token and is not a remote authentication boundary.

A future opt-in network listener needs all of the following before it is enabled:

1. A stable DNS hostname resolving to a private interface, with a trusted TLS
   certificate for that hostname. Display explicit setup and certificate trust
   instructions for each client. WebAuthn requires a secure context and a
   valid relying-party domain; arbitrary LAN IPs must not be assumed to work.
2. A separate listener and handler that require authentication for every
   protected GET and POST, including status and file operations. Validate the
   exact Host and HTTPS Origin. Retain separate local-only administration and
   first-run login interfaces; never expose Telegram authorization to the LAN.
3. A random, short-lived, single-use pairing request initiated from the phone
   and explicitly approved in the local-only UI. Pairing approval and
   credential registration must be linked to the same challenge; no password,
   token or raw device address is a substitute for proof of possession.
4. A maintained WebAuthn server implementation validating RP ID, exact origin,
   challenge, authenticator signature, user verification and sign counter.
   Store public keys, credential IDs and user-visible labels; never biometrics.
   MAC addresses are not available to browser applications and are not a safe
   identity mechanism. Bind and persist only completed registrations.
5. Rotatable, short-lived, server-side authenticated sessions with Secure,
   HttpOnly, SameSite cookies and CSRF protection, plus rate limits and a local
   screen for revoking devices. Destroy sessions after revocation and restart.
6. Security tests for unpaired, expired and revoked credentials, replayed
   challenges, forged assertions, mismatched origins/RP IDs, proxy headers,
   CSRF, concurrent pairing and sensitive endpoints. Verify real registration
   and login with supported Windows, Linux and phone browser combinations.

Only expose the network listener after these checks succeed. Do not weaken
TLS, browser-origin or authenticator verification to make onboarding easier.

Reference: https://www.w3.org/TR/webauthn/
