"""TN Game Studio Facebook Groups assisted publishing bridge.

Meta no longer exposes an official API for third-party publishing to Facebook
Groups. This module deliberately keeps Facebook Group destinations outside the
automated PlatformPost publisher and adds a manual, multi-group handoff flow to
the composer instead.

The integration is isolated from the large upstream composer template/view files:
ComposerConfig.ready() imports this module, which registers the small companion
models and installs a render wrapper that injects CSS/JS only on composer pages.
"""

from __future__ import annotations

import json
import uuid
from functools import wraps
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.members.decorators import require_permission

_INSTALLED = False


class FacebookGroupTarget(models.Model):
    """A reusable Facebook Group destination saved at workspace level."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="facebook_group_targets",
    )
    name = models.CharField(max_length=120)
    url = models.URLField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "composer"
        db_table = "composer_facebook_group_target"
        ordering = ["name", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "url"],
                name="composer_fb_group_workspace_url_unique",
            )
        ]

    def __str__(self):
        return self.name


class PostFacebookGroupTarget(models.Model):
    """Manual publishing state for one post → Facebook Group destination."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        POSTED = "posted", "Posted"
        SKIPPED = "skipped", "Skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(
        "composer.Post",
        on_delete=models.CASCADE,
        related_name="facebook_group_targets",
    )
    target = models.ForeignKey(
        FacebookGroupTarget,
        on_delete=models.CASCADE,
        related_name="post_targets",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    posted_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "composer"
        db_table = "composer_post_facebook_group_target"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["post", "target"],
                name="composer_post_fb_group_unique",
            )
        ]

    def __str__(self):
        return f"{self.post_id} → {self.target.name} ({self.status})"


def normalize_group_url(value: str) -> str:
    """Return a canonical Facebook group URL or an empty string when invalid."""
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    host = parsed.netloc.lower().split(":", 1)[0]
    if parsed.scheme not in {"http", "https"} or host not in {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
    }:
        return ""
    path = parsed.path.rstrip("/")
    if not path.startswith("/groups/") or len(path.split("/")) < 3:
        return ""
    return f"https://www.facebook.com{path}/"


def _serialize_target(target: FacebookGroupTarget) -> dict[str, str]:
    return {"id": str(target.id), "name": target.name, "url": target.url}


def _serialize_post_target(link: PostFacebookGroupTarget) -> dict[str, str | None]:
    return {
        **_serialize_target(link.target),
        "status": link.status,
        "posted_at": link.posted_at.isoformat() if link.posted_at else None,
    }


def ready_facebook_group_handoffs(workspace, *, now=None):
    """Return scheduled posts whose pending Group handoff is due.

    Group targets intentionally do not become ``PlatformPost`` rows, so the
    automatic publisher cannot discover them.  The assisted task follows the
    real schedule of the post's automatic variants instead: a shared
    ``Post.scheduled_at`` or a per-account ``PlatformPost.scheduled_at``.  A
    client hold parks the handoff along with the automatic post so rights or
    approval safeguards cannot be bypassed by the manual workflow.
    """
    from .models import PlatformPost

    now = now or timezone.now()
    active_schedule_statuses = {
        PlatformPost.Status.SCHEDULED,
        PlatformPost.Status.PUBLISHING,
        PlatformPost.Status.PUBLISHED,
        PlatformPost.Status.FAILED,
    }
    links = (
        PostFacebookGroupTarget.objects.filter(
            post__workspace=workspace,
            status=PostFacebookGroupTarget.Status.PENDING,
        )
        .select_related("post", "target")
        .prefetch_related("post__platform_posts")
        .order_by("created_at")
    )

    by_post = {}
    for link in links:
        entry = by_post.setdefault(
            link.post_id,
            {"post": link.post, "groups": [], "due_at": None},
        )
        entry["groups"].append(link.target)

    ready = []
    for entry in by_post.values():
        post = entry["post"]
        variants = list(post.platform_posts.all())
        if any(variant.status == PlatformPost.Status.ON_HOLD for variant in variants):
            continue

        due_times = []
        for variant in variants:
            if variant.status not in active_schedule_statuses:
                continue
            effective_at = variant.scheduled_at or post.scheduled_at
            if effective_at is not None:
                due_times.append(effective_at)

        if not due_times:
            continue
        due_at = min(due_times)
        if due_at > now:
            continue
        entry["due_at"] = due_at
        ready.append(entry)

    return sorted(ready, key=lambda item: (item["due_at"], item["post"].created_at))


def _workspace(request, workspace_id):
    # Import lazily to avoid a circular import during app startup.
    from . import views

    return views._get_workspace(request, workspace_id)


def _post_in_workspace(post_id, workspace):
    from .models import Post

    return get_object_or_404(Post, id=post_id, workspace=workspace)


@login_required
@require_permission("create_posts")
@require_http_methods(["GET", "POST"])
def facebook_group_catalog(request, workspace_id):
    """List/add/remove reusable Facebook Group destinations for a workspace."""
    workspace = _workspace(request, workspace_id)

    if request.method == "GET":
        groups = FacebookGroupTarget.objects.filter(workspace=workspace)
        return JsonResponse({"groups": [_serialize_target(group) for group in groups]})

    action = request.POST.get("action", "add").strip().lower()
    if action == "add":
        url = normalize_group_url(request.POST.get("url", ""))
        name = request.POST.get("name", "").strip()[:120] or "Facebook Group"
        if not url:
            return JsonResponse({"error": "Enter a valid Facebook Group URL."}, status=400)
        group, created = FacebookGroupTarget.objects.get_or_create(
            workspace=workspace,
            url=url,
            defaults={"name": name},
        )
        if not created and group.name != name:
            group.name = name
            group.save(update_fields=["name", "updated_at"])
        return JsonResponse({"group": _serialize_target(group), "created": created})

    if action == "remove":
        group = get_object_or_404(
            FacebookGroupTarget,
            id=request.POST.get("group_id"),
            workspace=workspace,
        )
        group.delete()
        return JsonResponse({"removed": True})

    return JsonResponse({"error": "Unsupported action."}, status=400)


@login_required
@require_permission("create_posts")
@require_http_methods(["GET", "POST"])
def facebook_group_post_targets(request, workspace_id, post_id):
    """Persist selections and per-group manual publishing state for a post."""
    workspace = _workspace(request, workspace_id)
    post = _post_in_workspace(post_id, workspace)

    if request.method == "GET":
        links = PostFacebookGroupTarget.objects.filter(post=post).select_related("target")
        return JsonResponse({"groups": [_serialize_post_target(link) for link in links]})

    action = request.POST.get("action", "set").strip().lower()
    if action == "set":
        raw_ids = [value.strip() for value in request.POST.get("group_ids", "").split(",") if value.strip()]
        valid_ids: list[uuid.UUID] = []
        for value in raw_ids[:100]:
            try:
                valid_ids.append(uuid.UUID(value))
            except (TypeError, ValueError):
                continue

        targets = list(FacebookGroupTarget.objects.filter(workspace=workspace, id__in=valid_ids))
        keep_ids = {target.id for target in targets}
        with transaction.atomic():
            PostFacebookGroupTarget.objects.filter(post=post).exclude(target_id__in=keep_ids).delete()
            for target in targets:
                PostFacebookGroupTarget.objects.get_or_create(post=post, target=target)

        links = PostFacebookGroupTarget.objects.filter(post=post).select_related("target")
        return JsonResponse({"groups": [_serialize_post_target(link) for link in links]})

    if action == "status":
        status = request.POST.get("status", "").strip().lower()
        if status not in {
            PostFacebookGroupTarget.Status.PENDING,
            PostFacebookGroupTarget.Status.POSTED,
            PostFacebookGroupTarget.Status.SKIPPED,
        }:
            return JsonResponse({"error": "Invalid group publishing status."}, status=400)
        link = get_object_or_404(
            PostFacebookGroupTarget.objects.select_related("target"),
            post=post,
            target_id=request.POST.get("group_id"),
            target__workspace=workspace,
        )
        link.status = status
        link.posted_at = timezone.now() if status == PostFacebookGroupTarget.Status.POSTED else None
        link.save(update_fields=["status", "posted_at", "updated_at"])
        return JsonResponse({"group": _serialize_post_target(link)})

    return JsonResponse({"error": "Unsupported action."}, status=400)


def _inject_group_assistant(response, *, workspace_id: str, post_id: str = ""):
    """Inject the group destination UI assets into a rendered composer response."""
    if getattr(response, "status_code", 500) != 200 or not hasattr(response, "content"):
        return response
    content_type = response.get("Content-Type", "")
    if not content_type.startswith("text/html"):
        return response

    try:
        html = response.content.decode(response.charset or "utf-8")
    except (UnicodeDecodeError, AttributeError):
        return response

    if "facebook_groups.js" in html:
        return response

    static_url = getattr(settings, "STATIC_URL", "/static/") or "/static/"
    if not static_url.endswith("/"):
        static_url += "/"

    catalog_url = reverse("composer:facebook_group_catalog", kwargs={"workspace_id": workspace_id})
    post_key = post_id or "new"
    fragment = (
        f'<link rel="stylesheet" href="{static_url}composer/facebook_groups.css?v=20260901-3">'
        '<script id="tn-facebook-groups-script" defer '
        f"data-catalog-url={json.dumps(catalog_url)} "
        f"data-post-key={json.dumps(post_key)} "
        f'src="{static_url}composer/facebook_groups.js?v=20260902-3"></script>'
    )
    if "</body>" in html:
        html = html.replace("</body>", f"{fragment}</body>", 1)
    else:
        html += fragment

    response.content = html.encode(response.charset or "utf-8")
    if response.has_header("Content-Length"):
        del response["Content-Length"]
    return response


def install_facebook_groups() -> None:
    """Install the composer-only Facebook Groups UI bridge once per process."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import views

    original_render = views.render

    @wraps(original_render)
    def render_with_facebook_groups(request, template_name, context=None, *args, **kwargs):
        response = original_render(request, template_name, context, *args, **kwargs)
        if template_name != "composer/compose.html" or not context or not context.get("workspace"):
            return response

        post_id = str(context["post"].id) if context.get("post") is not None else ""
        return _inject_group_assistant(
            response,
            workspace_id=str(context["workspace"].id),
            post_id=post_id,
        )

    views.render = render_with_facebook_groups
