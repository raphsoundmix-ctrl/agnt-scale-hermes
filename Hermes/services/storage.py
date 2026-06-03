"""
S3-compatible storage client (MinIO).

Uploads generated images/videos to the `mao-visual` bucket. Returns
public URLs constructed from MINIO_PUBLIC_URL (so the browser can fetch
them directly when the bucket policy is public-read).

Key layout:
  {tenant_id}/{cabinet_id}/avatars/{avatar_id}/preview.png
  {tenant_id}/{cabinet_id}/briefs/{brief_id}/source/{n}.jpg
  {tenant_id}/{cabinet_id}/generations/{generation_id}/{slide_index}.{ext}
  {tenant_id}/moodboards/{moodboard_id}/preview.png
"""
from __future__ import annotations

import io
import ipaddress
import logging
import mimetypes
from typing import Optional
from urllib.parse import urlparse

import boto3
import httpx
from botocore.config import Config

from config import settings

logger = logging.getLogger("hermes.storage")

_client = None


# ─── SSRF protection ─────────────────────────────────────────────────────
# upload_from_url() fetches user-influenceable URLs (e.g. asset URLs returned
# by OpenRouter media APIs). Without these guards an attacker who can place
# a URL in our pipeline could pivot to internal services (link-local
# metadata at 169.254.169.254, container networks, private RFC1918 ranges)
# or fetch over plaintext http://. We allow only an explicit list of hosts
# and reject any URL whose hostname literally parses as a private IP.
_ALLOWED_HOSTS = {"openrouter.ai", "files.openrouter.ai", "cdn.openrouter.ai"}
_BLOCKED_NETS = [
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _validate_outbound_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme != "https":
        raise ValueError(f"Only https allowed, got: {p.scheme}")
    host = (p.hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        raise ValueError(f"Host not in allowlist: {host}")
    # Defense in depth: if the URL hostname itself is a literal IP that
    # somehow snuck onto the allowlist (or in a future where the allowlist
    # is widened), still reject private/link-local ranges.
    ip = None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None  # hostname, not IP — allowlist already checked
    if ip is not None:
        for net in _BLOCKED_NETS:
            if ip in net:
                raise ValueError(f"Blocked private/link-local IP: {ip}")


def _make_client():
    """Create S3 client pointing at MinIO. Cached at module level."""
    global _client
    if _client is not None:
        return _client

    endpoint = settings.MINIO_ENDPOINT
    # boto3 wants region; MinIO ignores it but requires a non-empty value
    _client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.MINIO_ROOT_USER,
        aws_secret_access_key=settings.MINIO_ROOT_PASSWORD,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return _client


def ensure_bucket(bucket: Optional[str] = None) -> None:
    """Create the bucket if it doesn't exist. Safe to call at startup."""
    b = bucket or settings.MINIO_BUCKET
    s3 = _make_client()
    try:
        s3.head_bucket(Bucket=b)
    except Exception:
        try:
            s3.create_bucket(Bucket=b)
            logger.info(f"Created MinIO bucket: {b}")
        except Exception as e:
            logger.warning(f"Could not create bucket {b}: {e}")


def public_url(path: str) -> str:
    """Build the public-read URL for an object key."""
    base = settings.MINIO_PUBLIC_URL.rstrip("/")
    return f"{base}/{settings.MINIO_BUCKET}/{path}"


def upload_bytes(
    path: str,
    data: bytes,
    content_type: Optional[str] = None,
) -> str:
    """Upload bytes → returns the public URL."""
    if content_type is None:
        guessed, _ = mimetypes.guess_type(path)
        content_type = guessed or "application/octet-stream"

    s3 = _make_client()
    s3.put_object(
        Bucket=settings.MINIO_BUCKET,
        Key=path,
        Body=data,
        ContentType=content_type,
    )
    return public_url(path)


async def upload_from_url(
    src_url: str,
    dest_path: str,
    content_type: Optional[str] = None,
) -> str:
    """
    Download a remote asset (e.g. from OpenRouter) and re-upload to MinIO.
    Returns the public MinIO URL.
    """
    # SSRF guard: only https, allowlisted hosts, never private/link-local IPs.
    _validate_outbound_url(src_url)
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        r = await client.get(src_url)
        r.raise_for_status()
        ct = content_type or r.headers.get("content-type") or "application/octet-stream"
        return upload_bytes(dest_path, r.content, content_type=ct)


def get_object_bytes(path: str) -> bytes:
    """Read an object's raw bytes by key. Used to hand a generated slide
    to an external provider (fal/Kling) as a base64 data URI when MinIO
    isn't publicly reachable."""
    s3 = _make_client()
    obj = s3.get_object(Bucket=settings.MINIO_BUCKET, Key=path)
    return obj["Body"].read()


def delete(path: str) -> None:
    """Delete a single object."""
    s3 = _make_client()
    s3.delete_object(Bucket=settings.MINIO_BUCKET, Key=path)


def object_key_from_url(url: str) -> Optional[str]:
    """Extract `{bucket}/{key}` → `{key}` from a public URL we issued."""
    try:
        parsed = urlparse(url)
        path = parsed.path.lstrip("/")
        if path.startswith(settings.MINIO_BUCKET + "/"):
            return path[len(settings.MINIO_BUCKET) + 1 :]
        return None
    except Exception:
        return None
