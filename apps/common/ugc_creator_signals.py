"""Signals that keep creator relationships and rights passports synchronized."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UGCSubmission
from .ugc_creator_services import synchronize_submission_relationship


@receiver(post_save, sender=UGCSubmission, dispatch_uid="common.sync_ugc_creator_relationship")
def sync_ugc_creator_relationship(sender, instance, raw=False, **kwargs):
    if raw:
        return
    synchronize_submission_relationship(instance)
