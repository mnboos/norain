"""Move email verification to allauth's EmailAddress and add User.signup_completed."""

from django.db import migrations, models


def to_allauth(apps, schema_editor):
    User = apps.get_model("core", "User")
    EmailAddress = apps.get_model("account", "EmailAddress")
    for user in User.objects.all():
        EmailAddress.objects.get_or_create(
            user=user,
            email=user.email.lower(),
            defaults={"primary": True, "verified": user.email_verified or user.is_superuser},
        )
    # Every account made before the two-step sign-up picked its own username and password.
    User.objects.update(signup_completed=True)
    # The old sign-up made accounts inactive until verified. allauth will not send a new
    # link to an inactive account, so wake those up; they still cannot sign in before they
    # verify. "Never signed in" leaves alone an account an admin switched off on purpose.
    User.objects.filter(email_verified=False, is_active=False, last_login__isnull=True).update(is_active=True)


def from_allauth(apps, schema_editor):
    User = apps.get_model("core", "User")
    EmailAddress = apps.get_model("account", "EmailAddress")
    verified = EmailAddress.objects.filter(verified=True).values("user_id")
    User.objects.filter(pk__in=verified).update(email_verified=True)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_recurringroute_last_viewed_at"),
        ("account", "0009_emailaddress_unique_primary_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="signup_completed",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(to_allauth, from_allauth),
        migrations.RemoveField(
            model_name="user",
            name="email_verified",
        ),
    ]
