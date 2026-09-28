from django.urls import path

from core import auth_views, carveout_views, party_views, views

urlpatterns = [
    path("", views.home, name="home"),
    path("sign-in/", auth_views.sign_in, name="login"),
    path("sign-out/", auth_views.sign_out, name="logout"),
    path("password/", auth_views.password_change, name="password_change"),
    path("users/", views.users, name="users"),
    path("users/add/", views.user_add, name="user_add"),
    path("users/<int:pk>/", views.user_detail, name="user_detail"),
    path("users/<int:pk>/role/", views.user_role, name="user_role"),
    path("users/<int:pk>/active/", views.user_active, name="user_active"),
    path("users/<int:pk>/reset-password/", views.user_reset_password, name="user_reset_password"),
    path("audit/", views.audit_log, name="audit_log"),
    path("parties/", party_views.parties, name="parties"),
    path("parties/add/", party_views.party_add, name="party_add"),
    path("parties/<int:pk>/", party_views.party_detail, name="party_detail"),
    path("parties/<int:pk>/details/", party_views.party_details, name="party_details"),
    path("parties/<int:pk>/credit/", party_views.party_credit, name="party_credit"),
    path("parties/<int:pk>/active/", party_views.party_active, name="party_active"),
    path("parties/<int:pk>/unlink/<int:creditor_id>/", party_views.party_unlink, name="party_unlink"),
    path("tags/", carveout_views.tags, name="tags"),
    path("tags/add/", carveout_views.tag_add, name="tag_add"),
    path("tags/<int:pk>/", carveout_views.tag_edit, name="tag_edit"),
    path("tag-records/", carveout_views.tag_records, name="tag_records"),
    path("tag-records/main/", carveout_views.tag_record_main, name="tag_record_main"),
    path("tag-records/<str:record_type>/<int:record_id>/", carveout_views.tag_record_split, name="tag_record_split"),
    path("carve-out/", carveout_views.carveout_summary, name="carveout_summary"),
]
