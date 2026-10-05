from unittest.mock import patch

from PIL import Image, ImageDraw

from apps.route_videos import renderer
from apps.route_videos.tasks import _build_scenes


def test_each_leg_keeps_route_numbers_and_custom_names():
    stops = [
        {"name": name, "longitude": -86 + i / 10, "latitude": 36}
        for i, name in enumerate(["Start here", "Picnic spot", "My falls", "Home"])
    ]
    legs = [
        {"from": stops[i]["name"], "to": stops[i + 1]["name"], "coordinates": [[-86, 36], [-85, 35]]} for i in range(3)
    ]
    scenes = list(_build_scenes({"stops": stops, "clips": {"legs": legs}}))[1:]
    for index, item in enumerate(scenes):
        markers = item["scene"]["stops"]
        assert [stop["number"] for stop in markers] == [index + 1, index + 2]
        assert [stop["name"] for stop in markers] == [stops[index]["name"], stops[index + 1]["name"]]
        assert markers[0]["longitude"] == stops[index]["longitude"]


def test_old_manifests_fall_back_to_leg_names_and_geometry():
    markers = renderer.leg_stop_markers([], {"from": "Picnic", "to": "Falls", "coordinates": [[-86, 36], [-85, 35]]}, 2)
    assert [stop["number"] for stop in markers] == [3, 4]
    assert [stop["name"] for stop in markers] == ["Picnic", "Falls"]
    assert markers[1]["latitude"] == 35


def test_visible_destination_does_not_get_renumbered_as_one():
    stops = [
        {"number": 2, "name": "Off screen", "longitude": -500, "latitude": 300},
        {"number": 3, "name": "My falls", "longitude": 500, "latitude": 300},
    ]
    with patch.object(renderer, "project_to_image", side_effect=lambda coord, camera: coord):
        labels = renderer._leg_stop_labels(stops, (0, 0, 0))
    assert len(labels) == 1
    assert labels[0]["number"] == 3
    assert labels[0]["text"] == "3. My falls"


def test_route_is_thin_and_blends_with_the_map():
    image = Image.new("RGB", (100, 100), (100, 100, 100))
    renderer._draw_driving_route(ImageDraw.Draw(image, "RGBA"), [(10, 50), (90, 50)], [(10, 50), (40, 50)])
    assert image.getpixel((60, 47)) == (100, 100, 100)
    assert image.getpixel((60, 50)) != (255, 255, 255)
    assert image.getpixel((60, 50)) != (8, 145, 178)  # translucent, not solid
    assert image.getpixel((30, 50)) != image.getpixel((60, 50))  # completed vs upcoming
