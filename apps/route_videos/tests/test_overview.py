from unittest.mock import patch

from PIL import Image, ImageDraw

from apps.route_videos import renderer
from apps.route_videos.tasks import _build_scenes


def test_overview_card_waits_three_seconds():
    draw = ImageDraw.Draw(Image.new("RGB", (720, 1280)))
    with patch.object(renderer, "_draw_bottom_card") as card:
        renderer._draw_overview(draw, [(100, 100), (200, 200)], [], {}, 2.99)
        card.assert_not_called()
        renderer._draw_overview(draw, [(100, 100), (200, 200)], [], {"title": "Our trip"}, 3)
        assert card.call_args.args[1] == "Our trip"


def test_all_25_full_names_fit_in_separate_boxes():
    stops = [{"name": "W" * 80, "longitude": -86, "latitude": 36} for _ in range(25)]
    labels = renderer._overview_labels(stops, (-86, 36, 7))
    assert len(labels) == 25
    for i, label in enumerate(labels):
        assert label["text"].replace("\n", "") == f"{i + 1}. " + "W" * 80
        left, top, right, bottom = label["box"]
        assert 0 <= left < right <= 720
        assert 0 <= top < bottom < 1045
        for other in labels[i + 1 :]:
            l2, t2, r2, b2 = other["box"]
            assert right <= l2 or r2 <= left or bottom <= t2 or b2 <= top


def test_overview_uses_custom_stop_name():
    labels = renderer._overview_labels(
        [{"name": "Our picnic spot", "location_name": "Foster Falls", "longitude": -85.67, "latitude": 35.18}],
        (-86, 36, 7),
    )
    assert "Our picnic spot" in labels[0]["text"]


def test_old_overview_manifests_get_eight_seconds_legs_keep_five():
    scenes = list(
        _build_scenes({"clips": {"overview": {"suggested_duration_seconds": 5}, "legs": [{"from": "A", "to": "B"}]}})
    )
    assert scenes[0]["scene"]["seconds"] == 8
    assert scenes[1]["scene"]["seconds"] == 5


def test_renderer_holds_map_for_three_seconds_even_for_old_five_second_scene(tmp_path):
    import io
    from types import SimpleNamespace
    from unittest.mock import Mock

    class FrameSink:
        count = 0

        def write(self, data):
            self.count += 1

        def close(self):
            pass

    sink = FrameSink()
    process = SimpleNamespace(stdin=sink, stderr=io.BytesIO(), wait=lambda: 0, kill=Mock())
    scene = {
        "type": "overview",
        "coordinates": [[-86, 36], [-85, 35]],
        "seconds": 5,
        "fps": 1,
        "stops": [{"name": "My stop", "longitude": -86, "latitude": 36}],
    }
    with (
        patch.object(renderer, "fetch_base_map", return_value=Image.new("RGB", (720, 1280))),
        patch.object(renderer.subprocess, "Popen", return_value=process),
        patch.object(renderer, "_draw_overview") as draw,
    ):
        renderer.render_scene_mp4(scene, "test", tmp_path / "overview.mp4")
    assert sink.count == 8
    assert [call.args[-1] for call in draw.call_args_list] == list(range(8))
    assert draw.call_args.args[2][0]["text"] == "1. My stop"
