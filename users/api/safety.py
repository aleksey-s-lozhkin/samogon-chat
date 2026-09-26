from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from rest_framework import serializers
from rest_framework.decorators import api_view
from config.rate_limit import is_allowed
from chat.services.guests import eligible_guests
from chat.api.mobile_serializers import MobileErrorSerializer, AccountSerializer
from users.models import PersonalBlock, UserReport
from users.services.safety import set_personal_block, report_user
from .views import api_auth_error


class ReportInput(serializers.Serializer):
    reason = serializers.ChoiceField(choices=UserReport.Reason.choices)
    details = serializers.CharField(max_length=1000, allow_blank=True, required=False, default="")


class ReportResult(serializers.Serializer):
    reported = serializers.BooleanField()
    created = serializers.BooleanField()


class BlockList(serializers.Serializer):
    api_version = serializers.CharField()
    blocks = AccountSerializer(many=True)
    next_offset = serializers.IntegerField(allow_null=True)


errors = {status: MobileErrorSerializer for status in (400, 401, 403, 404, 429)}


@never_cache
@extend_schema(tags=["users"], auth=[{"cookieAuth": []}], request=ReportInput, responses={200: ReportResult, 201: ReportResult, **errors})
@api_view(["POST"])
def api_user_report(request, user_id):
    if error := api_auth_error(request): return error
    target = eligible_guests().exclude(pk=request.user.pk).filter(pk=user_id).first()
    if not target: return JsonResponse({"error": "user_not_found"}, status=404)
    serializer = ReportInput(data=request.data)
    if not serializer.is_valid(): return JsonResponse({"error": "invalid_report", "fields": serializer.errors}, status=400)
    if not is_allowed(identifier=f"user:{request.user.pk}", bucket="user-report", limit=settings.MESSAGE_REPORT_RATE_LIMIT, window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS):
        return JsonResponse({"error": "rate_limited"}, status=429)
    _, created = report_user(reporter=request.user, target=target, **serializer.validated_data)
    return JsonResponse({"reported": True, "created": created}, status=201 if created else 200)


@never_cache
@extend_schema(tags=["users"], auth=[{"cookieAuth": []}], parameters=[OpenApiParameter("offset", int)], responses={200: BlockList, **errors})
@api_view(["GET"])
def api_blocks(request):
    if error := api_auth_error(request): return error
    try:
        offset = int(request.query_params.get("offset", 0))
        if offset < 0: raise ValueError
    except (TypeError, ValueError): return JsonResponse({"error": "invalid_pagination"}, status=400)
    blocks = list(PersonalBlock.objects.filter(owner=request.user).select_related("target").order_by("pk")[offset:offset+51])
    return JsonResponse({"api_version": "v1", "blocks": [{"id": b.target_id, "username": b.target.username, "display_name": b.target.username} for b in blocks[:50]], "next_offset": offset+50 if len(blocks)>50 else None})


@never_cache
@extend_schema_view(
    put=extend_schema(tags=["users"], auth=[{"cookieAuth": []}], request=None, responses={204: None, **errors}),
    delete=extend_schema(tags=["users"], auth=[{"cookieAuth": []}], responses={204: None, **errors}),
)
@api_view(["PUT", "DELETE"])
def api_block(request, user_id):
    if error := api_auth_error(request): return error
    if request.method == "PUT" and not eligible_guests().exclude(pk=request.user.pk).filter(pk=user_id).exists():
        return JsonResponse({"error": "user_not_found"}, status=404)
    set_personal_block(owner=request.user, target_id=user_id, blocked=request.method == "PUT")
    return HttpResponse(status=204)
