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
