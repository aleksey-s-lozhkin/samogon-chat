"""Страница модерации внутри PWA, без доступа к остальной переписке."""
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Min, Prefetch
from django.http import FileResponse, HttpResponse, JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from chat.models import Attachment, Message, MessageReport
from chat.services.moderation import DecisionConflict, decide_report, require_moderator


class DecisionForm(forms.Form):
    action = forms.ChoiceField(label='Решение', choices=[
        ('dismiss', 'Отклонить жалобу'), ('hide', 'Скрыть сообщение и закрыть жалобы'),
        ('ban', 'Заблокировать автора и закрыть жалобы'), ('restore', 'Восстановить сообщение'),
    ])
    reason = forms.CharField(label='Причина решения', max_length=240, widget=forms.Textarea(attrs={'rows': 3}))
    ban_days = forms.TypedChoiceField(label='Срок блокировки (только при блокировке)', coerce=int,
                                    choices=[(1, 'Сутки'), (7, 'Неделя'), (0, 'Навсегда')], required=False)
    hide_with_ban = forms.BooleanField(label="Также скрыть сообщение при блокировке", required=False, initial=True)
    confirm = forms.BooleanField(label='Подтверждаю выбранное решение')


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def moderation(request, report_id=None):
    require_moderator(request.user)
    section = 'resolved' if request.GET.get('state') == 'resolved' else 'new'
    selected = None
    if report_id is not None:
        selected = get_object_or_404(
            MessageReport.objects.select_related('message__user', 'message__room'), pk=report_id,
        )
        section = 'resolved' if selected.resolved_at else 'new'
    reports = MessageReport.objects.filter(resolved_at__isnull=section == 'new')
    queue = Message.objects.filter(reports__in=reports).select_related('user', 'room').annotate(
        report_count=Count('reports'), oldest_report=Min('reports__created_at'),
    ).prefetch_related(Prefetch('reports', queryset=reports.order_by('created_at'), to_attr='queue_reports')).order_by('oldest_report', 'pk')
    page = Paginator(queue, 20).get_page(request.GET.get('page'))
    # Representative complaint for each grouped card; retain every reporter in detail.
    for item in page:
        item.queue_report = item.queue_reports[0]
    form = DecisionForm()
    status = 200
    if report_id is not None:
        if request.method == 'POST':
            require_moderator(request.user, write=True)
            form = DecisionForm(request.POST)
            if form.is_valid():
                try:
                    decide_report(actor=request.user, report_id=selected.pk,
                                  **{k: form.cleaned_data[k] for k in ('action', 'reason', 'ban_days', 'hide_with_ban')})
                except DecisionConflict as error:
                    form.add_error(None, str(error))
                    status = 409
                    selected.refresh_from_db()
                except ValidationError as error:
                    form.add_error(None, error)
                    status = 400
                else:
                    messages.success(request, 'Решение сохранено.')
                    if request.headers.get('Accept') == 'application/json':
                        return JsonResponse({'url': reverse('moderation_detail', args=[selected.pk])})
                    return redirect('moderation_detail', report_id=selected.pk)
            else:
                status = 400
    elif request.method == 'POST':
        return HttpResponse(status=405)
    if selected:
        form.fields['action'].choices = (
            [('restore', 'Восстановить сообщение')] if selected.resolved_at else
            [choice for choice in form.fields['action'].choices if choice[0] != 'restore']
        )
    return render(request, 'chat/moderation.html', {
        'page': page, 'section': section, 'selected': selected, 'form': form,
        'can_decide': request.user.has_perm('chat.moderate_message'),
        'pending': MessageReport.objects.filter(resolved_at__isnull=True).count(),
        'complaints': selected.message.reports.select_related('reporter', 'resolved_by').order_by('created_at') if selected else [],
        'events': selected.message.moderation_events.select_related('moderator') if selected else [],
    }, status=status)


@never_cache
@login_required
@require_GET
def moderation_attachment(request, report_id, attachment_id):
    require_moderator(request.user)
    report = get_object_or_404(MessageReport, pk=report_id)
    attachment = get_object_or_404(Attachment, pk=attachment_id, message_id=report.message_id)
    # Always download: untrusted documents never render in the application origin.
    if settings.DEBUG:
        return FileResponse(attachment.file.open('rb'), as_attachment=True,
                            filename=attachment.original_name, content_type=attachment.content_type)
    response = HttpResponse(content_type=attachment.content_type)
    response['Content-Disposition'] = content_disposition_header(True, attachment.original_name)
    response['X-Accel-Redirect'] = f'/media/{attachment.file.name}'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
