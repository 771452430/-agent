# Yonyou Contacts Search API

## Fixed URL Template

Use this exact URL template and replace only `keywords`:

```text
https://c2.yonyoucloud.com/yonbip-ec-contacts/contacts/pcUser/pc/search?vercode=8.3.15&language=zh_CN&locale=zh_CN&et=1775097041.027&uspace_product_line=pc&keywords=liugangy%40yonyou.com&pageNum=1&pageSize=10&crossTenant=1&esnAttr=1&ek2=86191eb5fa978f3fa2faa3ac16a3e40bf7b68d18c1f3ed984c61b0afa702c5b8
```

## Input Rule

- Short account input: append `@yonyou.com`
- Full email input: use as-is

## Request Rule

- Method: `GET`
- Required header: `Cookie`
- Recommended headers:
  - `Accept: application/json, text/plain, */*`
  - `User-Agent: Mozilla/5.0`

## Safety Notes

- Always ask for a fresh Cookie at runtime
- Do not write Cookies into repo files
- Do not change query parameters other than `keywords`
