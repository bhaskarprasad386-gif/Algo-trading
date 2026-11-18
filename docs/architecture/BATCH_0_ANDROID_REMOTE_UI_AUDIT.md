# Batch 0 — Android Client, Remote UI, and Release Workflow Audit

**Status:** Source audit only; no APK build or release workflow was triggered.  
**Audited base:** `main` at `8da655f0c287251cf1602cae484714a7c26fcf15`.

## 1. Current Android architecture

- Native Kotlin Android application; compile SDK 34, minimum SDK 24, target SDK 34, Java/Kotlin 17.
- Android Gradle Plugin 8.1.4 and Kotlin plugin 1.9.22; release/build workflows provision JDK 17 and Gradle 8.2.
- OkHttp 4.12 and Retrofit 2.11 provide HTTP API access; coroutines/lifecycle-runtime are present.
- `MainActivity` is a large native dashboard with navigation, scanner refresh controls, Angel One credential fields, and real-trading safety controls.
- `BoxSpreadActivity` is a native Activity and polls the Box scanner REST endpoint every five seconds using a Handler. It removes the callback in `onDestroy`.
- `ApiService` defines a broad Retrofit API interface, including broker and scanner endpoints. The inspected service has no WebSocket client.
- The manifest requests INTERNET and ACCESS_NETWORK_STATE, disables cleartext traffic, and currently sets `android:allowBackup="true"`.
- The Gradle default backend URLs and both Android build/release workflow defaults still point to the legacy Render hostname. A release explicitly injects that Render URL today.

## 2. Existing server-driven pieces versus missing remote UI

There is already a remote strategy registry/workspace API:
- `GET /api/v1/app/strategies`
- `GET /api/v1/app/strategies/{strategy_id}/workspace`

The Android API models consume the strategy registry, but navigation/screens such as Main, Box Spread, scanner detail, strategy registry, results journal, and updates are native Activities. No signed remote page manifest, schema-version negotiation, remote component renderer, ETag content cache, capability allowlist, or last-known-good page rollback was found in the inspected Android service/navigation files.

**Conclusion:** The app has server-provided strategy metadata/workspace data, not a complete server-driven page system. Routine native screen layout changes still require an APK update in the current implementation.

## 3. Authentication and secret handling

- The real-owner access token is stored in a process-local `@Volatile` Kotlin property in `ApiService`.
- The OkHttp interceptor attaches it only to broker API paths (excluding owner login) and clears it on HTTP 401.
- No encrypted/Keystore-backed token persistence or refresh-token lifecycle was found in the inspected `ApiService`.
- MainActivity contains Angel One API key, client code, password, and TOTP input fields; this is a broker-specific UI and does not yet support a generalized account picker.
- The Android manifest currently allows app backup. Before persisting any session token, review backup/data-extraction rules and explicitly exclude secret/session storage from backup.
- Broker passwords, API keys, and TOTP seeds should not be stored in SharedPreferences or included in remote page data, logs, crash reports, or analytics.

## 4. Update and release workflow

- `android-build.yml` builds a debug APK on pushes/PRs that touch `mobile/android/**`; it also supports manual backend URL overrides.
- `android-build-verify.yml` is manual-only and runs unit tests plus debug compilation.
- `android-release.yml` is manual-only, validates signing secrets, builds a signed release APK, publishes a GitHub Release, and publishes the app update manifest.
- Release versionCode validation is hard-coded to require a value greater than 6 in the inspected workflow; this must be maintained as releases advance.
- Release and build workflow defaults currently inject the Render backend URL, so the eventual VPS/Cloudflare move requires coordinated URL configuration and end-to-end compatibility validation before an APK is published.
- No Android build/test/release workflow was run as part of this source audit.

## 5. Recommended remote UI contract

Use the native Kotlin app as a stable shell for auth, secure storage, device capabilities, push lifecycle, and safety-critical confirmation. Serve routine page composition as signed data, not arbitrary executable code.

Minimum manifest fields:
- manifest schema version and app minimum supported version;
- page ID, route, content revision, and API contract version;
- list of allowed preinstalled components and their validated properties;
- capability allowlist (API calls/events the page may invoke);
- issued-at/expiry, content hash, and server signature/key ID;
- fallback route and compatible last-known-good revision.

Client requirements:
- Verify signature/hash before rendering; reject unknown schema, unsupported components, expired or rolled-back manifests.
- Cache manifest and page payload by version/ETag; invalidate only changed pages.
- Keep a last-known-good local page bundle and native fallback route.
- Never execute arbitrary JavaScript, Kotlin, downloaded bytecode, or untrusted remote HTML as an app page.
- Keep Live Trading enable/disable, account selection, and kill-switch confirmations native and server-enforced; remote content cannot grant capabilities.
- Use Keystore-backed encrypted storage for refreshable app session material, with backup exclusions and explicit logout/revocation.
- Add a lifecycle-managed authenticated push channel for page invalidation and private account execution events; retain bounded polling fallback. Do not create a market-data WebSocket in the Android app.

## 6. Required validation before migration

1. Build debug and signed release with explicit VPS/Cloudflare URLs in a non-production workflow.
2. Verify TLS, certificate validation, API auth, 401 refresh/re-auth, logout, and process-death restoration.
3. Test current APK compatibility with the new backend during rolling deployment.
4. Test remote manifest signature rejection, expiry, unsupported version, offline cache, ETag refresh, rollback, and last-known-good fallback.
5. Test private push authorization and sequence-based resync; confirm no account data crosses account boundaries.
6. Test app backup/restore and ensure secrets/session material are excluded or re-provisioned securely.
7. Verify Live controls remain disabled and cannot be enabled by remote page metadata.
8. Only publish an APK after the target backend URL and release manifest have passed end-to-end validation.

## 7. Safety decision

No Android source, manifest, Gradle configuration, signing secret, APK, or release workflow was changed. No APK was built or published. Existing Render URL remains unchanged until the replacement endpoint is verified. No mobile WebSocket or remote executable UI was introduced.
