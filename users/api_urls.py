from django.urls import path

from users.api import api_push_self_test, api_push_subscriptions, api_status


urlpatterns = [
    path("status/", api_status, name="api_v1_status"),
    path(
        "push/subscriptions/",
        api_push_subscriptions,
        name="api_v1_push_subscriptions",
    ),
    path(
        "push/self-test/",
        api_push_self_test,
        name="api_v1_push_self_test",
    ),
]
