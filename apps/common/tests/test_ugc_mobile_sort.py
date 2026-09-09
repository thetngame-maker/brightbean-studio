from datetime import timedelta
from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase
from django.utils import timezone

from apps.common.ugc_mobile_queue_views import _filters_from_request, _sort_mobile


class UGCMobileSortTests(SimpleTestCase):
    def test_oldest_sort_orders_submissions_ascending(self):
        now = timezone.now()
        newest = SimpleNamespace(submitted_at=now)
        middle = SimpleNamespace(submitted_at=now - timedelta(days=1))
        oldest = SimpleNamespace(submitted_at=now - timedelta(days=2))

        self.assertEqual(_sort_mobile([middle, newest, oldest], "oldest"), [oldest, middle, newest])

    def test_oldest_is_an_accepted_mobile_sort_filter(self):
        request = RequestFactory().get("/community-content/", {"sort": "oldest"})

        _relevance, _media, sort_mode, _permission = _filters_from_request(request)

        self.assertEqual(sort_mode, "oldest")
