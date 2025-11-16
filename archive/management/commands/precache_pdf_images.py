from django.core.management.base import BaseCommand

from archive.models import PDFDocument
from archive.pdf_cache import cached_page_image_exists, generate_page_image, pdf_page_cache_path
from archive.utils import local_path_for_field_file


class Command(BaseCommand):
    help = 'Pre-generate cached images for all PDF pages of each published PDFDocument.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--include-drafts',
            action='store_true',
            help='Also pre-cache images for unpublished (draft) documents.',
        )
        parser.add_argument(
            '--drafts-only',
            action='store_true',
            help='Only pre-cache images for unpublished (draft) documents.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Regenerate cached images even when they already exist.',
        )
        parser.add_argument(
            '--slug',
            action='append',
            dest='slugs',
            help='Only pre-cache the document with this slug (repeatable).',
        )

    def handle(self, *args, **options):
        include_drafts = options.get('include_drafts')
        drafts_only = options.get('drafts_only')
        force = options.get('force')
        slugs = options.get('slugs') or []

        if drafts_only:
            docs = PDFDocument.objects.filter(is_published=False)
        elif include_drafts:
            docs = PDFDocument.objects.all()
        else:
            docs = PDFDocument.objects.filter(is_published=True)

        if slugs:
            docs = docs.filter(slug__in=slugs)

        for doc in docs:
            self.stdout.write(f'Processing: {doc.title} ({doc.slug})')
            if not doc.file:
                self.stdout.write(self.style.WARNING('  Skipping: no PDF file attached'))
                continue

            page_count = doc.page_count or 1
            self.stdout.write(f'  Downloading PDF ({page_count} pages)...')
            with local_path_for_field_file(doc.file) as pdf_path:
                if not pdf_path:
                    self.stdout.write(self.style.WARNING('  Skipping: PDF not accessible from storage'))
                    continue

                self.stdout.write('  PDF ready, checking page cache...')
                cached_count = 0
                generated_count = 0
                failed_count = 0

                for page_num in range(1, page_count + 1):
                    _, output_path = pdf_page_cache_path(doc.slug, page_num)
                    if not force and cached_page_image_exists(output_path, slug=doc.slug, page_num=page_num):
                        cached_count += 1
                        continue

                    self.stdout.write(f'  Generating page {page_num}/{page_count}...')
                    try:
                        result = generate_page_image(
                            pdf_path,
                            page_num,
                            output_path,
                            force_regenerate=force,
                            slug=doc.slug,
                        )
                        if result:
                            generated_count += 1
                            self.stdout.write(f'  Cached page {page_num}/{page_count}')
                        else:
                            failed_count += 1
                            self.stdout.write(self.style.WARNING(f'  Failed to cache page {page_num}/{page_count}'))
                    except Exception as exc:
                        failed_count += 1
                        self.stdout.write(self.style.ERROR(f'  Error caching page {page_num}/{page_count}: {exc}'))

                self.stdout.write(
                    f'  Done: {cached_count} already cached, {generated_count} generated, {failed_count} failed'
                )

        self.stdout.write(self.style.SUCCESS('Pre-caching complete.'))
