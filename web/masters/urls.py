from django.urls import path

from masters import views

urlpatterns = [
    path("creditors/", views.creditors, name="creditors"),
    path("creditors/new/", views.creditor_new, name="creditor_new"),
    path("creditors/<int:creditor_id>/", views.creditor_edit, name="creditor_edit"),
    path("settings/", views.settings_numbers, name="settings"),
    path("settings/users/", views.settings_users, name="settings_users"),
    path("settings/users/<int:user_id>/", views.settings_user, name="settings_user"),
    path("settings/access/", views.settings_access, name="settings_access"),
]
