"""
createsuperuser for openIMIS.

core.User has no password column, so Django's own command never asks for a password
and never reads DJANGO_SUPERUSER_PASSWORD. This command collects it, then
UserManager.create_superuser creates the interactive user (tblUsers) the UI logs in
as, with the IMIS Administrator role. No technical user is created.
"""
import getpass
import os

from django.contrib.auth.management.commands.createsuperuser import Command as DjangoCommand
from django.core.management.base import CommandError


class Command(DjangoCommand):
    def handle(self, *args, **options):
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        if not password:
            if not options["interactive"]:
                raise CommandError(
                    "You must set DJANGO_SUPERUSER_PASSWORD when using --noinput."
                )
            password = getpass.getpass()
            password2 = getpass.getpass("Password (again): ")
            if password != password2:
                raise CommandError("Your passwords didn't match.")
            if not password.strip():
                raise CommandError("Blank passwords aren't allowed.")
        os.environ["DJANGO_SUPERUSER_PASSWORD"] = password
        return super().handle(*args, **options)
