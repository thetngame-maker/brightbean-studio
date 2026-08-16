from django import template

from apps.inbox.lead_views import lead_profile_for_message

register = template.Library()


@register.simple_tag
def inbox_lead_profile(message):
    return lead_profile_for_message(message)
