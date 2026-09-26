from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from chat.models import Attachment, Message, MessageReport, ModerationEvent, Room
from chat.services.moderation import DecisionConflict, decide_report
from users.models import User


class ModerationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_moderators', stdout=StringIO())
        cls.mod = User.objects.create_user(username='moderator', is_staff=True)
        cls.mod.groups.add(Group.objects.get(name='Moderators'))
        cls.author = User.objects.create_user(username='author')
        cls.reporter = User.objects.create_user(username='reporter')
        cls.other = User.objects.create_user(username='other')
        cls.room = Room.objects.create(name='Закрытая', slug='moderation-test', visibility='private', owner=cls.author)
        cls.message = Message.objects.create(room=cls.room, user=cls.author, recipient=cls.reporter, text='Обжалованное сообщение')
        cls.unrelated = Message.objects.create(room=cls.room, user=cls.author, recipient=cls.reporter, text='Чужой контекст не показывать')
        cls.report = MessageReport.objects.create(message=cls.message, reporter=cls.reporter, reason='spam')
        cls.second = MessageReport.objects.create(message=cls.message, reporter=cls.other, reason='abuse')
        cls.url = reverse('moderation_detail', args=[cls.report.pk])

    def setUp(self):
        self.client.force_login(self.mod)

    def post(self, action='hide', **overrides):
        return self.client.post(self.url, {'action': action, 'reason': 'Проверено модератором', 'confirm': 'on', 'ban_days': '1', **overrides})

    def test_guest_regular_user_and_staff_without_permissions_cannot_read_or_decide(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        for user in [self.author, User.objects.create_user(username='staff-only', is_staff=True)]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.post().status_code, 403)

    def test_view_permission_does_not_allow_decisions(self):
        self.other.user_permissions.add(Permission.objects.get(codename='view_messagereport'))
        self.client.force_login(self.other)
        self.assertContains(self.client.get(self.url), 'Обжалованное сообщение')
        self.assertEqual(self.post().status_code, 403)

    def test_list_groups_reports_and_detail_does_not_disclose_neighbor_messages(self):
        response = self.client.get(reverse('moderation'))
        self.assertEqual(len(response.context['page']), 1)
        self.assertContains(response, 'Жалоб: 2')
        response = self.client.get(self.url)
        self.assertContains(response, 'Обжалованное сообщение')
        self.assertNotContains(response, 'Чужой контекст не показывать')
        self.assertIn('no-store', response['Cache-Control'])

    @patch('chat.views.broadcast_message_deleted')
    def test_hide_resolves_group_and_logs_once_and_notifies_after_commit(self, broadcast):
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post().status_code, 302)
        broadcast.assert_called_once()
        self.message.refresh_from_db()
        self.assertIsNotNone(self.message.hidden_at)
        self.assertEqual(MessageReport.objects.filter(outcome='hide', resolved_by=self.mod).count(), 2)
        self.assertEqual(ModerationEvent.objects.filter(action='hide_message').count(), 1)
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(ModerationEvent.objects.filter(action='hide_message').count(), 1)

    def test_stale_second_report_cannot_override_first_decision(self):
        decide_report(actor=self.mod, report_id=self.report.pk, action='dismiss', reason='Нет нарушения')
        with self.assertRaises(DecisionConflict):
            decide_report(actor=self.mod, report_id=self.second.pk, action='hide', reason='Устаревшее решение')
        self.message.refresh_from_db()
        self.assertIsNone(self.message.hidden_at)

    def test_restore_preserves_original_decision_and_adds_history(self):
        self.post()
        self.assertEqual(self.post('restore').status_code, 302)
        self.message.refresh_from_db(); self.report.refresh_from_db()
        self.assertIsNone(self.message.hidden_at)
        self.assertEqual(self.report.outcome, 'hide')
        self.assertEqual(ModerationEvent.objects.filter(action='restore_message').count(), 1)
        self.assertEqual(self.post('restore').status_code, 409)

    def test_ban_requires_user_permission_and_protects_staff(self):
        self.assertEqual(self.post('ban').status_code, 302)
        self.author.refresh_from_db()
        self.assertTrue(self.author.is_banned)
        self.assertIsNotNone(self.author.banned_until)
        self.message.refresh_from_db()
        self.assertIsNone(self.message.hidden_at)

    def test_banning_staff_does_not_close_report(self):
        self.author.is_staff = True; self.author.save()
        self.assertEqual(self.post('ban').status_code, 403)
        self.report.refresh_from_db()
        self.assertIsNone(self.report.resolved_at)

    def test_missing_confirmation_reason_or_invalid_action_does_not_mutate(self):
        for overrides in [{'confirm': ''}, {'reason': ''}, {'reason': 'x'*241}, {'action': 'delete'}, {'ban_days': '365'}]:
            self.assertEqual(self.post(**overrides).status_code, 400)
        self.report.refresh_from_db()
        self.assertIsNone(self.report.resolved_at)

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.mod)
        self.assertEqual(client.post(self.url, {'action': 'hide'}).status_code, 403)

    def test_attachment_scope_is_only_the_reported_message(self):
        attachment = Attachment.objects.create(message=self.unrelated, file='attachments/fake.txt', original_name='fake.txt', content_type='text/plain', size=1, kind='file')
        url = reverse('moderation_attachment', args=[self.report.pk, attachment.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        attachment.message = self.message; attachment.save()
        with self.settings(DEBUG=False):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('attachment;', response['Content-Disposition'])
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_hide_and_restore_also_write_audit_events(self):
        url = reverse('admin:chat_message_changelist')
        for action in ['hide_messages', 'restore_messages']:
            self.assertEqual(self.client.post(url, {'action': action, '_selected_action': [self.message.pk]}).status_code, 302)
        self.assertEqual(ModerationEvent.objects.filter(message=self.message).count(), 2)

    def test_old_resolved_report_remains_readable(self):
        from django.utils import timezone
        self.report.resolved_at = timezone.now(); self.report.save()
        self.assertContains(self.client.get(self.url), 'Рассмотрено ранее')

    def test_ban_can_hide_message_in_the_same_transaction(self):
        response = self.post('ban', hide_with_ban='on')
        self.assertEqual(response.status_code, 302)
        self.author.refresh_from_db()
        self.message.refresh_from_db()
        self.assertTrue(self.author.is_banned)
        self.assertIsNotNone(self.message.hidden_at)
        self.assertEqual(ModerationEvent.objects.filter(message=self.message).count(), 3)

    def test_ajax_success_keeps_flash_message_for_the_following_page(self):
        response = self.client.post(self.url, {
            'action': 'dismiss', 'reason': 'Нет нарушения', 'confirm': 'on',
        }, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertContains(self.client.get(response.json()['url']), 'Решение сохранено.')

    def test_revoked_permissions_deny_a_previously_opened_report(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.mod.groups.clear()
        self.assertEqual(self.post().status_code, 403)
        self.report.refresh_from_db()
        self.assertIsNone(self.report.resolved_at)

    def test_previous_application_can_insert_report_without_new_columns(self):
        from django.db import connection
        from django.utils import timezone
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO chat_messagereport "
                "(message_id, reporter_id, reason, details, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                [self.unrelated.pk, self.reporter.pk, 'spam', '', timezone.now()],
            )
        report = MessageReport.objects.get(message=self.unrelated)
        self.assertEqual(report.outcome, '')
        self.assertEqual(report.resolution_note, '')
