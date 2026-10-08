import logging
import re
import uuid
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests

from config import DOWNLOAD_DIR, DOWNLOAD_TIMEOUT, MAX_REDIRECTS, MAX_STORAGE_FILE_BYTES

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

_SENSITIVE_QUERY_KEYS = {
    "token",
    "auth",
    "authorization",
    "cookie",
    "cookies",
    "key",
    "signature",
    "sig",
    "session",
    "sessionid",
    "api_key",
    "api_hash",
    "bot_token",
    "exp",
    "expires",
    "secret",
}


class DownloadTooLargeError(Exception):
    """Raised when a download exceeds the application's storage-file limit."""


def _sanitize_url_for_log(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid-url>"

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in _SENSITIVE_QUERY_KEYS:
            query_items.append((key, "[REDACTED]"))
        else:
            query_items.append((key, value))

    safe_query = urlencode(query_items)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", safe_query))


@dataclass(frozen=True)
class DownloadedFile:
    path: Path
    filename: str
    size: int


def validate_http_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError as error:
        raise ValueError("Invalid URL") from error

    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise ValueError("Only direct HTTP and HTTPS URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not supported")
    return url


def _filename_from_response(response: requests.Response, url: str) -> str:
    message = Message()
    message["Content-Disposition"] = response.headers.get("Content-Disposition", "")
    filename = message.get_filename()

    if not filename:
        filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])

    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")[:180]
    if not filename or filename in {".", ".."}:
        filename = "download"

    reserved_name = filename.split(".", 1)[0].upper()
    if reserved_name in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(
        r"(?:COM|LPT)[1-9]", reserved_name
    ):
        filename = f"_{filename}"

    return filename


def download_file(url: str) -> DownloadedFile:
    current_url = validate_http_url(url)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    sanitized_start_url = _sanitize_url_for_log(current_url)

    logger.info(
        "Starting download for %s (max_storage_bytes=%s max_redirects=%s timeout=%s)",
        sanitized_start_url,
        MAX_STORAGE_FILE_BYTES,
        MAX_REDIRECTS,
        DOWNLOAD_TIMEOUT,
    )

    try:
        with requests.Session() as session:
            session.headers.update({"User-Agent": USER_AGENT})
            session.max_redirects = MAX_REDIRECTS

            response = session.get(
                current_url,
                stream=True,
                timeout=DOWNLOAD_TIMEOUT,
                allow_redirects=True,
            )

            redirect_history = [
                {
                    "status_code": entry.status_code,
                    "url": _sanitize_url_for_log(entry.url),
                }
                for entry in response.history
            ]
            final_url = _sanitize_url_for_log(response.url or current_url)
            content_type = response.headers.get("Content-Type")
            content_length = response.headers.get("Content-Length")

            logger.info(
                "HTTP response: status=%s final_url=%s redirect_history=%s content_type=%s content_length=%s",
                response.status_code,
                final_url,
                redirect_history,
                content_type,
                content_length,
            )

            try:
                if response.status_code >= 400:
                    logger.error(
                        "Download failed with HTTP status: status=%s final_url=%s redirect_history=%s content_type=%s content_length=%s",
                        response.status_code,
                        final_url,
                        redirect_history,
                        content_type,
                        content_length,
                    )
                    response.raise_for_status()

                if content_length is not None:
                    try:
                        content_length_value = int(content_length)
                    except ValueError:
                        logger.warning(
                            "Ignoring invalid Content-Length header for %s: %s",
                            final_url,
                            content_length,
                        )
                    else:
                        logger.info(
                            "Content-Length check: final_url=%s content_length=%s max_allowed=%s",
                            final_url,
                            content_length_value,
                            MAX_STORAGE_FILE_BYTES,
                        )
                        if content_length_value > MAX_STORAGE_FILE_BYTES:
                            logger.error(
                                "Rejected download: Content-Length exceeds storage limit: final_url=%s content_length=%s limit=%s",
                                final_url,
                                content_length_value,
                                MAX_STORAGE_FILE_BYTES,
                            )
                            raise DownloadTooLargeError

                filename = _filename_from_response(response, final_url)
                temporary_path = DOWNLOAD_DIR / f"{uuid.uuid4().hex}_{filename}"
                downloaded_bytes = 0

                with temporary_path.open("wb") as output_file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        downloaded_bytes += len(chunk)
                        if downloaded_bytes > MAX_STORAGE_FILE_BYTES:
                            logger.error(
                                "Rejected download during streaming: final_url=%s downloaded_bytes=%s limit=%s",
                                final_url,
                                downloaded_bytes,
                                MAX_STORAGE_FILE_BYTES,
                            )
                            raise DownloadTooLargeError
                        output_file.write(chunk)

                if downloaded_bytes == 0:
                    raise requests.RequestException("The response contained no file data")

                logger.info(
                    "Downloaded successfully: filename=%s bytes=%s final_url=%s content_type=%s",
                    filename,
                    downloaded_bytes,
                    final_url,
                    content_type,
                )
                return DownloadedFile(temporary_path, filename, downloaded_bytes)
            finally:
                response.close()
    except DownloadTooLargeError:
        logger.exception("Download rejected due to size limit: %s", sanitized_start_url)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    except requests.RequestException as exc:
        error_response = exc.response
        response_status = None
        response_headers = {}
        response_history = []
        if error_response is not None:
            response_status = error_response.status_code
            response_headers = error_response.headers
            response_history = error_response.history
        logger.exception(
            "Download request failed: status=%s final_url=%s redirect_history=%s content_type=%s content_length=%s",
            response_status,
            _sanitize_url_for_log(current_url),
            [
                {"status_code": step.status_code, "url": _sanitize_url_for_log(step.url)}
                for step in response_history
            ],
            response_headers.get("Content-Type") if response_headers else None,
            response_headers.get("Content-Length") if response_headers else None,
        )
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    except Exception:
        logger.exception("Unexpected download failure: %s", sanitized_start_url)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise