"""Signals that keep creator relationships and rights passports synchronized."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UGCRightsPassport, UGCSubmission
from .ugc_creator_rights_requests import cancel_pending_rights_requests_for_submission
from .ugc_creator_services import synchronize_submission_relationship
from .ugc_creator_task_services import sync_rights_renewal_task


@receiver(post_save, sender=UGCSubmission, dispatch_uid="common.sync_ugc_creator_relationship")
def sync_ugc_creator_relationship(sender, instance, raw=False, **kwargs):
    if raw:
        return
    synchronize_submission_relationship(instance)
    cancel_pending_rights_requests_for_submission(instance)


@receiver(post_save, sender=UGCRightsPassport, dispatch_uid="common.sync_ugc_rights_renewal_task")
def sync_ugc_rights_renewal_task(sender, instance, raw=False, **kwargs):
    if raw:
        return
    sync_rights_renewal_task(instance)
