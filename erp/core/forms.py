from django import forms

from core.models import Role, User


class UserAddForm(forms.Form):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=Role.choices, initial=Role.SALES)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("That email already has a user. Open it from the list instead.")
        return email


class RoleForm(forms.Form):
    role = forms.ChoiceField(choices=Role.choices)
