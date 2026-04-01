---
name: yonyou-work-notify
description: Get Yonyou self-app access tokens, compute the required HmacSHA256 signature, and send idempotent work notifications through `/yonbip/uspace/rest/openapi/idempotent/work/notify/push`. Use when Codex needs to prepare, send, or troubleshoot 用友/友空间工作通知消息, validate `srcMsgId` idempotent payloads, or debug `access_token`, tenant domain, and request-body issues.
---

# Yonyou Work Notify

## Required Values First

Always ask the user to provide these 3 required values before doing anything else:

1. `appKey`
2. `appSecret`
3. `yhtUserId`

If any of the 3 is missing, stop and ask for it first. Do not assume or reuse a different user's `yhtUserId`.

## Quick Start

1. Ask the user for the 3 required values first: `appKey`, `appSecret`, `yhtUserId`.
2. Treat `appKey` and `appSecret` as secrets. Do not write them into repo files, examples, or logs.
3. Confirm the tenant `openapi_base_url` before sending the notification. The notification API uses a tenant data-center domain, not a fixed global domain.
4. Prefer using the same data-center host for both token retrieval and notification sending. A token from one data-center host can be rejected as `非法token` on another host.
5. Use `scripts/send_work_notify.py` for end-to-end sending.
6. Use `scripts/get_access_token.py` when only token retrieval or signature troubleshooting is needed.
7. Open `references/api.md` when mapping fields, checking required parameters, or reviewing response semantics.

## Workflow

### 1. Collect inputs

Collect these values before a real send:

- First-pass required values: `appKey`, `appSecret`, `yhtUserId`
- Send-time required values: tenant `openapi_base_url`, `srcMsgId`, `title`, `content`
- Optional business routing fields: `labelCode`, `serviceCode`, `appId`, `tabId`, `catcode1st`, `url`, `webUrl`, `miniProgramUrl`
- Optional read-mark fields: `esnData` with `fromId`

If `srcMsgId` is missing, build one in the form `业务标识:唯一编码`, for example `OA_APP:000001`.
If `openapi_base_url` is missing, ask for the tenant domain before attempting a real send.

### 2. Get `access_token`

- Use the upgraded self-app endpoint: `/iuap-api-auth/open-auth/selfAppAuth/base/v1/getAccessToken`
- Prefer the same data-center host as `openapi_base_url`; if a business request returns `非法token`, the token may have been fetched from the wrong data center
- Build the signing string by sorting parameter names and concatenating `key + value`
- Compute `HmacSHA256`, then `Base64`, then URL-encode the result
- Prefer the bundled script so signature, timestamp, and query encoding stay correct

### 3. Send the idempotent work notification

- POST JSON to the tenant OpenAPI domain; on newer data-center routes prefer `/iuap-api-gateway/yonbip/uspace/rest/openapi/idempotent/work/notify/push` and keep legacy `/yonbip/...` as a fallback
- Pass `access_token` as a query parameter
- Include required JSON fields: `srcMsgId`, `yhtUserIds`, `title`, `content`
- Add `serviceCode` when the message should reuse a Workbench service icon
- Add `esnData` with `fromId` when downstream read-marking needs a business identifier

### 4. Validate the result

- Treat `code == "200"` plus `data.flag == 0` as a successful send
- Remember that the API is idempotent by `srcMsgId`; repeated sends can return success without creating another message
- When debugging failures, compare payload types and required fields against `references/api.md`

## Bundled Resources

- `scripts/send_work_notify.py`: Prompt for missing credentials, fetch a token, and send the notification
- `scripts/get_access_token.py`: Prompt for missing credentials and print the token or the raw auth response
- `scripts/client.py`: Shared signing, prompting, and HTTP helpers
- `references/api.md`: Concise reference for token retrieval, signature rules, payload fields, and response codes

## Common Commands

Run from the skill folder:

```bash
python3 scripts/get_access_token.py \
  --auth-base-url https://c2.yonyoucloud.com \
  --json

python3 scripts/send_work_notify.py \
  --openapi-base-url https://<tenant-domain> \
  --src-msg-id OA_APP:000001 \
  --yht-user-id 7847bf59-19bc-4d2a-b4f4-7e2450f25a7f \
  --title "标题名称" \
  --content "通知内容xxxx" \
  --label-code OA \
  --service-code XXX
```

## Troubleshooting

- If a user starts without `appKey`, `appSecret`, or `yhtUserId`, collect those 3 values first
- If token retrieval fails, verify the auth base URL, system clock, and secret pair
- If notification sending fails, re-check the tenant OpenAPI domain and the JSON field types
- If a send appears ignored, verify whether the same `srcMsgId` was already used
- If the token contains special characters, keep using encoded query parameters instead of manual string concatenation
