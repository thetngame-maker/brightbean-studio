"""Background generation of complete Route Video clip packs."""

from pathlib import Path

from background_task import background
from django.core.files import File
from django.utils import timezone
from django.utils.text import slugify

from apps.media_library.models import MediaFolder
from apps.media_library.services import create_asset
from apps.media_library.tasks import process_media_asset

from .models import RouteVideoRenderJob
from .renderer import render_scene_mp4


def _short_name(value):
    return str(value or "").split(",")[0].strip()


def _build_scenes(manifest):
    route = manifest.get("route") or {}
    stops = manifest.get("stops") or []
    clips = manifest.get("clips") or {}
    fmt = manifest.get("format") or {}
    fps = int(fmt.get("fps") or 30)

    overview = clips.get("overview") or {}
    yield {
        "filename": "01-route-overview.mp4",
        "label": "Overview",
        "scene": {
            "type": "overview",
            "coordinates": overview.get("coordinates") or route.get("coordinates") or [],
            "stops": stops,
            "seconds": overview.get("suggested_duration_seconds") or 5,
            "fps": fps,
            "title": manifest.get("title") or "Tennessee Road Trip",
            "subtitle": f'{len(stops)} stops · {route.get("distance_miles", 0)} mi · {route.get("duration_label", "")}',
            "map_style": manifest.get("map_style") or "outdoors-v12",
        },
    }

    for index, leg in enumerate(clips.get("legs") or []):
        start = _short_name(leg.get("from"))
        end = _short_name(leg.get("to"))
        slug_start = slugify(start)[:40] or "start"
        slug_end = slugify(end)[:40] or "stop"
        yield {
            "filename": f"{index + 2:02d}-{slug_start}-to-{slug_end}.mp4",
            "label": f"{start} → {end}",
            "scene": {
                "type": "leg",
                "coordinates": leg.get("coordinates") or [],
                "seconds": leg.get("suggested_clip_seconds") or 5,
                "fps": fps,
                "title": f"{start} → {end}",
                "subtitle": f'{leg.get("distance_miles", 0)} mi · {leg.get("duration_minutes", 0)} min',
                "marker_label": manifest.get("marker_label") or "TN",
                "map_style": manifest.get("map_style") or "outdoors-v12",
                "camera_follow": True,
            },
        }


@background(schedule=0)
def render_route_video_pack(job_id):
    """Render all clips for one job and save each clip into the Media Library."""
    try:
        job = RouteVideoRenderJob.objects.select_related("workspace__organization", "created_by").get(pk=job_id)
    except RouteVideoRenderJob.DoesNotExist:
        return

    from django.conf import settings

    token = getattr(settings, "MAPBOX_ACCESS_TOKEN", "")
    if not token:
        job.status = RouteVideoRenderJob.Status.FAILED
        job.error_message = "MAPBOX_ACCESS_TOKEN is not configured."
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error_message", "completed_at", "updated_at"])
        return

    job.status = RouteVideoRenderJob.Status.RENDERING
    job.started_at = timezone.now()
    job.error_message = ""
    job.save(update_fields=["status", "started_at", "error_message", "updated_at"])

    try:
        folder, _ = MediaFolder.objects.get_or_create(
            organization=job.workspace.organization,
            workspace=job.workspace,
            parent_folder=None,
            name="Route Videos",
        )
        asset_ids = list(job.media_asset_ids or [])

        for position, item in enumerate(_build_scenes(job.manifest), start=1):
            scene = item["scene"]
            coordinates = scene.get("coordinates") or []
            if len(coordinates) < 2:
                raise ValueError(f'{item["label"]} has no usable route geometry.')

            job.current_clip_label = item["label"]
            job.save(update_fields=["current_clip_label", "updated_at"])

            rendered_path = render_scene_mp4(scene, token)
            try:
                with open(rendered_path, "rb") as handle:
                    uploaded = File(handle, name=item["filename"])
                    asset = create_asset(
                        organization=job.workspace.organization,
                        workspace=job.workspace,
                        uploaded_file=uploaded,
                        uploaded_by=job.created_by,
                        folder=folder,
                        title=item["label"],
                        tags=["route-video", "generated"],
                    )
                asset.source = "route_video_generator"
                asset.save(update_fields=["source", "updated_at"])
                process_media_asset(str(asset.id))
                asset_ids.append(str(asset.id))
            finally:
                Path(rendered_path).unlink(missing_ok=True)

            job.media_asset_ids = asset_ids
            job.completed_clips = position
            job.save(update_fields=["media_asset_ids", "completed_clips", "updated_at"])

        job.status = RouteVideoRenderJob.Status.COMPLETED
        job.current_clip_label = ""
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "current_clip_label", "completed_at", "updated_at"])
    except Exception as exc:
        job.status = RouteVideoRenderJob.Status.FAILED
        job.error_message = str(exc)[:2000]
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error_message", "completed_at", "updated_at"])
