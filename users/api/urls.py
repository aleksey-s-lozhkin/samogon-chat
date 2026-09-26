from django.urls import path

from chat.api.mobile_views import api_statuses

from .safety import api_block, api_blocks, api_user_report

from .views import api_current_user, api_push_self_test, api_push_subscriptions, api_status


urlpatterns = [
    path("users/<int:user_id>/reports/", api_user_report, name="api_v1_user_report"),
    path("users/me/blocks/", api_blocks, name="api_v1_personal_blocks"),
    path("users/me/blocks/<int:user_id>/", api_block, name="api_v1_personal_block"),
    path("users/statuses/", api_statuses, name="api_v1_user_statuses"),
    path("status/", api_status, name="api_v1_status"),
    path("push/subscriptions/", api_push_subscriptions, name="api_v1_push_subscriptions"),
    path("push/self-test/", api_push_self_test, name="api_v1_push_self_test"),
    path("users/me/", api_current_user, name="api_v1_current_user"),
]
