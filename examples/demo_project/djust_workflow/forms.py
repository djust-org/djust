"""Plain Django forms. The same classes serve the old and the live views."""

from django import forms

from .models import Ticket


class TicketListParams(forms.Form):
    """The list page's query string: what ``?status=open&q=vpn&page=2`` may say."""

    status = forms.ChoiceField(
        choices=[("all", "All"), *Ticket.Status.choices],
        required=False,
        initial="all",
    )
    q = forms.CharField(max_length=100, required=False, initial="")
    page = forms.IntegerField(min_value=1, required=False, initial=1)


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        # ``created_by`` and ``status`` are left out so a browser cannot set them.
        fields = ["title", "body", "priority"]

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if len(title) < 5:
            raise forms.ValidationError("Use at least 5 characters.")
        duplicates = Ticket.objects.filter(title__iexact=title, status=Ticket.Status.OPEN)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("An open ticket already has this title.")
        return title

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("priority") == Ticket.Priority.URGENT and len(cleaned.get("body", "")) < 20:
            self.add_error("body", "An urgent ticket needs at least 20 characters of detail.")
        return cleaned
