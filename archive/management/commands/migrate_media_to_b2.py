from pathlib import Path

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Upload files from the local media directory to the configured default storage backend.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete-local',
            action='store_true',
            help='Delete each local file after it has been uploaded successfully.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be uploaded without writing anything.',
        )

    def handle(self, *args, **options):
        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.exists():
            self.stdout.write(self.style.WARNING(f'Media root does not exist: {media_root}'))
            return

        uploaded_count = 0
        deleted_count = 0
        skipped_count = 0

        for file_path in media_root.rglob('*'):
            if not file_path.is_file():
                continue

            relative_path = file_path.relative_to(media_root).as_posix()
            if options['dry_run']:
                self.stdout.write(f'[dry-run] {relative_path}')
                uploaded_count += 1
                continue

            # Skip uploading if the file already exists in the configured storage backend
            try:
                if default_storage.exists(relative_path):
                    self.stdout.write(self.style.WARNING(f'Skipped {relative_path} (already exists in storage)'))
                    skipped_count += 1
                    continue
            except Exception:
                # Some storage backends may not implement `exists()` reliably; fall back to attempting upload
                pass

            try:
                with file_path.open('rb') as source_file:
                    default_storage.save(relative_path, File(source_file))
                uploaded_count += 1
                self.stdout.write(self.style.SUCCESS(f'Uploaded {relative_path}'))

                if options['delete_local']:
                    file_path.unlink()
                    deleted_count += 1
            except Exception as exc:
                skipped_count += 1
                self.stdout.write(self.style.ERROR(f'Failed {relative_path}: {exc}'))

        self.stdout.write(
            self.style.SUCCESS(
                f'Completed. Uploaded: {uploaded_count}, Deleted: {deleted_count}, Failed: {skipped_count}'
            )
        )
