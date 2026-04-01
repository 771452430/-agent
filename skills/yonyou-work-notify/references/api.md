# Yonyou Idempotent Work Notify API

## 1. Self-app `access_token`

- Default upgraded path: `/iuap-api-auth/open-auth/selfAppAuth/base/v1/getAccessToken`
- Example host in the doc: `https://c2.yonyoucloud.com`
- In practice, many tenants need the token request to hit the same data-center host as the business OpenAPI domain; otherwise business calls can return `非法token`
- Method: `GET`
- Query parameters:
  - `appKey`
  - `timestamp` in Unix milliseconds
  - `signature`

### Signature rule

1. Sort parameters by name, excluding `signature`
2. Concatenate `key + value` in order
3. Compute `HmacSHA256` with `appSecret`
4. Base64-encode the binary digest
5. URL-encode the Base64 string

Example source string:

```text
appKey41832a3d2df94989b500da6a22268747timestamp1568098531823
```

### Success response

```json
{
  "code": "00000",
  "message": "成功！",
  "data": {
    "access_token": "b8743244c5b44b8fb1e52a55be7e2f",
    "expire": 7200
  }
}
```

## 2. Idempotent work notification push

- Path in current business doc: `/yonbip/uspace/rest/openapi/idempotent/work/notify/push`
- Data-center migration note: some tenants on new gateway routes need `/iuap-api-gateway/yonbip/uspace/rest/openapi/idempotent/work/notify/push`
- Method: `POST`
- Content-Type: `application/json`
- Query parameter: `access_token`
- Domain: tenant-specific OpenAPI/data-center domain

### Required body fields

- `srcMsgId`: idempotent message key; recommended format `业务标识:唯一编码`
- `yhtUserIds`: array of 友互通 user IDs
- `title`
- `content`

### Frequently used optional fields

- `labelCode`: web 分类
- `url`: mobile open URL
- `webUrl`: web open URL
- `miniProgramUrl`: 友空间小程序地址
- `appId`: 友空间 appId or 工作台 `serviceCode`
- `tabId`: mobile custom category
- `esnData`: array/object business attributes; include `fromId` when read-mark logic needs a business ID
- `attributes`: custom extension attributes
- `catcode1st`: category ID
- `serviceCode`: used for message icon/service association

### Success response

```json
{
  "code": "200",
  "message": "成功",
  "data": {
    "flag": 0,
    "msg": "success"
  },
  "displayCode": ""
}
```

## 3. Failure hints

- HTTP `400`: parameter error, usually wrong type or missing required field
- Business success must still check `data.flag`
- Reusing the same `srcMsgId` returns success without re-sending

## 4. Field tips

- Keep request encoding as UTF-8
- Let your HTTP library encode query parameters; this is important because new tokens can contain special characters
- `serviceCode` is useful when the message should inherit the configured Workbench service icon
