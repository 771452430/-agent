"""GitLab 文档树导入服务。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..schemas import GitLabTreeImportResponse, KnowledgeImportIssue
from .gitlab_settings_service import GitLabSettingsService
from .knowledge_store import KnowledgeStore


class GitLabImportError(RuntimeError):
    """GitLab 导入过程中的可诊断异常。"""

    def __init__(self, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ParsedGitLabTreeUrl:
    source_url: str
    scheme: str
    host: str
    project_path: str
    ref: str
    tree_path: str


@dataclass(frozen=True)
class GitLabBlobEntry:
    path: str


class GitLabImportService:
    """把 GitLab tree URL 抓取为知识树文档。"""

    def __init__(self, knowledge_store: KnowledgeStore, gitlab_settings_service: GitLabSettingsService) -> None:
        self.knowledge_store = knowledge_store
        self.gitlab_settings_service = gitlab_settings_service

    def import_tree(self, *, tree_url: str, parent_node_id: str | None = None) -> GitLabTreeImportResponse:
        runtime = self.gitlab_settings_service.get_runtime_settings()
        parsed = self._parse_tree_url(tree_url, allowed_hosts=runtime.allowed_hosts)
        token = (runtime.token or "").strip()
        if token == "":
            raise GitLabImportError(
                "服务端缺少 `GITLAB_IMPORT_TOKEN`，暂时无法导入 GitLab 文档。",
                status_code=500,
            )

        blob_entries = self._list_blob_entries(parsed, token)
        documents = []
        skipped_paths: list[str] = []
        failed_items: list[KnowledgeImportIssue] = []
        created_count = 0
        updated_count = 0

        for entry in blob_entries:
            relative_path = self._build_relative_path(parsed, entry.path)
            if relative_path == "":
                continue

            if not self.knowledge_store.is_supported_document(relative_path):
                skipped_paths.append(relative_path)
                continue

            try:
                file_bytes = self._fetch_raw_file(parsed, token, entry.path)
                document, updated = self.knowledge_store.upsert_document(
                    parent_node_id=parent_node_id,
                    relative_path=relative_path,
                    file_bytes=file_bytes,
                    external_url=self._build_blob_url(parsed, entry.path),
                )
            except (GitLabImportError, ValueError) as exc:
                failed_items.append(KnowledgeImportIssue(path=relative_path, reason=str(exc)))
                continue

            documents.append(document)
            if document.status == "error":
                failed_items.append(
                    KnowledgeImportIssue(
                        path=relative_path,
                        reason=document.error_message or "文档入库失败",
                    )
                )
                continue
            if updated:
                updated_count += 1
            else:
                created_count += 1

        return GitLabTreeImportResponse(
            source_url=parsed.source_url,
            created_count=created_count,
            updated_count=updated_count,
            skipped_count=len(skipped_paths),
            failed_count=len(failed_items),
            skipped_paths=skipped_paths,
            failed_items=failed_items,
            documents=documents,
        )

    def _parse_tree_url(self, tree_url: str, *, allowed_hosts: list[str]) -> ParsedGitLabTreeUrl:
        normalized = tree_url.strip()
        if normalized == "":
            raise GitLabImportError("GitLab 文档地址不能为空。", status_code=400)

        parsed = urllib.parse.urlparse(normalized)
        if parsed.scheme not in {"http", "https"}:
            raise GitLabImportError("GitLab 文档地址必须以 http:// 或 https:// 开头。", status_code=400)

        host = parsed.netloc.lower().strip()
        if host == "":
            raise GitLabImportError("GitLab 文档地址缺少域名。", status_code=400)

        normalized_allowed_hosts = {item.lower().strip() for item in allowed_hosts if item.strip() != ""}
        if host not in normalized_allowed_hosts:
            raise GitLabImportError(
                f"当前只允许导入这些 GitLab 域名：{', '.join(sorted(normalized_allowed_hosts))}",
                status_code=400,
            )

        marker = "/-/tree/"
        raw_path = parsed.path.rstrip("/")
        if marker not in raw_path:
            raise GitLabImportError("当前只支持 GitLab `/-/tree/<ref>/<path>` 文档树地址。", status_code=400)

        project_part, tree_part = raw_path.split(marker, 1)
        project_path = urllib.parse.unquote(project_part.strip("/"))
        if project_path == "":
            raise GitLabImportError("GitLab 文档地址里缺少项目路径。", status_code=400)

        tree_segments = [urllib.parse.unquote(segment) for segment in tree_part.split("/") if segment != ""]
        if len(tree_segments) < 2:
            raise GitLabImportError("当前只支持 `/-/tree/<ref>/<path>` 格式，必须包含目录路径。", status_code=400)

        ref = tree_segments[0].strip()
        tree_path = "/".join(segment.strip("/") for segment in tree_segments[1:] if segment.strip("/") != "")
        if ref == "" or tree_path == "":
            raise GitLabImportError("GitLab 文档地址里的 ref 或目录路径为空。", status_code=400)

        source_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, raw_path, "", "", ""))
        return ParsedGitLabTreeUrl(
            source_url=source_url,
            scheme=parsed.scheme,
            host=host,
            project_path=project_path,
            ref=ref,
            tree_path=tree_path,
        )

    def _list_blob_entries(self, parsed: ParsedGitLabTreeUrl, token: str) -> list[GitLabBlobEntry]:
        entries: list[GitLabBlobEntry] = []
        page = 1
        while True:
            payload, headers = self._request_json(self._build_tree_api_url(parsed, page=page), token)
            if not isinstance(payload, list):
                raise GitLabImportError("GitLab tree 接口返回格式异常。", status_code=502)

            for item in payload:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "") != "blob":
                    continue
                file_path = str(item.get("path") or "").strip("/")
                if file_path == "":
                    continue
                entries.append(GitLabBlobEntry(path=file_path))

            next_page = str(headers.get("X-Next-Page") or headers.get("x-next-page") or "").strip()
            if next_page == "":
                break
            try:
                page = int(next_page)
            except ValueError:
                break
        return entries

    def _fetch_raw_file(self, parsed: ParsedGitLabTreeUrl, token: str, file_path: str) -> bytes:
        return self._request_bytes(self._build_raw_file_api_url(parsed, file_path), token)

    def _build_tree_api_url(self, parsed: ParsedGitLabTreeUrl, *, page: int) -> str:
        project_id = urllib.parse.quote(parsed.project_path, safe="")
        query = urllib.parse.urlencode(
            {
                "ref": parsed.ref,
                "path": parsed.tree_path,
                "recursive": "true",
                "per_page": "100",
                "page": str(page),
            }
        )
        return f"{parsed.scheme}://{parsed.host}/api/v4/projects/{project_id}/repository/tree?{query}"

    def _build_raw_file_api_url(self, parsed: ParsedGitLabTreeUrl, file_path: str) -> str:
        project_id = urllib.parse.quote(parsed.project_path, safe="")
        encoded_file_path = urllib.parse.quote(file_path, safe="")
        query = urllib.parse.urlencode({"ref": parsed.ref})
        return f"{parsed.scheme}://{parsed.host}/api/v4/projects/{project_id}/repository/files/{encoded_file_path}/raw?{query}"

    def _build_blob_url(self, parsed: ParsedGitLabTreeUrl, file_path: str) -> str:
        project_path = urllib.parse.quote(parsed.project_path, safe="/")
        encoded_ref = urllib.parse.quote(parsed.ref, safe="")
        encoded_file_path = urllib.parse.quote(file_path, safe="/")
        return f"{parsed.scheme}://{parsed.host}/{project_path}/-/blob/{encoded_ref}/{encoded_file_path}"

    def _build_relative_path(self, parsed: ParsedGitLabTreeUrl, file_path: str) -> str:
        normalized_file_path = file_path.strip("/")
        normalized_tree_path = parsed.tree_path.strip("/")
        if normalized_tree_path != "":
            prefix = normalized_tree_path + "/"
            if normalized_file_path.startswith(prefix):
                return normalized_file_path[len(prefix):]
            if normalized_file_path == normalized_tree_path:
                return PurePosixPath(normalized_file_path).name
        return normalized_file_path

    def _request_json(self, url: str, token: str) -> tuple[object, dict[str, str]]:
        raw_body, headers = self._request(url, token)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            snippet = raw_body.decode("utf-8", errors="ignore")[:300]
            raise GitLabImportError(f"GitLab 返回了无法解析的 JSON：{snippet}", status_code=502) from exc
        return payload, headers

    def _request_bytes(self, url: str, token: str) -> bytes:
        raw_body, _headers = self._request(url, token)
        return raw_body

    def _request(self, url: str, token: str) -> tuple[bytes, dict[str, str]]:
        request = urllib.request.Request(
            url,
            headers={
                "PRIVATE-TOKEN": token,
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "agentDemo-gitlab-importer/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read(), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:400].strip()
            if exc.code in {401, 403}:
                raise GitLabImportError(
                    "GitLab 认证失败，请检查服务端 `GITLAB_IMPORT_TOKEN` 是否有效。",
                    status_code=502,
                ) from exc
            if exc.code == 404:
                raise GitLabImportError("GitLab 上游返回 404，请确认项目、ref 或目录路径存在。", status_code=502) from exc
            raise GitLabImportError(
                f"GitLab 接口失败：HTTP {exc.code}{f' - {detail}' if detail else ''}",
                status_code=502,
            ) from exc
        except urllib.error.URLError as exc:
            raise GitLabImportError(f"访问 GitLab 失败：{exc.reason}", status_code=502) from exc
