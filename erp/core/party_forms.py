"""Party forms (core step C3)."""
from django import forms

from core.parties import find_same_name


class PartyDetailsForm(forms.Form):
    """What Sales and the owner may edit (owner's choice: contact number and address)."""
    name = forms.CharField(max_length=150)
    contact_number = forms.CharField(max_length=50, required=False)
    address = forms.CharField(max_length=250, required=False)

    def __init__(self, *args, party=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.party = party

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        same = find_same_name(name, exclude_pk=self.party.pk if self.party else None)
        if same:
            raise forms.ValidationError(f"{same.name} is already on the list. Open it instead of adding it again.")
        return name


class PartyCreditForm(forms.Form):
    """Owner only: credit, terms, affiliate."""
    credit_allowed = forms.BooleanField(required=False)
    terms_days = forms.IntegerField(required=False, min_value=0, max_value=365)
    is_affiliate = forms.BooleanField(required=False)

    def clean(self):
        data = super().clean()
        if data.get("credit_allowed") and data.get("terms_days") is None:
            self.add_error("terms_days", "Give the number of days the customer has to pay.")
        return data
