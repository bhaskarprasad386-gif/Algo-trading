# Batch 0 — Android Owner Session Security

## Implemented

- The dedicated owner access token is encrypted with AES-256-GCM.
- The encryption key is generated and held by Android Keystore; it is never written to app files.
- Ciphertext is stored only in `Context.noBackupFilesDir`, not SharedPreferences or ordinary app files.
- The encrypted payload includes the server-issued `expires_in` deadline. The client refuses to restore a token within 30 seconds of expiry.
- A broker API 401 clears the in-memory token and encrypted session file.
- Corrupt ciphertext, authentication-tag failure or an invalidated Keystore key discards the stored session and forces a new owner login.
- The app remains usable for unauthenticated/read-only endpoints if Keystore initialization fails; authenticated broker endpoints fail closed.
- The token is persisted before being exposed to the in-memory interceptor. A storage failure therefore cannot create an authenticated in-memory-only session.
- Android PR CI now runs JVM unit tests before building the debug APK.

## Explicit non-goals

- Broker API keys, client codes, broker passwords and TOTP seeds are not persisted by this change.
- No token refresh endpoint is assumed. Once the server-issued token expires, the user must authenticate again.
- This does not arm real trading or enable broker order routing.
- No remote page renderer, push notification client or signed component manifest is introduced here.

## Verification still required

- Android unit tests and debug build must pass on the exact PR head.
- Install/upgrade test on API 24+ physical/emulator devices.
- Process-death restore, expired token, 401 revocation, app-data restore and Keystore invalidation scenarios on-device.
- Confirm the backend owner-login `expires_in` is always positive and no greater than one day.
