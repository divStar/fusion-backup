# Authentication: the robot account

fusion-backup reads your Fusion data as a **Secure Service Account (SSA)**: a robot user that belongs to an APS app and proves its identity with a private key. This page explains why, how to set it up, and how it works.

## Why a robot

Autodesk offers three ways for a program to reach your Fusion data, and only one of them suits unattended backups:

* **Your own user login** ([authorization code](https://aps.autodesk.com/en/docs/oauth/v2/tutorials/get-3-legged-token), "3-legged", with or without PKCE)\
Needs a browser once, then keeps itself alive with refresh tokens that are valid for 15 days and change on every use. Every run would have to write the new token back, e.g. into a CI secret, and two runs must never renew it at the same time. A missed run, or a lost new token, means logging in again by hand.
* **App-only token** ([client credentials](https://aps.autodesk.com/en/docs/oauth/v2/tutorials/get-2-legged-token), "2-legged": just the app's client ID and secret)\
Has no user context. Fusion and personal hub data require a user, so it sees no hubs at all.
* **[Secure Service Account](https://aps.autodesk.com/en/docs/ssa/v1)** (JWT bearer, "3-legged without a browser")\
The robot signs a short-lived JWT with its private key and exchanges it for a regular access token with user context. There is no refresh token: every run, and every hour of a long run, simply requests a new token. All credentials are static, so they fit plain CI secrets and parallel runs don't interfere.

> [!NOTE]
> The user login with a refresh token does work; fusion-backup was first built that way. It was dropped because keeping the token alive across scheduled runs is fragile, and once it lapses, CI can't log in again on its own: the browser login needs a person.

The robot only sees what you share with it, e.g. Reader access to one project, whereas your own login could read all your hubs and projects.

## Setup

### 1. APS app (Server-to-Server)

1. Sign in at [aps.autodesk.com](https://aps.autodesk.com) and choose a plan; the free plan is enough. Autodesk requires a payment method on file even for the free plan, to verify your identity. What is included and what is charged is listed on Autodesk's [rate sheet](https://www.autodesk.com/products/autodesk-platform-services/product-details).
2. Create a **developer hub** (Autodesk account → Products and services → Hubs → Create hub → APS Developer Hub).
3. In the developer hub, create an application of type **Server-to-Server** and enable at least the **Data Management API**, the **Manufacturing Data Model API** and, if listed, the **Secure Service Account API**.
4. Note the app's **client ID** and **client secret**.

### 2. Robot (service account) and key

The easiest way is Autodesk's **[SSA Manager](https://ssa-manager.autodesk.io/)** (source: [ssa-manager-sample](https://github.com/autodesk-platform-services/ssa-manager-sample)):

1. Sign in with the app's client ID and secret.
2. Create a robot with a first and last name.
3. A key for the robot should already exist - use the email there.\
Alternatively: create a key for it if it does not already exist.

It shows what fusion-backup needs: the robot's **email address** (for the invitation below), its **service account ID**, the **key ID** and the **private key**. The private key is shown only once; store it right away.

Alternatively, create the robot and the key with the [SSA API](https://aps.autodesk.com/en/docs/ssa/v1/tutorials/getting-started-with-ssa/task1-create-an-ssa/) itself.

### 3. Give the robot access in Fusion

1. **Hub member:** open your hub's admin page, `https://<your-hub>.autodesk360.com/g/admin/manage/hub`, and invite the robot's email address as a member. Robots accept automatically; it shows as active.
2. **Project permissions:** in the project's **Members and Permissions**, add the robot with at least **Reader** on the project or on the folders it should back up.

Reader is enough to list projects, download files and generate STEP exports, through both versions of the Manufacturing Data Model API.

### 4. Credentials as environment variables

| Variable | Value |
|---|---|
| `APS_CLIENT_ID` | client ID of the Server-to-Server app |
| `APS_CLIENT_SECRET` | client secret of that app |
| `APS_SSA_ID` | the robot's service account ID |
| `APS_SSA_KEY_ID` | the ID of the robot's key |
| `APS_SSA_PRIVATE_KEY` | the private key as PEM text (typical for CI secrets) |
| `APS_SSA_PRIVATE_KEY_FILE` | alternatively, the path of a file containing the PEM (typical for local use) |

Exactly one of the two key variables is needed; if both are set, `APS_SSA_PRIVATE_KEY` is used and the file is ignored. In CI, pass all of them from the CI system's secrets, as in the [Gitea example](../examples/gitea/README.md). Locally, for example with the key in a file only you can read:

```bash
export APS_CLIENT_ID="<client ID>"
export APS_CLIENT_SECRET="<client secret>"
export APS_SSA_ID="<service account ID>"
export APS_SSA_KEY_ID="<key ID>"
export APS_SSA_PRIVATE_KEY_FILE="$HOME/.config/fusion-backup/robot.pem"
```

Then test the setup:

```bash
fusion-backup check --repo path/to/repo
```

`check` logs in as the robot, lists the hubs and projects it can see and, with `--repo`, confirms that each repository's configured hub and project are among them. A robot that logs in but sees no projects has not been added to the hub or project yet.

## How the login works

1. fusion-backup signs a JWT with the robot's private key (RS256; header `kid` = key ID; claims: issuer = client ID, subject = service account ID, audience = the token endpoint, scope `data:read`, a unique ID, valid for 5 minutes).
2. It sends the JWT to `https://developer.api.autodesk.com/authentication/v2/token` (grant type `urn:ietf:params:oauth:grant-type:jwt-bearer`), authenticating the app with its client ID and secret.
3. Autodesk returns an access token valid for one hour, acting as the robot. When it expires during a long run, steps 1 and 2 are simply repeated.

Pre-signed download URLs carry their own credentials; fusion-backup sends them neither the token nor the app's secret, and never shows them in error messages.

## Keeping the credentials safe

> [!CAUTION]
> The private key together with the client ID and secret allow reading everything the robot has access to. Keep them in CI secrets or in files only you can read (e.g. `chmod 600`), never in a repository or a log.

* **Least privilege:** give the robot Reader access to what it backs up, nothing more. Hub administration is not needed.
* **Key rotation:** a robot can have several keys. To rotate, create a new key, update `APS_SSA_KEY_ID` and the private key, then disable or delete the old key.
* **Inactivity:** Autodesk deactivates robots that are unused for 12 months.

## Limitations

> [!NOTE]
> Autodesk's **usage numbers can't be read by the robot**: the APS Application Management API (`/applications/v2/.../usage/daily-totals`) answers robot tokens with `401 "Auto-provisioning is only available for Autodesk employees"`, although its documentation says Secure Service Accounts are accepted. Watch your usage in the APS portal (developer hub → Hub analytics → API usage) and rely on Autodesk's notification emails, which arrive when you approach your plan's limits.
