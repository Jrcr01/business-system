from django.urls import path

from masters import setup_more, views

urlpatterns = [
    path("creditors/", views.creditors, name="creditors"),
    path("creditors/new/", views.creditor_new, name="creditor_new"),
    path("creditors/<int:creditor_id>/", views.creditor_edit, name="creditor_edit"),
    path("settings/", views.settings_numbers, name="settings"),
    path("settings/users/", views.settings_users, name="settings_users"),
    path("settings/users/<int:user_id>/", views.settings_user, name="settings_user"),
    path("settings/notifications/", views.settings_notifications, name="settings_notifications"),
    path("settings/ai/", views.settings_ai, name="settings_ai"),
    path("settings/company/", views.settings_company, name="settings_company"),
    path("settings/banks/", views.settings_banks, name="settings_banks"),
    path("settings/audit/", setup_more.settings_audit, name="settings_audit"),
    path("settings/backup/", setup_more.settings_backup, name="settings_backup"),
    path("review/", setup_more.checks_review, name="checks_review"),
]
