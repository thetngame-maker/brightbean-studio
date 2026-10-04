"""Video rendering helpers for Route Videos.

The renderer fetches one Mapbox base image for a scene, projects route
coordinates into that fixed camera, paints route progress / labels with Pillow,
and streams raw RGB frames directly into ffmpeg. No per-frame Mapbox calls.
"""

import io
import math
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

BASE_WIDTH = 720
BASE_HEIGHT = 1280
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
DEFAULT_FPS = 30
DEFAULT_SECONDS = 5
TILE_SIZE = 512
MAX_ZOOM = 15.5
MIN_ZOOM = 3.0
ALLOWED_MAP_STYLES = {
    "outdoors-v12",
    "streets-v12",
    "light-v11",
    "dark-v11",
    "satellite-streets-v12",
}


def _safe_map_style(value):
    value = str(value or "outdoors-v12")
    return value if value in ALLOWED_MAP_STYLES else "outdoors-v12"


def decode_polyline6(encoded):
    """Decode a Google/Mapbox polyline6 string into [lon, lat] coordinates."""
    if not encoded:
        return []
    coords = []
    index = 0
    lat = 0
    lon = 0
    factor = 1_000_000
    length = len(encoded)

    while index < length:
        values = []
        for _ in range(2):
            result = 0
            shift = 0
            while True:
                if index >= length:
                    raise ValueError("Invalid encoded polyline.")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            values.append(delta)
        lat += values[0]
        lon += values[1]
        coords.append([lon / factor, lat / factor])
    return coords


def mercator_world(lon, lat, zoom):
    lat = max(min(float(lat), 85.05112878), -85.05112878)
    scale = TILE_SIZE * (2**zoom)
    x = (float(lon) + 180.0) / 360.0 * scale
    sin_lat = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * scale
    return x, y


def _bounds_center(coords):
    lons = [float(c[0]) for c in coords]
    lats = [float(c[1]) for c in coords]
    return (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2


def choose_camera(coords, width=BASE_WIDTH, height=BASE_HEIGHT, padding=100):
    """Return lon, lat, zoom fitting coords into a fixed static-map viewport."""
    if not coords:
        return -86.0, 35.8, 5.0

    center_lon, center_lat = _bounds_center(coords)
    usable_w = max(width - 2 * padding, 100)
    usable_h = max(height - 2 * padding, 100)

    best_zoom = MIN_ZOOM
    for zoom in [MIN_ZOOM + i * 0.25 for i in range(int((MAX_ZOOM - MIN_ZOOM) / 0.25) + 1)]:
        center_x, center_y = mercator_world(center_lon, center_lat, zoom)
        points = [mercator_world(lon, lat, zoom) for lon, lat in coords]
        span_x = max(abs(x - center_x) for x, _ in points) * 2
        span_y = max(abs(y - center_y) for _, y in points) * 2
        if span_x <= usable_w and span_y <= usable_h:
            best_zoom = zoom
        else:
            break
    return center_lon, center_lat, best_zoom


def project_to_image(coord, camera, width=BASE_WIDTH, height=BASE_HEIGHT):
    lon, lat = coord
    center_lon, center_lat, zoom = camera
    center_x, center_y = mercator_world(center_lon, center_lat, zoom)
    x, y = mercator_world(lon, lat, zoom)
    return width / 2 + (x - center_x), height / 2 + (y - center_y)


def _base_map_url(token, camera, width=BASE_WIDTH, height=BASE_HEIGHT, style="outdoors-v12"):
    lon, lat, zoom = camera
    # Keep Mapbox attribution/logo defaults enabled in exported footage.
    return (
        f"https://api.mapbox.com/styles/v1/mapbox/{style}/static/"
        f"{lon:.6f},{lat:.6f},{zoom:.2f},0/{width}x{height}"
        f"?access_token={quote(token, safe='')}"
    )


def fetch_base_map(token, camera, style="outdoors-v12"):
    request = Request(
        _base_map_url(token, camera, style=style),
        headers={"User-Agent": "TN-Social-Studio/route-video-renderer"},
    )
    with urlopen(request, timeout=30) as response:
        return Image.open(io.BytesIO(response.read())).convert("RGB")


def _font(size, bold=False):
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _draw_polyline(draw, points, fill, width):
    clean = [(int(x), int(y)) for x, y in points if math.isfinite(x) and math.isfinite(y)]
    if len(clean) >= 2:
        draw.line(clean, fill=fill, width=width, joint="curve")


def _interpolate_path(points, progress):
    if not points:
        return None, []
    if len(points) == 1:
        return points[0], points

    progress = max(0.0, min(1.0, float(progress)))
    distances = [0.0]
    total = 0.0
    for a, b in zip(points, points[1:], strict=False):
        segment = math.hypot(b[0] - a[0], b[1] - a[1])
        total += segment
        distances.append(total)

    if total <= 0:
        return points[-1], points

    target = total * progress
    partial = [points[0]]
    for i in range(1, len(points)):
        if distances[i] < target:
            partial.append(points[i])
            continue
        prior = distances[i - 1]
        segment = max(distances[i] - prior, 1e-9)
        ratio = (target - prior) / segment
        x = points[i - 1][0] + (points[i][0] - points[i - 1][0]) * ratio
        y = points[i - 1][1] + (points[i][1] - points[i - 1][1]) * ratio
        marker = (x, y)
        partial.append(marker)
        return marker, partial
    return points[-1], points


def _draw_marker(draw, position, label="TN"):
    if not position:
        return
    x, y = position
    r = 23
    draw.ellipse((x - r - 4, y - r - 4, x + r + 4, y + r + 4), fill=(255, 255, 255), outline=(255, 255, 255), width=2)
    draw.ellipse((x - r, y - r, x + r, y + r), fill=(249, 115, 22))
    font = _font(16, bold=True)
    box = draw.textbbox((0, 0), label, font=font)
    draw.text((x - (box[2] - box[0]) / 2, y - (box[3] - box[1]) / 2 - 1), label, font=font, fill=(255, 255, 255))


def _draw_stop(draw, position, number):
    x, y = position
    r = 18
    draw.ellipse((x - r - 3, y - r - 3, x + r + 3, y + r + 3), fill=(255, 255, 255))
    draw.ellipse((x - r, y - r, x + r, y + r), fill=(249, 115, 22))
    font = _font(14, bold=True)
    value = str(number)
    box = draw.textbbox((0, 0), value, font=font)
    draw.text((x - (box[2] - box[0]) / 2, y - (box[3] - box[1]) / 2 - 1), value, font=font, fill=(255, 255, 255))


def _draw_bottom_card(draw, title, subtitle):
    left, top, right, bottom = 34, 1045, BASE_WIDTH - 34, 1210
    draw.rounded_rectangle((left, top, right, bottom), radius=24, fill=(255, 255, 255))
    title_font = _font(32, bold=True)
    sub_font = _font(22)
    draw.text((left + 28, top + 28), title, font=title_font, fill=(28, 25, 23))
    draw.text((left + 28, top + 80), subtitle, font=sub_font, fill=(87, 83, 78))


OVERVIEW_INTRO_SECONDS = 3
OVERVIEW_SECONDS = 8


def _overview_labels(stops, camera):
    """Lay out every name in separate callouts, linked to numbered map pins."""
    if not stops:
        return []
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    columns = 3
    rows = max(5, math.ceil(len(stops) / columns) + 1)
    width = 208
    cell_height = 960 / rows
    slots = [(20 + column * 236, 40 + row * cell_height) for row in range(rows) for column in range(columns)]
    points = [project_to_image([stop["longitude"], stop["latitude"]], camera) for stop in stops]
    labels = []
    for index, stop in enumerate(stops, start=1):
        text = f"{index}. {stop.get('name') or stop.get('location_name') or 'Stop'}"
        for size in range(14 if len(stops) > 10 else 19, 7, -1):
            font = _font(size, bold=True)
            lines = []
            remaining = text
            while remaining:
                count = len(remaining)
                while count > 1 and measure.textlength(remaining[:count], font=font) > width - 20:
                    count -= 1
                if count < len(remaining):
                    word_break = remaining.rfind(" ", 0, count)
                    if word_break > 0:
                        count = word_break + 1
                lines.append(remaining[:count])
                remaining = remaining[count:]
            label_text = "\n".join(lines)
            bbox = measure.multiline_textbbox((0, 0), label_text, font=font, spacing=3)
            height = bbox[3] - bbox[1] + 20
            if height <= cell_height - 10:
                break
        point = points[index - 1]

        def slot_score(slot, height=height, point=point):
            x, y = slot
            collisions = sum(x - 25 <= px <= x + width + 25 and y - 25 <= py <= y + height + 25 for px, py in points)
            overlaps = sum(
                not (
                    x + width + 8 <= label["box"][0]
                    or label["box"][2] + 8 <= x
                    or y + height + 8 <= label["box"][1]
                    or label["box"][3] + 8 <= y
                )
                for label in labels
            )
            return (overlaps, collisions, (x + width / 2 - point[0]) ** 2 + (y + height / 2 - point[1]) ** 2)

        candidates = list(slots)
        if len(stops) <= 10:
            for dx, dy in [(30, -height / 2), (-width - 30, -height / 2), (-width / 2, -height - 30), (-width / 2, 30)]:
                for shift in (0, -60, 60):
                    candidates.append(
                        (
                            max(20, min(point[0] + dx, BASE_WIDTH - width - 20)),
                            max(40, min(point[1] + dy + shift, 1010 - height)),
                        )
                    )
        slot = min(candidates, key=slot_score)
        if slot in slots:
            slots.remove(slot)
        x, y = slot
        labels.append(
            {
                "box": (x, y, x + width, y + height),
                "text": label_text,
                "font": font,
                "point": point,
                "number": index,
                "text_offset": bbox[1],
            }
        )
    return labels


def _draw_overview(draw, projected, labels, scene, elapsed):
    _draw_polyline(draw, projected, fill=(255, 255, 255), width=13)
    _draw_polyline(draw, projected, fill=(249, 115, 22), width=7)
    for label in labels:
        left, top, right, bottom = label["box"]
        x, y = label["point"]
        anchor = (max(left, min(x, right)), max(top, min(y, bottom)))
        draw.line([label["point"], anchor], fill=(255, 255, 255), width=5)
        draw.line([label["point"], anchor], fill=(154, 52, 18), width=2)
    for label in labels:
        _draw_stop(draw, label["point"], label["number"])
    for label in labels:
        left, top, _, _ = label["box"]
        draw.rounded_rectangle(label["box"], radius=9, fill=(255, 255, 255), outline=(249, 115, 22), width=2)
        draw.multiline_text(
            (left + 10, top + 10 - label["text_offset"]),
            label["text"],
            font=label["font"],
            spacing=3,
            fill=(28, 25, 23),
        )
    if elapsed >= OVERVIEW_INTRO_SECONDS:
        _draw_bottom_card(draw, scene.get("title") or "Road Trip", scene.get("subtitle") or "")


def _camera_follow_keyframes(coords, token, base_camera, style, count=10):
    """Pre-render a small set of moving-camera backgrounds for leg scenes.

    Static Images does not provide a continuous camera stream, so we cache a
    bounded number of camera positions and switch between them while overlays
    are projected against the matching camera. This gives the travel clip a
    map-follow feel without issuing a Mapbox request for every video frame.
    """
    count = max(4, min(int(count), 12))
    follow_zoom = min(MAX_ZOOM, max(10.5, float(base_camera[2]) + 1.75))
    frames = []
    last_index = max(len(coords) - 1, 1)
    for i in range(count):
        progress = i / max(count - 1, 1)
        coord_index = min(round(progress * last_index), len(coords) - 1)
        lon, lat = coords[coord_index]
        camera = (float(lon), float(lat), follow_zoom)
        frames.append((camera, fetch_base_map(token, camera, style=style)))
    return frames


def render_scene_mp4(scene, token, destination=None):
    """Render one overview or leg scene to an MP4 file and return its path."""
    coords = scene.get("coordinates") or []
    if len(coords) < 2:
        raise ValueError("Scene needs at least two route coordinates.")

    fps = int(scene.get("fps") or DEFAULT_FPS)
    overview = scene.get("type") == "overview"
    seconds = max(OVERVIEW_SECONDS if overview else 2, min(int(scene.get("seconds") or DEFAULT_SECONDS), 15))
    frame_count = fps * seconds
    stops = scene.get("stops") or []
    camera_coords = coords + [[stop["longitude"], stop["latitude"]] for stop in stops] if overview else coords
    camera = choose_camera(camera_coords, padding=130 if overview else 90)
    map_style = _safe_map_style(scene.get("map_style"))
    base = fetch_base_map(token, camera, style=map_style)
    follow_frames = None
    if scene.get("type") == "leg" and scene.get("camera_follow"):
        follow_frames = _camera_follow_keyframes(
            coords,
            token,
            camera,
            map_style,
            count=scene.get("camera_keyframes") or 10,
        )
    projected = [project_to_image(coord, camera) for coord in coords]
    overview_labels = _overview_labels(stops, camera) if overview else []

    if destination is None:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
            destination = handle.name
    destination = str(Path(destination))

    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{BASE_WIDTH}x{BASE_HEIGHT}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:flags=lanczos",
        "-movflags",
        "+faststart",
        destination,
    ]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        for frame_index in range(frame_count):
            progress = frame_index / max(frame_count - 1, 1)
            # Ease in/out gives the moving marker a less robotic feel.
            eased = progress * progress * (3 - 2 * progress)
            frame_camera = camera
            if follow_frames:
                camera_index = min(
                    round(eased * (len(follow_frames) - 1)),
                    len(follow_frames) - 1,
                )
                frame_camera, frame_base = follow_frames[camera_index]
                image = frame_base.copy()
                frame_projected = [project_to_image(coord, frame_camera) for coord in coords]
            else:
                image = base.copy()
                frame_projected = projected
            draw = ImageDraw.Draw(image)

            if overview:
                _draw_overview(draw, projected, overview_labels, scene, frame_index / fps)
            else:
                marker, visible = _interpolate_path(frame_projected, eased)
                _draw_polyline(draw, frame_projected, fill=(255, 255, 255), width=12)
                _draw_polyline(draw, visible, fill=(249, 115, 22), width=7)
                _draw_marker(draw, marker, scene.get("marker_label") or "TN")
                _draw_stop(draw, frame_projected[0], 1)
                _draw_stop(draw, frame_projected[-1], 2)
                _draw_bottom_card(
                    draw,
                    scene.get("title") or "Next Stop",
                    scene.get("subtitle") or "",
                )

            if proc.stdin is None:
                raise RuntimeError("ffmpeg input stream is unavailable.")
            proc.stdin.write(image.tobytes())

        if proc.stdin:
            proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        return_code = proc.wait()
        if return_code != 0:
            raise RuntimeError(f"ffmpeg failed: {stderr[-500:]}")
    except Exception:
        proc.kill()
        Path(destination).unlink(missing_ok=True)
        raise

    return destination
