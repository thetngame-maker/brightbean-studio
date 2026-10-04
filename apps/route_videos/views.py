"""Route Video Generator workspace tool.

Milestone 1 turns an ordered list of destinations into a reusable route manifest:
geocoded stops, driving distance/time, per-leg stats, a full-route map preview,
and a deterministic clip plan for the later MP4 renderer.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

from .renderer import decode_polyline6, render_scene_mp4

MAX_STOPS = 25
MAPBOX_GEOCODE_URL = "https://api.mapbox.com/search/geocode/v6/forward"
MAPBOX_DIRECTIONS_URL = "https://api.mapbox.com/directions/v5/mapbox/driving"


def _get_workspace(request, workspace_id):
    workspace = get_object_or_404(Workspace, id=workspace_id)
    if not request.user.is_authenticated:
        raise PermissionDenied("Authentication required.")
    if not WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).exists():
        raise PermissionDenied("You are not a member of this workspace.")
    return workspace


def _mapbox_json(url, params):
    token = getattr(settings, "MAPBOX_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("MAPBOX_ACCESS_TOKEN is not configured.")

    query = urlencode({**params, "access_token": token})
    request = Request(
        f"{url}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "TN-Social-Studio/route-video-generator",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"Mapbox returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Mapbox could not be reached. Try again.") from exc


def _geocode_stop(query):
    payload = _mapbox_json(
        MAPBOX_GEOCODE_URL,
        {
            "q": query,
            "limit": 1,
            "language": "en",
        },
    )
    features = payload.get("features") or []
    if not features:
        raise ValueError(f'No location found for "{query}".')

    feature = features[0]
    coordinates = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coordinates) < 2:
        raise ValueError(f'No usable coordinates found for "{query}".')

    props = feature.get("properties") or {}
    label = (
        props.get("full_address")
        or props.get("name_preferred")
        or props.get("name")
        or feature.get("place_name")
        or query
    )
    return {
        "query": query,
        "name": label,
        "longitude": float(coordinates[0]),
        "latitude": float(coordinates[1]),
    }


def _static_map_url(stops, encoded_route):
    token = getattr(settings, "MAPBOX_ACCESS_TOKEN", "")
    overlays = []
    for stop in stops:
        overlays.append(
            "pin-s+f97316({:.6f},{:.6f})".format(stop["longitude"], stop["latitude"])
        )
    if encoded_route:
        overlays.append(f"path-5+f97316-0.9({quote(encoded_route, safe='')})")

    overlay = ",".join(overlays)
    # 720x1280 mirrors the 9:16 output we will render in the video worker.
    return (
        "https://api.mapbox.com/styles/v1/mapbox/outdoors-v12/static/"
        f"{overlay}/auto/720x1280"
        f"?padding=80&access_token={quote(token, safe='')}"
    )


def builder(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    return render(
        request,
        "route_videos/builder.html",
        {
            "workspace": workspace,
            "mapbox_configured": bool(getattr(settings, "MAPBOX_ACCESS_TOKEN", "")),
            "max_stops": MAX_STOPS,
        },
    )


@require_POST
def route_preview(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)

    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    raw_stops = body.get("stops") or []
    stops = [str(item).strip() for item in raw_stops if str(item).strip()]

    if len(stops) < 2:
        return JsonResponse({"error": "Add at least two stops."}, status=400)
    if len(stops) > MAX_STOPS:
        return JsonResponse({"error": f"A route can contain at most {MAX_STOPS} stops."}, status=400)

    if not getattr(settings, "MAPBOX_ACCESS_TOKEN", ""):
        return JsonResponse(
            {
                "error": "Mapbox is not configured yet.",
                "configuration_required": "MAPBOX_ACCESS_TOKEN",
            },
            status=503,
        )

    try:
        geocoded = [_geocode_stop(stop) for stop in stops]
        coordinate_string = ";".join(
            f'{stop["longitude"]:.6f},{stop["latitude"]:.6f}' for stop in geocoded
        )
        directions = _mapbox_json(
            f"{MAPBOX_DIRECTIONS_URL}/{coordinate_string}",
            {
                "overview": "simplified",
                "geometries": "polyline6",
                "steps": "true",
            },
        )
        routes = directions.get("routes") or []
        if not routes:
            message = directions.get("message") or "Mapbox could not build a driving route."
            raise ValueError(message)

        route = routes[0]
        legs = route.get("legs") or []
        clip_legs = []
        for index, leg in enumerate(legs):
            if index + 1 >= len(geocoded):
                break
            leg_coordinates = []
            for step in leg.get("steps") or []:
                geometry = step.get("geometry") or ""
                step_coordinates = decode_polyline6(geometry) if geometry else []
                if leg_coordinates and step_coordinates and leg_coordinates[-1] == step_coordinates[0]:
                    step_coordinates = step_coordinates[1:]
                leg_coordinates.extend(step_coordinates)
            clip_legs.append(
                {
                    "index": index + 1,
                    "from": geocoded[index]["name"],
                    "to": geocoded[index + 1]["name"],
                    "distance_miles": round(float(leg.get("distance") or 0) / 1609.344, 1),
                    "duration_minutes": round(float(leg.get("duration") or 0) / 60),
                    "suggested_clip_seconds": 5,
                    "coordinates": leg_coordinates,
                }
            )

        distance_miles = round(float(route.get("distance") or 0) / 1609.344, 1)
        duration_seconds = float(route.get("duration") or 0)
        duration_minutes = round(duration_seconds / 60)
        hours, minutes = divmod(duration_minutes, 60)
        duration_label = f"{hours} hr {minutes} min" if hours else f"{minutes} min"

        encoded_route = route.get("geometry") or ""
        route_coordinates = decode_polyline6(encoded_route) if encoded_route else []
        manifest = {
            "schema_version": 1,
            "workspace_id": str(workspace.id),
            "format": {"width": 1080, "height": 1920, "fps": 30},
            "route": {
                "profile": "driving",
                "distance_miles": distance_miles,
                "duration_seconds": round(duration_seconds),
                "duration_label": duration_label,
                "geometry_polyline6": encoded_route,
                "coordinates": route_coordinates,
            },
            "stops": geocoded,
            "clips": {
                "overview": {
                    "type": "overview",
                    "suggested_duration_seconds": 5,
                    "show_all_stops": True,
                    "coordinates": route_coordinates,
                },
                "legs": clip_legs,
            },
        }

        return JsonResponse(
            {
                "distance_miles": distance_miles,
                "duration_seconds": round(duration_seconds),
                "duration_label": duration_label,
                "stops": geocoded,
                "legs": clip_legs,
                "static_map_url": _static_map_url(geocoded, encoded_route),
                "manifest": manifest,
            }
        )
    except (RuntimeError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def render_clip(request, workspace_id):
    """Render one overview or leg scene and stream the MP4 response."""
    _get_workspace(request, workspace_id)
    token = getattr(settings, "MAPBOX_ACCESS_TOKEN", "")
    if not token:
        return JsonResponse({"error": "Mapbox is not configured yet."}, status=503)

    try:
        body = json.loads(request.body.decode("utf-8"))
        manifest = body.get("manifest") or {}
        route = manifest.get("route") or {}
        stops = manifest.get("stops") or []
        clips = manifest.get("clips") or {}
        clip_type = str(body.get("clip_type") or "overview")
        clip_index = int(body.get("clip_index") or 0)

        if len(stops) < 2 or len(stops) > MAX_STOPS:
            raise ValueError("Invalid route manifest.")

        if clip_type == "overview":
            overview = clips.get("overview") or {}
            scene = {
                "type": "overview",
                "coordinates": overview.get("coordinates") or route.get("coordinates") or [],
                "stops": stops,
                "seconds": overview.get("suggested_duration_seconds") or 5,
                "fps": (manifest.get("format") or {}).get("fps") or 30,
                "title": body.get("title") or "Tennessee Road Trip",
                "subtitle": f'{len(stops)} stops · {route.get("distance_miles", 0)} mi · {route.get("duration_label", "")}',
                "map_style": body.get("map_style") or "outdoors-v12",
            }
            filename = "01-route-overview.mp4"
        elif clip_type == "leg":
            legs = clips.get("legs") or []
            if clip_index < 0 or clip_index >= len(legs):
                raise ValueError("Invalid route leg.")
            leg = legs[clip_index]
            scene = {
                "type": "leg",
                "coordinates": leg.get("coordinates") or [],
                "seconds": leg.get("suggested_clip_seconds") or 5,
                "fps": (manifest.get("format") or {}).get("fps") or 30,
                "title": f'{str(leg.get("from") or "").split(",")[0]} → {str(leg.get("to") or "").split(",")[0]}',
                "subtitle": f'{leg.get("distance_miles", 0)} mi · {leg.get("duration_minutes", 0)} min',
                "marker_label": body.get("marker_label") or "TN",
                "map_style": body.get("map_style") or "outdoors-v12",
                "camera_follow": bool(body.get("camera_follow", True)),
            }
            filename = f"{clip_index + 2:02d}-route-leg.mp4"
        else:
            raise ValueError("Unknown clip type.")

        coordinates = scene.get("coordinates") or []
        if len(coordinates) < 2 or len(coordinates) > 20000:
            raise ValueError("This clip has invalid route geometry.")

        rendered_path = render_scene_mp4(scene, token)
        response = FileResponse(open(rendered_path, "rb"), content_type="video/mp4", as_attachment=True, filename=filename)
        response._resource_closers.append(lambda: __import__("pathlib").Path(rendered_path).unlink(missing_ok=True))
        return response
    except (json.JSONDecodeError, UnicodeDecodeError, RuntimeError, ValueError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def start_render_pack(request, workspace_id):
    """Queue rendering of the overview + every leg into the workspace Media Library."""
    workspace = _get_workspace(request, workspace_id)

    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    manifest = body.get("manifest") or {}
    stops = manifest.get("stops") or []
    legs = ((manifest.get("clips") or {}).get("legs") or [])

    if len(stops) < 2 or len(stops) > MAX_STOPS:
        return JsonResponse({"error": "Invalid route manifest."}, status=400)
    if str(manifest.get("workspace_id") or "") != str(workspace.id):
        return JsonResponse({"error": "Route manifest does not belong to this workspace."}, status=400)

    route_coordinates = (manifest.get("route") or {}).get("coordinates") or []
    total_coordinate_count = len(route_coordinates)
    for leg in legs:
        coordinates = leg.get("coordinates") or []
        if len(coordinates) < 2 or len(coordinates) > 20000:
            return JsonResponse({"error": "A route leg has invalid geometry."}, status=400)
        total_coordinate_count += len(coordinates)
    if total_coordinate_count > 100000:
        return JsonResponse({"error": "This route is too detailed to render as one pack."}, status=400)

    manifest["title"] = str(body.get("title") or "Tennessee Road Trip")[:140]
    manifest["marker_label"] = str(body.get("marker_label") or "TN")[:8]
    manifest["map_style"] = str(body.get("map_style") or "outdoors-v12")[:80]

    from .models import RouteVideoRenderJob
    from .tasks import render_route_video_pack

    job = RouteVideoRenderJob.objects.create(
        workspace=workspace,
        created_by=request.user,
        manifest=manifest,
        total_clips=1 + len(legs),
    )
    render_route_video_pack(str(job.id))

    return JsonResponse(
        {
            "job_id": str(job.id),
            "status": job.status,
            "total_clips": job.total_clips,
        },
        status=202,
    )


def render_pack_status(request, workspace_id, job_id):
    """Return progress and Media Library destinations for one render pack."""
    workspace = _get_workspace(request, workspace_id)

    from django.urls import reverse

    from .models import RouteVideoRenderJob

    job = get_object_or_404(
        RouteVideoRenderJob,
        pk=job_id,
        workspace=workspace,
    )
    assets = []
    for asset_id in job.media_asset_ids or []:
        assets.append(
            {
                "id": asset_id,
                "detail_url": reverse(
                    "media_library:asset_detail",
                    kwargs={"workspace_id": workspace.id, "asset_id": asset_id},
                ),
            }
        )

    return JsonResponse(
        {
            "job_id": str(job.id),
            "status": job.status,
            "total_clips": job.total_clips,
            "completed_clips": job.completed_clips,
            "current_clip_label": job.current_clip_label,
            "error": job.error_message,
            "assets": assets,
            "media_library_url": reverse("media_library:index", kwargs={"workspace_id": workspace.id}),
        }
    )
