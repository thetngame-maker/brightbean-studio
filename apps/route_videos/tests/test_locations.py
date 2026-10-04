import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from apps.route_videos import views


@pytest.fixture
def context(settings):
    settings.MAPBOX_ACCESS_TOKEN = "test-token"
    return SimpleNamespace(id=uuid4()), SimpleNamespace(pk=uuid4(), is_authenticated=True)


def post(view, body, context, payload=None):
    workspace, user = context
    request = RequestFactory().post("/", data=json.dumps(body), content_type="application/json")
    request.user = user
    with (
        patch.object(views, "_get_workspace", return_value=workspace),
        patch.object(views, "_mapbox_json", return_value=payload) as api,
    ):
        response = view(request, workspace.id)
    return response, api


def selection(context, **updates):
    workspace, user = context
    stop = dict(
        mapbox_id="poi.123",
        name="Foster Falls",
        address="Sequatchie, Tennessee",
        longitude=-85.675012345,
        latitude=35.181212345,
        source="mapbox_searchbox",
        confirmed=True,
    )
    stop.update(updates)
    return {
        "confirmation": signing.dumps(
            dict(workspace_id=str(workspace.id), user_id=str(user.pk), stop=stop), salt=views.LOCATION_SALT
        )
    }


@pytest.mark.parametrize("proximity,expected", [(None, views.TN_PROXIMITY), ([-85.3, 35.0], "-85.3,35.0")])
def test_search_bias_and_cross_state_results(context, proximity, expected):
    response, api = post(
        views.location_suggest,
        dict(query="Foster Falls", session_token=str(uuid4()), proximity=proximity),
        context,
        {"suggestions": [{"mapbox_id": "a", "name": "Foster Falls", "place_formatted": "Virginia"}]},
    )
    assert response.status_code == 200
    params = api.call_args.args[1]
    assert params["proximity"] == expected
    assert params["country"] == "US"
    assert "bbox" not in params
    assert json.loads(response.content)["suggestions"][0]["address"] == "Virginia"


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"query": "x"},
        {"query": "ab", "session_token": "bad"},
        {"query": "ab", "session_token": str(uuid4()), "proximity": [float("nan"), 35]},
        {"query": "ab", "session_token": str(uuid4()), "proximity": [False, 35]},
        {"query": "ab", "session_token": str(uuid4()), "proximity": [190, 35]},
    ],
)
def test_bad_search_input_never_calls_mapbox(context, body):
    response, api = post(views.location_suggest, body, context)
    assert response.status_code == 400
    api.assert_not_called()


def test_retrieve_keeps_exact_coordinates_and_signs(context):
    response, api = post(
        views.location_retrieve,
        {"mapbox_id": "poi/123", "session_token": str(uuid4())},
        context,
        {
            "features": [
                {
                    "geometry": {"coordinates": [-85.675012345, 35.181212345]},
                    "properties": {"mapbox_id": "poi/123", "name": "Foster Falls", "place_formatted": "TN"},
                }
            ]
        },
    )
    assert response.status_code == 200
    assert "/retrieve/poi%2F123" in api.call_args.args[0]
    stop = json.loads(response.content)["stop"]
    assert stop["longitude"] == -85.675012345
    assert stop["latitude"] == 35.181212345
    request = SimpleNamespace(user=context[1])
    assert views._confirmed_stop(stop, request, context[0])["mapbox_id"] == "poi/123"


@pytest.mark.parametrize(
    "features",
    [
        [],
        [{"geometry": {"coordinates": [0, 100]}}],
        [{"geometry": {"coordinates": [0, 0]}, "properties": {"mapbox_id": "wrong"}}],
    ],
)
def test_invalid_retrieve_not_confirmed(context, features):
    response, _ = post(
        views.location_retrieve, {"mapbox_id": "a", "session_token": str(uuid4())}, context, {"features": features}
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "stops", [["Nashville", "Foster Falls"], [{"confirmation": "forged"}] * 2, [None, None], [], "ab", [None] * 26]
)
def test_preview_requires_confirmed_stops(context, stops):
    response, api = post(views.route_preview, {"stops": stops}, context)
    assert response.status_code == 400
    api.assert_not_called()


def test_preview_uses_signed_coordinates_not_client_values(context):
    stops = [selection(context), selection(context, name="Chattanooga", longitude=-85.3)]
    stops[0]["longitude"] = 123
    response, api = post(
        views.route_preview,
        {"stops": stops},
        context,
        {"routes": [{"geometry": "", "distance": 100, "duration": 60, "legs": [{"steps": []}]}]},
    )
    assert response.status_code == 200
    assert "-85.675012345,35.181212345;-85.3,35.181212345" in api.call_args.args[0]
    api.assert_called_once()
    manifest = json.loads(response.content)["manifest"]
    assert manifest["stops"][0]["longitude"] == -85.675012345
    assert manifest["stops"][0]["mapbox_id"] == "poi.123"
    assert "confirmation" not in manifest["stops"][0]


@pytest.mark.parametrize("change", ["workspace", "user"])
def test_selection_bound_to_workspace_and_user(context, change):
    chosen = selection(context)
    other = (
        (SimpleNamespace(id=uuid4()), context[1])
        if change == "workspace"
        else (context[0], SimpleNamespace(pk=uuid4()))
    )
    response, api = post(views.route_preview, {"stops": [chosen, chosen]}, other)
    assert response.status_code == 400
    api.assert_not_called()


@pytest.mark.parametrize("view", [views.location_suggest, views.location_retrieve, views.route_preview])
def test_workspace_membership_required(context, view):
    request = RequestFactory().post("/", "{}", content_type="application/json")
    request.user = context[1]
    with (
        patch.object(views, "get_object_or_404", return_value=context[0]),
        patch.object(views.WorkspaceMembership.objects, "filter", return_value=Mock(exists=Mock(return_value=False))),
        pytest.raises(PermissionDenied),
    ):
        view(request, context[0].id)


def test_upstream_failure_is_retryable(context):
    request = RequestFactory().post(
        "/", json.dumps({"query": "Foster", "session_token": str(uuid4())}), content_type="application/json"
    )
    request.user = context[1]
    with (
        patch.object(views, "_get_workspace", return_value=context[0]),
        patch.object(views, "_mapbox_json", side_effect=RuntimeError("Mapbox could not be reached. Try again.")),
    ):
        response = views.location_suggest(request, context[0].id)
    assert response.status_code == 503


def test_generate_all_persists_selected_coordinates(context):
    from apps.route_videos.models import RouteVideoRenderJob

    workspace, user = context
    chosen = views._confirmed_stop(selection(context), SimpleNamespace(user=user), workspace)
    manifest = {
        "schema_version": 2,
        "workspace_id": str(workspace.id),
        "stops": [chosen, chosen],
        "route": {"coordinates": [[-85.6, 35.1], [-85.3, 35.0]]},
        "clips": {"legs": [{"coordinates": [[-85.6, 35.1], [-85.3, 35.0]]}]},
    }
    request = RequestFactory().post("/", json.dumps({"manifest": manifest}), content_type="application/json")
    request.user = user
    job = SimpleNamespace(id=uuid4(), status="queued", total_clips=2)
    with (
        patch.object(views, "_get_workspace", return_value=workspace),
        patch.object(RouteVideoRenderJob.objects, "create", return_value=job) as create,
        patch("apps.route_videos.tasks.render_route_video_pack") as enqueue,
    ):
        response = views.start_render_pack(request, workspace.id)
    assert response.status_code == 202
    assert create.call_args.kwargs["manifest"]["stops"][0] == chosen
    enqueue.assert_called_once_with(str(job.id))


def test_static_map_path_uses_polyline5(settings):
    from urllib.parse import unquote

    settings.MAPBOX_ACCESS_TOKEN = "test-token"
    coordinates = [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]]
    expected = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert views._encode_static_path(coordinates) == expected
    with patch.object(views, "decode_polyline6", return_value=coordinates) as decode:
        url = views._static_map_url([{"longitude": -120.2, "latitude": 38.5}], "directions-polyline6")
    decode.assert_called_once_with("directions-polyline6")
    assert f"path-5+f97316-0.9({expected})" in unquote(url)


@pytest.mark.parametrize("name", ["Our first stop", "Falls, picnic & swim", "", "   "])
def test_custom_stop_name_preserves_location(context, name):
    chosen = selection(context)
    chosen["display_name"] = name
    response, api = post(
        views.route_preview,
        {"stops": [chosen, selection(context)]},
        context,
        {"routes": [{"geometry": "", "legs": [{"steps": []}]}]},
    )
    assert response.status_code == 200
    data = json.loads(response.content)
    stop = data["manifest"]["stops"][0]
    assert stop["name"] == (name.strip() or "Foster Falls")
    assert stop["location_name"] == "Foster Falls"
    assert stop["longitude"] == -85.675012345
    assert stop["mapbox_id"] == "poi.123"
    assert data["legs"][0]["from"] == stop["name"]
    assert "-85.675012345,35.181212345" in api.call_args.args[0]


@pytest.mark.parametrize("name", ["x" * 81, 42, {}, "Falls\nPark"])
def test_invalid_custom_names_rejected(context, name):
    chosen = selection(context)
    chosen["display_name"] = name
    response, api = post(views.route_preview, {"stops": [chosen, selection(context)]}, context)
    assert response.status_code == 400
    api.assert_not_called()


def test_video_titles_preserve_commas():
    from apps.route_videos.tasks import _short_name

    assert _short_name("Falls, picnic & swim") == "Falls, picnic & swim"
