import json
import os
import secrets
from pathlib import Path

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from chat.models import Room, RoomMembership, RoomReadState


CONFIRMATION = "RELEASE-AUDIT-DATA"
USERNAME_PREFIX = "release_audit_"


class Command(BaseCommand):
    help = "Create or remove temporary accounts and sessions for release load audit."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("prepare", "cleanup"))
        parser.add_argument("--credentials", required=True, type=Path)
        parser.add_argument("--count", type=int, default=60)
        parser.add_argument("--room-slug", default="release-audit")
        parser.add_argument("--confirm", required=True)

    def handle(self, *args, **options):
        if options["confirm"] != CONFIRMATION:
            raise CommandError(f"Pass --confirm {CONFIRMATION}")
        credentials = options["credentials"].expanduser().resolve()
        if not credentials.is_absolute() or credentials == Path("/"):
            raise CommandError("--credentials must be a safe absolute path")
        if options["action"] == "prepare":
            self.prepare(
                credentials,
                count=options["count"],
                room_slug=options["room_slug"],
            )
        else:
            self.cleanup(credentials, room_slug=options["room_slug"])

    def prepare(self, credentials, *, count, room_slug):
        if count < 2 or count > 100:
            raise CommandError("--count must be between 2 and 100")
        if credentials.exists():
            raise CommandError("Credentials file already exists; clean it up first")
        if Room.objects.filter(slug=room_slug).exists():
            raise CommandError(f"Room {room_slug!r} already exists")

        credentials.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            credentials,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            User = get_user_model()
            run_id = secrets.token_hex(4)
            created_users = []
            records = []
            with transaction.atomic():
                for index in range(1, count + 1):
                    username = f"{USERNAME_PREFIX}{run_id}_{index:03d}"
                    user = User(username=username, first_name="Release audit")
                    user.set_unusable_password()
                    user.save()
                    created_users.append(user)
                room = Room.objects.create(
                    name="Release audit",
                    slug=room_slug,
                    description=f"Temporary release audit room ({run_id})",
                    visibility=Room.Visibility.PRIVATE,
                    owner=created_users[0],
                )
                RoomMembership.objects.bulk_create([
                    RoomMembership(room=room, user=user)
                    for user in created_users
                ])
                read_at = timezone.now()
                RoomReadState.objects.bulk_create([
                    RoomReadState(room=room, user=user, last_read_at=read_at)
                    for user in created_users
                ])
                for user in created_users:
                    session = SessionStore()
                    session[SESSION_KEY] = str(user.pk)
                    session[BACKEND_SESSION_KEY] = (
                        "django.contrib.auth.backends.ModelBackend"
                    )
                    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
                    session.set_expiry(3600)
                    session.create()
                    records.append({
                        "username": user.username,
                        "sessionid": session.session_key,
                    })
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(records, output, ensure_ascii=False, indent=2)
                    output.write("\n")
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            credentials.unlink(missing_ok=True)
            raise
        self.stdout.write(self.style.SUCCESS(
            f"Created {count} temporary users and room {room_slug!r}. "
            f"Credentials: {credentials}"
        ))

    def cleanup(self, credentials, *, room_slug):
        if not credentials.exists():
            raise CommandError("Credentials file does not exist")
        try:
            records = json.loads(credentials.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CommandError("Credentials file is unreadable") from error
        usernames = [
            item.get("username")
            for item in records
            if isinstance(item, dict)
            and str(item.get("username", "")).startswith(USERNAME_PREFIX)
        ]
        session_keys = [
            item.get("sessionid")
            for item in records
            if isinstance(item, dict) and item.get("sessionid")
        ]
        if not usernames or len(usernames) != len(records):
            raise CommandError("Credentials file does not contain only audit users")

        User = get_user_model()
        with transaction.atomic():
            Room.objects.filter(
                slug=room_slug,
                owner__username__in=usernames,
            ).delete()
            deleted_users, _details = User.objects.filter(
                username__in=usernames,
                first_name="Release audit",
            ).delete()
            Session.objects.filter(session_key__in=session_keys).delete()
        credentials.unlink()
        self.stdout.write(self.style.SUCCESS(
            f"Removed audit room, {len(usernames)} accounts and their sessions "
            f"({deleted_users} database rows deleted)."
        ))
