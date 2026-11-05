"""Independent navigation counterexamples; business permissions remain authoritative."""
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.http import QueryDict
from django.test import TestCase

from .models import User


class ReferenceIndependentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', verbosity=0)

    def setUp(self):
        self.student = User.objects.get(username='student')
        self.other = User.objects.get(username='student2')
        self.client.force_login(self.student)

    def test_long_catalog_search_is_rejected_or_preserved_when_another_chip_is_cleared(self):
        term = 'x' * 2001
        response = self.client.get('/resources/', {'q': term, 'kind': 'EQUIPMENT'})
        self.assertEqual(response.status_code, 200)
        if response.context['query_invalid']:
            self.assertEqual(response.context['query_count'], 0)
            return
        chips = [chip for chip in response.context['query_chips'] if chip['label'] == '\u7c7b\u578b']
        self.assertEqual(len(chips), 1)
        self.assertEqual(parse_qs(urlsplit(chips[0]['url']).query).get('q'), [term])

    def test_long_record_search_is_rejected_or_preserved_when_another_chip_is_cleared(self):
        term = 'x' * 2001
        response = self.client.get('/bookings/', {'q': term, 'status': 'CONFIRMED'})
        self.assertEqual(response.status_code, 200)
        if response.context['query_invalid']:
            self.assertEqual(response.context['query_count'], 0)
            return
        chips = [chip for chip in response.context['query_chips'] if chip['label'] == '\u72b6\u6001']
        self.assertEqual(len(chips), 1)
        self.assertEqual(parse_qs(urlsplit(chips[0]['url']).query).get('q'), [term])

    def test_foreign_record_context_is_removed_from_week_and_chip_links(self):
        response = self.client.get('/schedule/', {
            'origin': 'records', 'record_user': self.other.pk,
            'record_q': 'PRIVATE_QUERY', 'record_queue': 'review',
            'q': 'OSC', 'day': '2026-11-07',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'PRIVATE_QUERY')
        for target in [response.context['date_nav']['following'],
                       response.context['date_nav']['today_url'],
                       *[chip['url'] for chip in response.context['query_chips']]]:
            self.assertNotIn('record_', target)

    def test_switching_schedule_dates_does_not_overwrite_original_record_day_or_page(self):
        response = self.client.get('/schedule/', {
            'origin': 'records', 'record_user': self.student.pk,
            'record_day': '2026-11-07', 'record_page': '4', 'record_q': 'search',
            'day': '2026-11-08',
        })
        following = self.client.get(response.context['date_nav']['following'])
        back = QueryDict(urlsplit(following.context['view_context']['url']).query)
        self.assertEqual(back['day'], '2026-11-07')
        self.assertEqual(back['page'], '4')
        self.assertEqual(back['q'], 'search')
