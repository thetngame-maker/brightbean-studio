"""Recovery helpers for UGC media whose database row outlived its stored file."""

from __future__ import annotations

import logging
from functools import wraps

from apps.media_library.models import MediaAsset

logger = logging.getLogger(__name__)


def _asset_file_exists(asset: MediaAsset | None) -> bool:
    """Return True only when a MediaAsset still has a backing storage object."""
    if not asset or not asset.file or not asset.file.name:
        return False
    try:
        return bool(asset.file.storage.exists(asset.file.name))
    except Exception:
        logger.exception("Could not verify UGC media asset %s", getattr(asset, "id", None))
        return False


def install_missing_file_recovery() -> None:
    """Wrap UGC gallery capture so stale MediaAsset rows are recaptured."""
    from . import ugc_remote_media

    original = ugc_remote_media.capture_submission_gallery
    if getattr(original, "_ugc_missing_file_recovery", False):
        return

    @wraps(original)
    def recovered_capture(submission):
        if submission.media_asset_id and not _asset_file_exists(submission.media_asset):
            metadata = dict(submission.metadata or {})
            discovery = dict(metadata.get("discovery_import") or {})
            discovery["media_capture_status"] = "missing_file"
            discovery.pop("media_asset_id", None)
            metadata["discovery_import"] = discovery
            submission.media_asset = None
            submission.metadata = metadata
            submission.save(update_fields=["media_asset", "metadata", "updated_at"])
        return original(submission)

    recovered_capture._ugc_missing_file_recovery = True
    ugc_remote_media.capture_submission_gallery = recovered_capture
