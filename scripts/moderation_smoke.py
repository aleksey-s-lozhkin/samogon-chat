#!/usr/bin/env python
"""Изолированная браузерная проверка админки и PWA-модерации."""
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from browser_smoke import PROJECT_ROOT, SmokeServer, prepare_database


def run():
    with tempfile.TemporaryDirectory(prefix='samogon-moderation-smoke-') as directory:
        environment = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings_smoke',
                       'SAMOGON_SMOKE_DB': str(Path(directory) / 'smoke.sqlite3'),
                       'DATABASE_URL': '', 'REDIS_URL': '', 'DEBUG': '1',
                       'WEB_PUSH_ENABLED': '0'}
        prepare_database(environment)
        from django.contrib.auth.models import Group
        from django.core.management import call_command
        from django.test import Client
        from chat.models import Message, MessageReport, Room
        from users.models import User
        from playwright.sync_api import sync_playwright

        call_command('setup_moderators', verbosity=0)
        moderator = User.objects.create_user(username='mobile-moderator', is_staff=True)
        moderator.groups.add(Group.objects.get(name='Moderators'))
        client = Client(); client.force_login(moderator)
        session = client.cookies['sessionid'].value
        admin_client = Client(); admin_client.force_login(User.objects.get(username='smoke-admin'))
        admin_session = admin_client.cookies['sessionid'].value
        author = User.objects.get(username='smoke-owner')
        room = Room.objects.get(slug='u-stoyki')
        server = SmokeServer(environment); server.start()
        artifacts = PROJECT_ROOT / 'test-results' / 'moderation-smoke'
        artifacts.mkdir(parents=True, exist_ok=True)
        executor = ThreadPoolExecutor(max_workers=1)
        def database_call(function):
            return executor.submit(function).result()
        try:
            with sync_playwright() as playwright:
                for engine, width in [('chromium', 1280), ('chromium', 390), ('webkit', 320)]:
                    browser = getattr(playwright, engine).launch()
                    context = browser.new_context(viewport={'width': width, 'height': 844}, is_mobile=width<700)
                    context.add_cookies([{'name': 'sessionid', 'value': session, 'url': server.base_url}])
                    page = context.new_page()
                    msg = database_call(lambda: Message.objects.create(user=author, room=room, text='Тестовая жалоба. Проверяем удобство модерации с телефона. ' * 4))
                    report = database_call(lambda: MessageReport.objects.create(message=msg, reporter=moderator, reason='spam', details='Повторяющаяся реклама'))
                    page.goto(f'{server.base_url}/moderation/')
                    page.locator(f'#message-{msg.pk}').click()
                    page.locator('#id_action').select_option('hide')
                    page.locator('#id_reason').fill('Повторяющаяся реклама, проверено')
                    page.locator('#id_confirm').check()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Moderation page overflows'
                    page.screenshot(path=str(artifacts / f'moderation-{engine}-{width}.png'), full_page=True)
                    context.set_offline(True)
                    page.wait_for_function('!navigator.onLine')
                    assert page.locator('button[type=submit]').is_disabled()
                    context.set_offline(False)
                    page.wait_for_function('navigator.onLine')
                    page.locator('button[type=submit]').click()
                    page.get_by_role('status').filter(has_text='Решение сохранено.').wait_for()
                    database_call(report.refresh_from_db); assert report.outcome == 'hide'
                    page.locator('#id_reason').fill('Восстановлено после проверки')
                    page.locator('#id_confirm').check()
                    page.locator('button[type=submit]').click()
                    page.wait_for_selector('#decision-form', state='detached')
                    database_call(msg.refresh_from_db); assert msg.hidden_at is None
                    context.add_cookies([{'name': 'sessionid', 'value': admin_session, 'url': server.base_url}])
                    for suffix, name in [('/admin/', 'index'), ('/admin/chat/messagereport/', 'reports'), ('/admin/diagnostics/', 'diagnostics'), (f'/admin/chat/message/{msg.pk}/change/', 'message'), ('/admin/users/pushsubscription/send/', 'push'), ('/admin/exports/messages/', 'export')]:
                        page.goto(server.base_url + suffix)
                        page.locator('.app-main, .content-wrapper').wait_for()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Admin overflow: {name} {width}'
                        page.screenshot(path=str(artifacts / f'admin-{name}-{engine}-{width}.png'), full_page=True)
                        assert not page.get_by_role('button', name='Go', exact=True).count()
                        assert 'Select an option' not in page.locator('body').inner_text()
                    browser.close()
        finally:
            server.stop()
            executor.shutdown(wait=True)
    print('Moderation smoke passed: desktop/mobile Chromium and mobile WebKit; admin layout, hide/restore, offline state.')


if __name__ == '__main__':
    run()
