# Shoonya OAuth credential setup

This application uses Shoonya OAuth, not legacy QuickAuth. Do not configure Vendor Code, IMEI, or TOTP auto-login for this flow.

## Prerequisites
- Shoonya API access enabled for the account.
- A static public IP address for the backend host, registered in Shoonya API Key settings. The backend's outbound public IP must match the registered IP.
- Client ID and Secret Code from API Key settings. Keep the Secret Code private.

## OAuth endpoints
- Authorize: `https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=<CLIENT_ID>`
- REST host: `https://api.shoonya.com/NorenWClientAPI/`
- WebSocket: `wss://api.shoonya.com/NorenWSAPI/`

## Manual login procedure
1. Configure `SHOONYA_OAUTH_CLIENT_ID` and `SHOONYA_OAUTH_SECRET_CODE` only in the backend's private environment/secret store.
2. In the authenticated application, request a Shoonya authorization URL. The backend returns the URL and a one-time `flow_id` (valid for 10 minutes).
3. Open the authorization URL, sign in to Shoonya, and inspect the browser address bar after the response. Per Shoonya API Support's instructions, the page may display **“Access restricted”** while the one-time auth code is present in the URL. Copy only that code.
4. Return to the authenticated application and submit the auth code together with the flow ID. Do not paste the code into chat, logs, tickets, or screenshots.
5. The backend computes SHA-256 of `client_id + secret_code + auth_code`, calls `GenAcsTok`, and verifies the returned UID/account ID against the identity bound to the flow. Credentials are read only from server settings; request-supplied credential fields are ignored.
6. Shoonya access tokens expire after 24 hours. Re-authenticate using a fresh flow; do not assume refresh-token renewal.

This manual copy-and-submit flow does not require the application to configure a callback URL or `SHOONYA_REDIRECT_URI`.

## Backend environment configuration

Set privately on the backend:
- `SHOONYA_OAUTH_CLIENT_ID`
- `SHOONYA_OAUTH_SECRET_CODE`

Do not place either value in frontend configuration or commit them to the repository.

## Safety and scope
- OAuth flow IDs are owner-bound, client-bound, single-use, bounded, and expire after 10 minutes.
- Broker connection remains real-trading disabled with the kill switch enabled after authentication.
- Shoonya market-data feed and provider failover remain disabled until separately configured and explicitly validated.
- This setup does not enable order placement.

## Security
- Never commit credentials, auth codes, access tokens, or checksums.
- Never log credential values, auth codes, or token responses.
- If the backend outbound static IP changes, update the registered IP before attempting login.
