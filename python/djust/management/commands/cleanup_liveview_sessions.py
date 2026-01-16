"""
Django management command to clean up expired LiveView sessions.

Usage:
    python manage.py cleanup_liveview_sessions
    python manage.py cleanup_liveview_sessions --ttl 7200  # 2 hours
    python manage.py cleanup_liveview_sessions --stats     # Show stats only
"""

from django.core.management.base import BaseCommand
from djust.live_view import cleanup_expired_sessions, get_session_stats, DEFAULT_SESSION_TTL


class Command(BaseCommand):
    help = "Clean up expired LiveView sessions from memory cache"

    def add_arguments(self, parser):
        parser.add_argument(
            "--ttl",
            type=int,
            default=DEFAULT_SESSION_TTL,
            help=f"Time to live in seconds (default: {DEFAULT_SESSION_TTL})",
        )
        parser.add_argument(
            "--stats", action="store_true", help="Show session statistics without cleaning up"
        )

    def handle(self, *args, **options):
        ttl = options["ttl"]
        show_stats = options["stats"]

        # Show statistics
        stats = get_session_stats()
        self.stdout.write(self.style.SUCCESS("\nLiveView Session Statistics:"))
        self.stdout.write(f"  Total sessions: {stats['total_sessions']}")

        if stats["total_sessions"] > 0:
            self.stdout.write(f"  Oldest session: {stats['oldest_session_age']:.1f}s ago")
            self.stdout.write(f"  Newest session: {stats['newest_session_age']:.1f}s ago")
            self.stdout.write(f"  Average age: {stats['average_age']:.1f}s")

        # Clean up if not just showing stats
        if not show_stats:
            self.stdout.write(f"\nCleaning up sessions older than {ttl}s...")
            cleaned_count = cleanup_expired_sessions(ttl)

            if cleaned_count > 0:
                self.stdout.write(
                    self.style.SUCCESS(f"✓ Cleaned up {cleaned_count} expired session(s)")
                )
            else:
                self.stdout.write(self.style.WARNING("No expired sessions to clean up"))

            # Show updated stats
            updated_stats = get_session_stats()
            self.stdout.write(f"\nRemaining sessions: {updated_stats['total_sessions']}\n")
