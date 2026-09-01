from django.apps import AppConfig


class ComposerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.composer"
    verbose_name = "Post Composer"

    def ready(self):
        # TN Social Studio extension: explicit Instagram Story / Reel controls
        # and persistence. Kept in a small compatibility module so upstream
        # BrightBean composer files remain easy to update.
        from .instagram_story_mode import install_instagram_story_mode

        install_instagram_story_mode()

        # Facebook Groups no longer has an official third-party publishing API.
        # Keep the assisted multi-group handoff isolated from PlatformPost so
        # automated Page/Instagram publishing remains unchanged.
        from .facebook_groups import install_facebook_groups

        install_facebook_groups()
