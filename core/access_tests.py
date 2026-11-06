from django.test import TestCase
from django.urls import reverse


class PublicAccessTests(TestCase):
    def test_entry_is_public_and_does_not_pretend_mini_is_released(self):
        response = self.client.get(reverse('access'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '公开正式版尚未发布')
        self.assertContains(response, reverse('login'))
        self.assertNotContains(response, 'expr_code_qrcode')
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_read_only_entry_does_not_accept_post(self):
        self.assertEqual(self.client.post(reverse('access')).status_code, 405)
