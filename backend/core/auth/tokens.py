"""One-time tokens used to verify an account email address."""

from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """Invalidate verification links as soon as an account becomes active."""

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.password}{timestamp}{user.is_active}{user.email}"


email_verification_token_generator = EmailVerificationTokenGenerator()
