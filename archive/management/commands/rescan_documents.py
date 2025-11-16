from django.core.management.base import BaseCommand
from django.db.models import Q
from archive.models import PDFDocument


class Command(BaseCommand):
    help = (
        'Re-scan PDFDocument metadata: file size, page count, text percentage, and optionally OCR.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--ocr',
            action='store_true',
            help='Also run OCR extraction for each document (may be slow).',
        )
        parser.add_argument(
            '--force-ocr',
            action='store_true',
            help='Force OCR even if ocr_text already present.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of documents to process',
        )
        parser.add_argument(
            '--skip-scanned',
            action='store_true',
            help='Skip documents marked as scanned when doing OCR',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Process all documents, not just those missing metadata.',
        )

    def handle(self, *args, **options):
        qs = PDFDocument.objects.all()
        if not options['all']:
            # only those that look suspicious
            qs = qs.filter(Q(page_count__isnull=True) | Q(file_size=0))
        if options['limit']:
            qs = qs[: options['limit']]

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('No documents need rescanning.'))
            return

        self.stdout.write(self.style.SUCCESS(f'Rescanning {total} documents'))
        processed = 0
        for i, doc in enumerate(qs, 1):
            try:
                self.stdout.write(f'[{i}/{total}] {doc.slug or doc.title}... ', ending='')
                # update file size
                if doc.file:
                    try:
                        doc.file_size = doc.file.size
                    except FileNotFoundError:
                        self.stdout.write(self.style.WARNING('file missing, skipping document'))
                        skipped += 1
                        continue
                    except Exception:
                        pass
                # re-extract metadata (page count/text %)
                try:
                    doc.extract_pdf_metadata()
                except FileNotFoundError:
                    self.stdout.write(self.style.WARNING('file missing during metadata, skipping'))
                    skipped += 1
                    continue
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f'metadata failed ({e}) '))
                # optionally do OCR
                if options['ocr']:
                    if options['skip_scanned'] and doc.is_scanned:
                        self.stdout.write(self.style.WARNING('skipped OCR (scanned) '))
                    elif doc.ocr_text and not options['force_ocr']:
                        self.stdout.write(self.style.WARNING('OCR already present '))
                    else:
                        try:
                            doc.extract_ocr_text(force_reprocess=options['force_ocr'])
                        except FileNotFoundError:
                            self.stdout.write(self.style.WARNING('file missing during OCR, skipping'))
                            skipped += 1
                            continue
                        except Exception as e:
                            self.stdout.write(self.style.WARNING(f'OCR error ({e}) '))
                doc.save()
                processed += 1
                self.stdout.write(self.style.SUCCESS('done'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'error: {e}'))
        self.stdout.write(self.style.SUCCESS(f'Rescanned {processed} documents'))
