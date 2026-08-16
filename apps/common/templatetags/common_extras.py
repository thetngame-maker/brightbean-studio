import json

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.common.ugc_provenance import get_provenance, provenance_label

register = template.Library()


@register.filter(is_safe=True)
def json_attr(value):
    """Serialize a Python value as a JSON literal safe to embed inside an
    HTML attribute (e.g. Alpine.js x-data).

    json.dumps produces JSON, then HTML-escape covers &, <, >, ", '. The
    browser HTML-decodes the attribute value before the JS engine parses
    it, so Alpine still sees valid JSON.

    Pass Python values (list/dict/None), NOT pre-serialized JSON strings.
    None and empty-string (Django's `string_if_invalid` fallback when a
    template variable like `post.tags` doesn't resolve) both become `[]`,
    matching the prior `|default:'[]'|safe` idiom.
    """
    if value is None or value == "":
        return mark_safe(escape("[]"))
    return mark_safe(escape(json.dumps(value, ensure_ascii=False, default=str)))


@register.filter
def ugc_usage_count(metadata):
    """Number of Studio drafts/posts created from one UGC submission."""
    if not isinstance(metadata, dict):
        return 0
    post_ids = metadata.get("studio_post_ids") or []
    return len(post_ids) if isinstance(post_ids, list) else 0


@register.filter
def ugc_latest_post_id(metadata):
    """Most recently created Studio post id recorded on a UGC submission."""
    if not isinstance(metadata, dict):
        return ""
    post_ids = metadata.get("studio_post_ids") or []
    if not isinstance(post_ids, list) or not post_ids:
        return ""
    return str(post_ids[-1])


@register.filter
def ugc_source_label(metadata, fallback_source=""):
    """Human-friendly original source label for a UGC submission."""
    return provenance_label(metadata, fallback_source=fallback_source)


@register.filter
def ugc_source_url(metadata):
    """Original public source URL, if recorded."""
    return get_provenance(metadata).get("source_url", "")


@register.filter
def ugc_source_handle(metadata):
    """Creator handle recorded by the discovery/import source."""
    handle = get_provenance(metadata).get("creator_handle", "")
    return f"@{handle}" if handle else ""


@register.filter
def ugc_source_external_id(metadata):
    """Provider content identifier used for dedupe/re-import safety."""
    return get_provenance(metadata).get("external_id", "")


@register.inclusion_tag("components/ui_select.html")
def ui_select(
    *,
    model,
    options,
    multiple=False,
    onchange="",
    placeholder="Select",
    value_field="id",
    label_field="",
    icon_field="",
    icon="",
):
    """A styled single/multi select dropdown (Alpine + checkbox/click list).

    A drop-in upgrade for a plain ``<select>`` in an Alpine/HTMX toolbar. The
    panel is ``position: fixed`` (anchored on open) so an ``overflow`` filter
    row can't clip it. Bind it to a property in the enclosing ``x-data`` scope:
    an **array** when ``multiple`` (empty = "all"), otherwise a **string**.

    Params:
      model        Alpine expression holding the selection, e.g. "filters.status".
      options      iterable of model instances, or ``{"value","label","icon"}`` dicts.
      multiple     checkbox multi-select (True) vs single-select (False).
      onchange     Alpine expression run after a change, e.g. "reloadTab()".
      placeholder  trigger label shown when nothing is selected.
      value_field / label_field / icon_field
                   attribute names read off model instances (ignored for dicts).
                   ``icon_field`` is read as a platform code and rendered as a
                   per-option badge.
      icon         leading glyph for the trigger itself — one of
                   status / channel / tag / clock (see components/_filter_icon.html).
                   Omit for no icon.
    """
    norm = []
    for o in options:
        if isinstance(o, dict):
            value, label, opt_icon = o.get("value"), o.get("label"), o.get("icon")
        elif isinstance(o, (tuple, list)) and len(o) >= 2:
            # (value, label) pairs, e.g. Django `choices`.
            value, label, opt_icon = o[0], o[1], None
        elif isinstance(o, str):
            value = label = o
            opt_icon = None
        else:
            value = getattr(o, value_field, None)
            label = getattr(o, label_field) if label_field else str(o)
            opt_icon = getattr(o, icon_field, None) if icon_field else None
        norm.append({"value": str(value) if value is not None else "", "label": label, "icon": opt_icon})

    return {
        "model": model,
        "options": norm,
        # value+label only, for the Alpine trigger-label lookup in single mode.
        "options_js": [{"value": o["value"], "label": str(o["label"])} for o in norm],
        "multiple": bool(multiple),
        "onchange": onchange,
        "placeholder": placeholder,
        "icon": icon,
    }
