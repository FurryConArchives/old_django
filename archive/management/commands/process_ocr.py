"""
Django management command to process OCR for existing PDF documents.

Usage:
    python manage.py process_ocr                    # Process all unprocessed documents
    python manage.py process_ocr --all              # Reprocess all documents
    python manage.py process_ocr --limit 10        # Process only 10 documents
    python manage.py process_ocr --force            # Force reprocess even if already processed
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from archive.models import PDFDocument
import sys


class Command(BaseCommand):
    help = 'Process OCR for existing PDF documents'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Process all documents, including already processed ones',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force reprocessing even if already processed',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit the number of documents to process',
        )
        parser.add_argument(
            '--skip-scanned',
            action='store_true',
            help='Skip documents marked as scanned (only process digital PDFs)',
        )
        parser.add_argument(
            '--embed-in-pdf',
            action='store_true',
            help='Embed OCR text into PDF files (makes PDFs searchable in viewers)',
        )

    def handle(self, *args, **options):
        # Get documents to process
        if options['all'] or options['force']:
            documents = PDFDocument.objects.all()
        else:
            # Only process documents that don't have OCR text
            # If OCR text exists, assume it's fully OCR'd
            documents = PDFDocument.objects.filter(
                Q(ocr_text='') | Q(ocr_text__isnull=True)
            )

        # Filter out scanned documents if requested
        if options['skip_scanned']:
            documents = documents.filter(is_scanned=False)

        # Apply limit if specified
        if options['limit']:
            documents = documents[:options['limit']]

        total_count = documents.count()

        if total_count == 0:
            self.stdout.write(
                self.style.SUCCESS('No documents to process.')
            )
            return

        self.stdout.write(
            self.style.SUCCESS(f'Found {total_count} document(s) to process.')
        )

        # Process each document
        processed = 0
        failed = 0
        skipped = 0

        for i, document in enumerate(documents, 1):
            try:
                # Check if already has OCR text and not forcing
                if document.ocr_text and document.ocr_text.strip() and not options['force'] and not options['all']:
                    self.stdout.write(
                        f'[{i}/{total_count}] Skipping {document.title} (already has OCR text)'
                    )
                    skipped += 1
                    continue

                self.stdout.write(
                    f'[{i}/{total_count}] Processing: {document.title}...',
                    ending=' '
                )
                self.stdout.flush()

                try:
                    # Extract OCR text
                    document.extract_ocr_text(force_reprocess=options['force'])
                except FileNotFoundError:
                    self.stdout.write(self.style.WARNING('file missing, skipping'))
                    skipped += 1
                    continue

                # Save the document with OCR data
                document.save(update_fields=['ocr_text', 'ocr_processed'])
                
                # Refresh from database to verify save
                document.refresh_from_db()

                # Optionally embed OCR text into PDF file
                if options['embed_in_pdf'] and document.ocr_text:
                    self.stdout.write('Embedding OCR text in PDF...', ending=' ')
                    self.stdout.flush()
                    if document.embed_ocr_text_in_pdf():
                        self.stdout.write(self.style.SUCCESS('✓ Embedded'))
                    else:
                        self.stdout.write(self.style.WARNING('⚠ Embedding failed'))

                if document.ocr_text:
                    char_count = len(document.ocr_text)
                    location = 'PDF file' if options['embed_in_pdf'] else 'database'
                    self.stdout.write(
                        self.style.SUCCESS(f'✓ Done ({char_count:,} characters extracted and saved to {location})')
                    )
                    processed += 1
                else:
                    self.stdout.write(
                        self.style.WARNING('⚠ No text extracted')
                    )
                    processed += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'✗ Error: {str(e)}')
                )
                failed += 1
                import traceback
                if options.get('verbosity', 1) >= 2:
                    traceback.print_exc()

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS('Processing Complete'))
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(f'Total documents: {total_count}')
        self.stdout.write(
            self.style.SUCCESS(f'Successfully processed: {processed}')
        )
        if skipped > 0:
            self.stdout.write(
                self.style.WARNING(f'Skipped: {skipped}')
            )
        if failed > 0:
            self.stdout.write(
                self.style.ERROR(f'Failed: {failed}')
            )
        self.stdout.write('')

