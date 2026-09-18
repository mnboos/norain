"""Mail backends."""

from django.core.mail.backends import console


class ReadableConsoleBackend(console.EmailBackend):
    """Print mail as a person reads it: subject, sender, recipients, then the plain body.

    Django's console backend prints the encoded MIME text. Any body line over 78
    characters comes out as quoted-printable, so a link reads ``reset_key=3D…`` with
    ``=`` line breaks inside the key, and a link copied from the console fails.
    """

    def write_message(self, message):
        self.stream.write(
            f"Subject: {message.subject}\nFrom: {message.from_email}\nTo: {', '.join(message.to)}\n\n{message.body}\n"
        )
        self.stream.write("-" * 79 + "\n")
