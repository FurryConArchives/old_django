from datetime import datetime
import os
import secrets
import shutil

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models
from django.utils.text import slugify
from pdf2image import convert_from_path
from pypdf import PdfReader, PdfWriter

from .utils import local_path_for_field_file, save_field_file_from_path
from .models_physical import PhysicalInventoryItem, VaultContributorProfile
from .models_sitevisit import ScheduleAutoFetchSetting, SiteVisit

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None
    TESSERACT_AVAILABLE = False


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    description = models.TextField(blank=True)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subcategories')
    order = models.IntegerField(default=0, help_text='Display order')
    logo = models.TextField(blank=True, default='', help_text='Base64-encoded logo image (4:1 aspect ratio recommended). Store as data URI (data:image/png;base64,...)')
    location = models.CharField(max_length=200, blank=True, help_text='Location of the convention (e.g., "Boston, MA" or "Online")')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} / {self.name}"
        return self.name

    def get_full_path(self):
        if self.parent:
            return f"{self.parent.get_full_path()} / {self.name}"
        return self.name

    def get_primary_convention_name(self):
        from django.db.models import Count

        documents = PDFDocument.objects.filter(category=self, is_published=True).exclude(convention_name='')
        if documents.exists():
            convention_counts = documents.values('convention_name').annotate(count=Count('id')).order_by('-count')
            if convention_counts:
                return convention_counts[0]['convention_name']
        return None

    def get_active_years(self):
        from django.core.cache import cache
        from archive.consurf_slugs import expand_consurf_slug

        if not self.slug:
            return []

        cache_key = f'external:category:years:{self.slug}'
        cached_years = cache.get(cache_key)
        if cached_years is not None:
            return cached_years

        try:
            for slug_candidate in expand_consurf_slug(self.slug):
                response = requests.get(
                    f'https://consurf.net/api/external/conventions/{slug_candidate}',
                    timeout=2,
                )
                if response.status_code != 200:
                    continue
                data = response.json()
                years = []

                if isinstance(data, dict):
                    events = data.get('events') or data.get('result') or []
                    for event in events or []:
                        start_date = event.get('startDate')
                        if start_date:
                            try:
                                year = datetime.fromisoformat(start_date.replace('Z', '+00:00')).year
                                if year not in years:
                                    years.append(year)
                            except Exception:
                                pass

                    if not years:
                        iterations = data.get('iterations') or {}
                        first = iterations.get('first') or data.get('first') or data.get('firstEventDate')
                        latest = iterations.get('latest') or data.get('latest') or data.get('lastEventDate')
                        try:
                            first_year = datetime.fromisoformat(first.replace('Z', '+00:00')).year if first else None
                            latest_year = datetime.fromisoformat(latest.replace('Z', '+00:00')).year if latest else None
                        except Exception:
                            first_year = None
                            latest_year = None
                        if first_year and latest_year and latest_year >= first_year:
                            years = list(range(first_year, latest_year + 1))

                years = sorted(set(years))
                cache.set(cache_key, years, 60 * 60 * 12)
                return years
        except Exception:
            pass

        cache.set(cache_key, [], 60 * 60 * 12)
        return []

    def get_year_range(self):
        years = self.get_active_years()
        if not years:
            return None
        years = sorted({int(year) for year in years if year is not None})
        if not years:
            return None
        if len(years) == 1:
            return str(years[0])
        return f'{years[0]}-{years[-1]}'

    def get_latest_event(self):
        from django.core.cache import cache
        from archive.consurf_slugs import expand_consurf_slug

        if not self.slug:
            return None

        cache_key = f'external:category:latest:{self.slug}'
        cached_event = cache.get(cache_key)
        if cached_event == '__none__':
            return None
        if cached_event is not None:
            return cached_event

        try:
            years = self.get_active_years()
            if not years:
                cache.set(cache_key, '__none__', 60 * 60 * 12)
                return None

            latest_year = max(years)
            for slug_candidate in expand_consurf_slug(self.slug):
                response = requests.get(
                    f'https://consurf.net/api/external/conventions/{slug_candidate}',
                    timeout=2,
                )
                if response.status_code != 200:
                    continue
                try:
                    data = response.json()
                except Exception:
                    data = None

                if data and isinstance(data, dict):
                    events = data.get('events') or data.get('result') or []
                    for event in events or []:
                        start = event.get('startDate') or event.get('date')
                        ev_year = None
                        if start:
                            try:
                                ev_year = datetime.fromisoformat(start.replace('Z', '+00:00')).year
                            except Exception:
                                ev_year = None
                        if ev_year is None:
                            try:
                                iter_field = event.get('iteration') or event.get('year') or event.get('eventYear')
                                if iter_field:
                                    ev_year = int(iter_field)
                            except Exception:
                                ev_year = None

                        if ev_year == latest_year:
                            cache.set(cache_key, event, 60 * 60 * 12)
                            return event

                    candidates = []
                    for event in events or []:
                        try:
                            start = event.get('startDate') or event.get('date')
                            if not start:
                                continue
                            candidates.append((datetime.fromisoformat(start.replace('Z', '+00:00')), event))
                        except Exception:
                            continue

                    if candidates:
                        candidates.sort(key=lambda item: item[0], reverse=True)
                        cache.set(cache_key, candidates[0][1], 60 * 60 * 12)
                        return candidates[0][1]
        except Exception:
            pass

        cache.set(cache_key, '__none__', 60 * 60 * 12)
        return None

    @property
    def year_range(self):
        if hasattr(self, '_cached_year_range'):
            return self._cached_year_range
        return self.get_year_range()

    @year_range.setter
    def year_range(self, value):
        self._cached_year_range = value

    @property
    def latest_event(self):
        if hasattr(self, '_cached_latest_event'):
            return self._cached_latest_event
        return self.get_latest_event()

    @latest_event.setter
    def latest_event(self, value):
        self._cached_latest_event = value

    def get_consurf_public_url(self):
        """Public Consurf page for this convention (used by browse-card banners)."""
        if not self.slug:
            return None

        event = self.get_latest_event()
        if isinstance(event, dict):
            source = (event.get('source') or '').lower()
            if source == 'mlpcon':
                name = (event.get('name') or self.name or '').strip()
                mlpcon_slug = slugify(name).replace('-', '') if name else self.slug.replace('-', '')
                return f'https://mlpcon.info/{mlpcon_slug}' if mlpcon_slug else None
            url = event.get('url')
            if url:
                return url
            slug_val = event.get('slug') or event.get('id')
        else:
            slug_val = None

        slug_base = (slug_val or self.slug or '').strip('-')
        if not slug_base:
            return None

        years = self.get_active_years()
        year = max(years) if years else None
        if year:
            return f'https://consurf.net/{slug_base}-{year}'
        return f'https://consurf.net/{slug_base}'


class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Tag'
        verbose_name_plural = 'Tags'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class PDFDocument(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    file = models.FileField(upload_to='pdfs/%Y/%m/', validators=[FileExtensionValidator(allowed_extensions=['pdf'])], help_text='Upload PDF files only')

    description = models.TextField(blank=True, help_text='Brief description of the document')
    year = models.PositiveIntegerField(null=True, blank=True, help_text='Year the document was created or published')
    convention_name = models.CharField(max_length=200, blank=True, help_text='Name of the convention (if applicable)')
    author = models.CharField(max_length=200, blank=True, help_text='Original author or creator of the document')
    tags = models.ForeignKey('Tag', on_delete=models.SET_NULL, null=True, blank=True, related_name='documents', help_text='Tag for this document')

    donated_by = models.CharField(max_length=200, blank=True, help_text='Name of the person or organization who donated this document')
    donated_by_telegram = models.CharField(max_length=100, blank=True, help_text='Telegram username (without @) for profile picture (optional)')
    donor_avatar = models.ImageField(upload_to='donor_avatars/', blank=True, null=True, help_text='Profile picture of the donor (optional)')
    contributor_notes = models.TextField(blank=True, help_text='Public notes from the contributor about this document (optional)')

    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='documents')

    file_size = models.PositiveIntegerField(default=0, editable=False, help_text='File size in bytes')
    page_count = models.PositiveIntegerField(null=True, blank=True, editable=False, help_text='Number of pages in the PDF (auto-extracted)')
    is_scanned = models.BooleanField(default=False, help_text='Whether this PDF is scanned (image-based) or clean copy (text-based). Must be manually set.')
    text_percentage = models.FloatField(default=0.0, editable=False, help_text='Percentage of pages with extractable text (indicates quality of digital copy)')

    ocr_text = models.TextField(blank=True, editable=False, help_text='Extracted text content from PDF via OCR for full-text search')
    ocr_processed = models.BooleanField(default=False, editable=False, help_text='Whether OCR processing has been completed for this document')

    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='uploaded_documents')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_published = models.BooleanField(default=True, help_text='Whether this document is visible to users')
    takedown_by_request = models.BooleanField(default=False, help_text='Flag if this document was taken down by request (e.g., DMCA)')
    takedown_explanation = models.TextField(blank=True, help_text='Explanation for takedown (who/why requested, e.g., DMCA details)')

    download_count = models.PositiveIntegerField(default=0, editable=False)

    CC_LICENSE_CHOICES = [
        ('', 'No License Specified'),
        ('CC0', 'CC0 - Public Domain Dedication'),
        ('CC-BY', 'CC BY - Attribution'),
        ('CC-BY-SA', 'CC BY-SA - Attribution-ShareAlike'),
        ('CC-BY-ND', 'CC BY-ND - Attribution-NoDerivs'),
        ('CC-BY-NC', 'CC BY-NC - Attribution-NonCommercial'),
        ('CC-BY-NC-SA', 'CC BY-NC-SA - Attribution-NonCommercial-ShareAlike'),
        ('CC-BY-NC-ND', 'CC BY-NC-ND - Attribution-NonCommercial-NoDerivs'),
        ('ALL-RIGHTS-RESERVED', 'All Rights Reserved'),
        ('PERMISSION-GRANTED', 'Explicit Permission Granted'),
    ]

    creative_commons_license = models.CharField(max_length=50, choices=CC_LICENSE_CHOICES, default='', blank=True, help_text='Creative Commons or other license type')
    copyright_holder = models.CharField(max_length=255, blank=True, help_text='Copyright holder name (e.g., convention name, organization)')
    copyright_year = models.PositiveIntegerField(null=True, blank=True, help_text='Copyright year')

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'PDF Document'
        verbose_name_plural = 'PDF Documents'

    def extract_pdf_metadata(self):
        if not self.file:
            return
        try:
            with local_path_for_field_file(self.file) as pdf_path:
                if not pdf_path or not os.path.exists(pdf_path):
                    return
                pdf = PdfReader(pdf_path)
                self.page_count = len(pdf.pages)
                pages_with_text = 0
                sample_size = min(10, self.page_count)
                for i in range(sample_size):
                    try:
                        text = pdf.pages[i].extract_text()
                        if text and len(text.strip()) > 50:
                            pages_with_text += 1
                    except Exception:
                        continue
                self.text_percentage = (pages_with_text / sample_size) * 100 if sample_size > 0 else 0
        except Exception as e:
            print(f'Error extracting PDF metadata: {e}')
            self.page_count = None
            self.text_percentage = 0.0

    def extract_ocr_text(self, force_reprocess=False):
        if self.ocr_text and self.ocr_text.strip() and not force_reprocess:
            self.ocr_processed = True
            return
        if not self.file or (self.ocr_processed and not force_reprocess):
            return
        if not TESSERACT_AVAILABLE:
            print('Warning: pytesseract not available. OCR functionality disabled.')
            return

        try:
            with local_path_for_field_file(self.file) as pdf_path:
                if not pdf_path or not os.path.exists(pdf_path):
                    return

                all_text = []
                try:
                    pdf = PdfReader(pdf_path)
                    direct_text = []
                    for page in pdf.pages:
                        try:
                            text = page.extract_text()
                            if text and text.strip():
                                direct_text.append(text.strip())
                        except Exception:
                            continue
                    if direct_text and len(' '.join(direct_text)) > 100:
                        self.ocr_text = '\n\n'.join(direct_text)
                        self.ocr_processed = True
                        return
                except Exception as e:
                    print(f'Error extracting direct text: {e}')

                max_pages = min(self.page_count or 50, 50)
                images = convert_from_path(pdf_path, first_page=1, last_page=max_pages, dpi=300, fmt='jpeg', strict=False, thread_count=1, use_cropbox=True)
                for i, image in enumerate(images, 1):
                    try:
                        page_text = pytesseract.image_to_string(image, lang='eng')
                        if page_text and page_text.strip():
                            all_text.append(f'--- Page {i} ---\n{page_text.strip()}')
                    except Exception as e:
                        print(f'Error processing page {i} with OCR: {e}')

                if all_text:
                    self.ocr_text = '\n\n'.join(all_text)
                else:
                    self.ocr_text = ''
                self.ocr_processed = True
        except Exception as e:
            print(f'Error during OCR extraction for {self.title}: {e}')
            self.ocr_processed = True

    def embed_ocr_text_in_pdf(self):
        if not self.file or not TESSERACT_AVAILABLE:
            return False
        try:
            with local_path_for_field_file(self.file) as pdf_path:
                if not pdf_path or not os.path.exists(pdf_path):
                    return False

                backup_path = pdf_path + '.backup'
                shutil.copy2(pdf_path, backup_path)
                temp_files = []
                try:
                    max_pages = min(self.page_count or 50, 50)
                    images = convert_from_path(pdf_path, first_page=1, last_page=max_pages, dpi=300, fmt='jpeg', use_cropbox=True, strict=False, thread_count=1)
                    if not images:
                        return False

                    ocr_pdf_pages = []
                    for i, image in enumerate(images, 1):
                        temp_img_path = f'{pdf_path}.temp_page_{i}.jpg'
                        image.save(temp_img_path, 'JPEG')
                        temp_files.append(temp_img_path)

                        temp_pdf_path = f'{pdf_path}.temp_ocr_{i}.pdf'
                        pdf_bytes = pytesseract.image_to_pdf_or_hocr(temp_img_path, lang='eng', extension='pdf')
                        if isinstance(pdf_bytes, str):
                            pdf_bytes = pdf_bytes.encode('utf-8')
                        with open(temp_pdf_path, 'wb') as temp_pdf_file:
                            temp_pdf_file.write(pdf_bytes)
                        temp_files.append(temp_pdf_path)
                        if os.path.exists(temp_pdf_path):
                            ocr_pdf_pages.append(temp_pdf_path)

                    if not ocr_pdf_pages:
                        if os.path.exists(backup_path):
                            shutil.copy2(backup_path, pdf_path)
                            os.remove(backup_path)
                        return False

                    original_reader = PdfReader(pdf_path)
                    writer = PdfWriter()
                    for page_num in range(1, len(original_reader.pages) + 1):
                        original_page = original_reader.pages[page_num - 1]
                        if page_num <= len(ocr_pdf_pages):
                            try:
                                ocr_reader = PdfReader(ocr_pdf_pages[page_num - 1])
                                if ocr_reader.pages:
                                    writer.add_page(original_page)
                                    merged_page = writer.pages[-1]
                                    ocr_page = ocr_reader.pages[0]
                                    if hasattr(original_page, 'mediabox'):
                                        merged_page.mediabox = original_page.mediabox
                                    if hasattr(original_page, 'cropbox'):
                                        merged_page.cropbox = original_page.cropbox
                                    merged_page.merge_page(ocr_page)
                                    if hasattr(original_page, 'mediabox'):
                                        merged_page.mediabox = original_page.mediabox
                                    if hasattr(original_page, 'cropbox'):
                                        merged_page.cropbox = original_page.cropbox
                                    continue
                            except Exception as e:
                                print(f'Error merging OCR page {page_num}: {e}')
                        writer.add_page(original_page)

                    output_pdf_path = f'{pdf_path}.embedded.pdf'
                    with open(output_pdf_path, 'wb') as output_file:
                        writer.write(output_file)

                    save_field_file_from_path(self.file, output_pdf_path)

                    for temp_file in temp_files:
                        if os.path.exists(temp_file):
                            try:
                                os.remove(temp_file)
                            except Exception:
                                pass

                    for extra_path in (backup_path, output_pdf_path):
                        if os.path.exists(extra_path):
                            os.remove(extra_path)

                    return True
                except Exception as e:
                    if os.path.exists(backup_path):
                        shutil.copy2(backup_path, pdf_path)
                        os.remove(backup_path)

                    for temp_file in temp_files:
                        if os.path.exists(temp_file):
                            try:
                                os.remove(temp_file)
                            except Exception:
                                pass

                    raise e
        except Exception as e:
            print(f'Error during OCR embedding for {self.title}: {e}')
            return False

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)
            slug = base_slug
            counter = 1
            while PDFDocument.objects.filter(slug=slug).exists():
                slug = f'{base_slug}-{counter}'
                counter += 1
            self.slug = slug

        file_changed = False
        if self.file:
            if self.pk:
                try:
                    old_instance = PDFDocument.objects.get(pk=self.pk)
                    file_changed = old_instance.file.name != self.file.name
                except PDFDocument.DoesNotExist:
                    file_changed = True
            else:
                file_changed = True

            self.file_size = self.file.size
            if file_changed and self.slug:
                try:
                    from .pdf_cache import clear_document_page_cache
                    clear_document_page_cache(self.slug, page_count=self.page_count)
                except Exception:
                    pass

            if not self.page_count or file_changed:
                self.extract_pdf_metadata()

        super().save(*args, **kwargs)

        if file_changed and not self.ocr_text:
            try:
                self.extract_ocr_text()
                if self.ocr_processed:
                    super().save(update_fields=['ocr_text', 'ocr_processed'])
            except Exception as e:
                print(f'Error during OCR in save(): {e}')

    def __str__(self):
        return self.title

    @property
    def file_size_mb(self):
        return round(self.file_size / (1024 * 1024), 2)

    @property
    def filename(self):
        return os.path.basename(self.file.name)

    @property
    def telegram_avatar(self):
        if self.donated_by_telegram:
            class TelegramAvatar:
                def __init__(self, username):
                    self.username = username

                @property
                def url(self):
                    return f'/internal/api/telegram-avatar/{self.username}'

            return TelegramAvatar(self.donated_by_telegram)
        return None

    def increment_downloads(self):
        self.download_count += 1
        self.save(update_fields=['download_count'])


class DownloadLog(models.Model):
    document = models.ForeignKey(PDFDocument, on_delete=models.CASCADE, related_name='download_logs')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    downloaded_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    referer = models.CharField(max_length=500, null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    location = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        ordering = ['-downloaded_at']
        verbose_name = 'Download Log'
        verbose_name_plural = 'Download Logs'

    def __str__(self):
        return f"{self.document.title} - {self.downloaded_at}"


class Schedule(models.Model):
    SCHEDULE_TYPE_CHOICES = [
        ('sched', 'Sched'),
        ('guidebook', 'Guidebook'),
        ('sessionize', 'Sessionize'),
        ('pretalx', 'Pretalx'),
        ('furconnect', 'Furconnect'),
        ('local', 'Local JSON'),
    ]
    type = models.CharField(max_length=20, choices=SCHEDULE_TYPE_CHOICES)
    category = models.ForeignKey('Category', on_delete=models.CASCADE, null=True, blank=True, related_name='schedules', help_text='Convention/category for this schedule')
    convention_name = models.CharField(max_length=100, blank=True, help_text='Convention name for slug parsing (e.g., Anthro New England)')
    slug = models.CharField(max_length=100, help_text='URL route slug; for Pretalx, may be stored as event|host (e.g. gh-25|schedule.slofurs.org)')
    year = models.IntegerField(null=True, blank=True, help_text='Year of the schedule (e.g., 2026)')
    events_json = models.TextField(blank=True, help_text='Cached events as JSON')
    last_updated = models.DateTimeField(auto_now=True)
    error = models.TextField(blank=True, null=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = ('type', 'slug', 'year')
        verbose_name = 'Schedule'
        verbose_name_plural = 'Schedules'

    def __str__(self):
        return f"{self.type}: {self.slug}"

    @property
    def route_slug(self):
        if self.category_id and self.year:
            cat_slug = getattr(self.category, 'slug', None)
            if not cat_slug and self.category_id:
                try:
                    cat_slug = Category.objects.filter(pk=self.category_id).values_list('slug', flat=True).first()
                except Exception:
                    cat_slug = None
            if cat_slug:
                return f'{cat_slug}-{self.year}'
        if self.type == 'furconnect':
            from archive.furconnect_schedule import furconnect_route_slug
            return furconnect_route_slug(self.slug)
        from archive.pretalx_client import schedule_route_slug
        return schedule_route_slug(self)


class AppKey(models.Model):
    SCOPE_API = 'api'
    SCOPE_CON_DASHBOARD = 'con-dashboard'
    API_ROUTE_SCOPES = (
        'site',
        'home',
        'pages',
        'documents',
        'categories',
        'schedules',
        'search',
        'vault',
        'tags',
        'stats',
        'telegram',
    )
    LEGACY_SCOPE_MAP = {
        'all': frozenset({SCOPE_API, SCOPE_CON_DASHBOARD}),
        'convention': frozenset({SCOPE_CON_DASHBOARD}),
    }
    SCOPE_CHOICES = [
        (SCOPE_API, 'Full API'),
        *((name, name) for name in API_ROUTE_SCOPES),
        (SCOPE_CON_DASHBOARD, 'Dashboard'),
    ]
    LEGACY_SCOPE_CHOICES = [
        ('convention', 'Convention-scoped (analytics only)'),
        ('all', 'All scope (full API access)'),
    ]

    convention = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='app_keys', db_constraint=False, help_text='Convention this app key is allowed to access')
    key = models.CharField(max_length=128, unique=True, db_index=True)
    name = models.CharField(max_length=150, blank=True, help_text='Human-friendly label')
    scope = models.CharField(
        max_length=255,
        default='convention',
        help_text='Comma-separated scopes, optional :requests-per-minute suffix. Legacy values: convention, all.',
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, db_constraint=False)
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'App Key'
        verbose_name_plural = 'App Keys'

    def __init__(self, *args, **kwargs):
        has_scopes = 'scopes' in kwargs
        has_rate = 'rate_per_minute' in kwargs
        scopes = kwargs.pop('scopes', None)
        rate = kwargs.pop('rate_per_minute', None)
        super().__init__(*args, **kwargs)
        if has_scopes or has_rate:
            current_scopes = scopes if has_scopes and scopes else self.resolved_scopes()
            current_rate = rate if has_rate else self.rate_per_minute
            self.scope = self.encode_scope_field(current_scopes, current_rate)

    @classmethod
    def encode_scope_field(cls, scopes, rate_per_minute=None):
        values = {scope for scope in (scopes or []) if scope}
        if values == {cls.SCOPE_API, cls.SCOPE_CON_DASHBOARD}:
            encoded = 'all'
        elif values == {cls.SCOPE_CON_DASHBOARD} or not values:
            encoded = 'convention'
        elif values == {cls.SCOPE_API}:
            encoded = cls.SCOPE_API
        else:
            encoded = ','.join(sorted(values))
        try:
            rate = int(rate_per_minute)
        except (TypeError, ValueError):
            rate = 0
        if rate > 0:
            encoded = f'{encoded}:{rate}'
        return encoded

    def parse_scope_field(self):
        raw = (self.scope or '').strip()
        rate = None
        if ':' in raw:
            head, tail = raw.rsplit(':', 1)
            if tail.isdigit():
                raw = head
                rate = int(tail)
        if raw in self.LEGACY_SCOPE_MAP:
            return set(self.LEGACY_SCOPE_MAP[raw]), rate
        return {part.strip() for part in raw.split(',') if part.strip()}, rate

    def resolved_scopes(self):
        return self.parse_scope_field()[0]

    @property
    def scopes(self):
        return sorted(self.resolved_scopes())

    @scopes.setter
    def scopes(self, value):
        self.scope = self.encode_scope_field(value, self.rate_per_minute)

    @property
    def rate_per_minute(self):
        return self.parse_scope_field()[1]

    @rate_per_minute.setter
    def rate_per_minute(self, value):
        self.scope = self.encode_scope_field(self.resolved_scopes(), value)

    def set_access(self, scopes, rate_per_minute=None):
        self.scope = self.encode_scope_field(scopes, rate_per_minute)

    def has_scope(self, needed):
        if not needed:
            return True
        scopes = self.resolved_scopes()
        if needed == self.SCOPE_CON_DASHBOARD:
            return self.SCOPE_CON_DASHBOARD in scopes
        if self.SCOPE_API in scopes:
            return True
        return needed in scopes

    def scope_labels(self):
        labels = dict(self.SCOPE_CHOICES)
        return [labels.get(scope, scope) for scope in sorted(self.resolved_scopes())]

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = secrets.token_urlsafe(48)
        from django.db import connection
        from django.db.utils import DataError

        from .schema import ensure_appkey_scope_column

        try:
            super().save(*args, **kwargs)
        except DataError as exc:
            if 'scope' not in str(exc).lower():
                raise
            try:
                connection.rollback()
            except Exception:
                pass
            if not ensure_appkey_scope_column():
                raise
            super().save(*args, **kwargs)

    def __str__(self):
        return self.name or (self.key[:12] + '...')


APIToken = AppKey


class DiscordProfile(models.Model):
    discord_userid = models.CharField(max_length=32, unique=True)
    discord_username = models.CharField(max_length=64)
    avatar_url = models.URLField(blank=True)
    last_updated = models.DateTimeField(auto_now=True)
    kofi_donated_at = models.DateTimeField(null=True, blank=True)
    kofi_amount = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f'{self.discord_username} ({self.discord_userid})'


class KoFiDonation(models.Model):
    discord_profile = models.ForeignKey(DiscordProfile, null=True, blank=True, on_delete=models.SET_NULL, related_name='kofi_donations')
    name = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    avatar_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        parts = []
        if self.name:
            parts.append(self.name)
        if self.amount is not None:
            parts.append(f'${self.amount}')
        if self.discord_profile:
            parts.append(f'({self.discord_profile.discord_userid})')
        return ' '.join(parts) or '<KoFiDonation>'


class SiteBanner(models.Model):
    text = models.CharField(max_length=250, help_text='Banner text shown across the site')
    url = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text='Optional link when visitors click the banner (e.g. /documents or https://example.com)',
    )
    color = models.CharField(
        max_length=7,
        default='#0066cc',
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Use a valid hex color like #0066cc.')],
        help_text='Banner background color (hex)'
    )
    is_enabled = models.BooleanField(default=True, help_text='Show this banner on public pages')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='updated_site_banners')

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Site Banner'
        verbose_name_plural = 'Site Banners'

    def __str__(self):
        state = 'Enabled' if self.is_enabled else 'Disabled'
        return f'{state}: {self.text[:60]}'

    @property
    def link_url(self) -> str:
        return (self.url or '').strip()

    @property
    def opens_externally(self) -> bool:
        link = self.link_url.lower()
        return link.startswith('http://') or link.startswith('https://')


class OurFriend(models.Model):
    name = models.CharField(max_length=120, help_text='Display name shown in the footer and staff page')
    subtitle = models.CharField(
        max_length=80,
        blank=True,
        default='',
        help_text='Short label under the name (e.g. Convention, Application)',
    )
    url = models.URLField(help_text='Website link opened when the friend is clicked')
    telegram_username = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text='Telegram username used for the circular avatar (without @)',
    )
    order = models.IntegerField(default=0, help_text='Lower numbers appear first')
    is_enabled = models.BooleanField(default=True, help_text='Show this friend publicly')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Our Friend'
        verbose_name_plural = 'Our Friends'

    def __str__(self):
        state = 'Enabled' if self.is_enabled else 'Disabled'
        return f'{state}: {self.name}'

    @property
    def avatar_username(self) -> str:
        return (self.telegram_username or '').strip().lstrip('@')


class FaqEntry(models.Model):
    slug = models.SlugField(max_length=80, unique=True)
    question = models.CharField(max_length=255)
    answer = models.TextField(help_text='HTML is allowed. Shown on the website and in the app.')
    icon = models.CharField(
        max_length=50,
        blank=True,
        default='question-circle-fill',
        help_text='Bootstrap icon name without the bi- prefix, e.g. question-circle-fill',
    )
    order = models.IntegerField(default=0, help_text='Lower numbers appear first')
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'FAQ entry'
        verbose_name_plural = 'FAQ entries'

    def __str__(self):
        return self.question

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.question)[:80]
        super().save(*args, **kwargs)


class ContactChannel(models.Model):
    slug = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=80)
    value = models.CharField(max_length=200, help_text='Visible handle or address')
    url = models.CharField(max_length=500, blank=True, default='')
    icon = models.CharField(
        max_length=50,
        blank=True,
        default='envelope',
        help_text='Bootstrap icon name without the bi- prefix',
    )
    style_key = models.CharField(
        max_length=40,
        blank=True,
        default='',
        help_text='Optional CSS hook, e.g. telegram, email, discord',
    )
    order = models.IntegerField(default=0)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'Contact channel'
        verbose_name_plural = 'Contact channels'

    def __str__(self):
        return self.label


class SitePageSection(models.Model):
    PAGE_CHOICES = [
        ('rights', 'Rights & licensing'),
        ('preservation-policy', 'Preservation policy'),
        ('preservation-tips', 'Preservation tips'),
    ]

    page_slug = models.CharField(max_length=60, choices=PAGE_CHOICES, db_index=True)
    slug = models.SlugField(max_length=80)
    title = models.CharField(max_length=160)
    body = models.TextField(help_text='HTML is allowed.')
    order = models.IntegerField(default=0)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['page_slug', 'order', 'id']
        unique_together = [('page_slug', 'slug')]
        verbose_name = 'Site page section'
        verbose_name_plural = 'Site page sections'

    def __str__(self):
        return f'{self.get_page_slug_display()}: {self.title}'
