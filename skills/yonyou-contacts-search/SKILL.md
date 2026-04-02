---
name: yonyou-contacts-search
description: Query the Yonyou contacts search API with a short account or full email. Use when Codex needs to look up a Yonyou contact by account name, auto-append `@yonyou.com`, ask the user for a fresh Cookie each time, and return the raw JSON response from the contacts search endpoint.
---

# Yonyou Contacts Search

## Required Values First

Always ask the user for a fresh `Cookie` before doing anything else.

If the user gives a short account such as `liugangy`, convert it to `liugangy@yonyou.com`.
If the user already gives a full email address, use it as-is.

Do not reuse old Cookies. Do not save Cookies into repo files, examples, logs, or long-lived env vars.

## Quick Start

1. Ask the user for the current request Cookie first.
2. Read the short account or full email.
3. If the input does not contain `@`, append `@yonyou.com`.
4. Run `scripts/search_contacts.py`.
5. Return the raw JSON response.

## Workflow

### 1. Collect inputs

Required values:

- `Cookie`
- account input such as `liugangy` or `liugangy@yonyou.com`

Optional values:

- `timeout`

### 2. Normalize the account

- `liugangy` -> `liugangy@yonyou.com`
- `liugangy@yonyou.com` -> unchanged

### 3. Build the request

- Use the fixed URL template in `references/api.md`
- Replace only the `keywords` query parameter
- Keep all other query parameters unchanged
- Send a GET request with the provided `Cookie`

### 4. Return the result

- On success, print and return the raw JSON response
- On failure, surface HTTP status, response body snippet, or request exception details

## Bundled Resources

- `scripts/search_contacts.py`: Prompt for missing Cookie, normalize the account, call the contacts search API, and print raw JSON
- `references/api.md`: Fixed URL template, parameter rules, request headers, and safety notes

## Common Command

Run from the skill folder:

```bash
python3 scripts/search_contacts.py \
  --account liugangy
```

If `--cookie` is omitted, the script will prompt for it.

## Troubleshooting

- If the user has not provided a Cookie, stop and ask for it first
- If the input account has no `@`, append `@yonyou.com`
- If the API returns non-JSON content, show the HTTP status and raw response snippet
- If the request fails, keep the fixed URL template unchanged except for `keywords`
