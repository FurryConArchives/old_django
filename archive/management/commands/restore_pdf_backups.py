"""
Django management command to check for and restore PDF backups.

Usage:
    python manage.py restore_pdf_backups                    # List all PDFs with backups
    python manage.py restore_pdf_backups --restore-all      # Restore all PDFs from backups
    python manage.py restore_pdf_backups --restore <slug>   # Restore specific PDF by slug
    python manage.py restore_pdf_backups --check            # Check which PDFs might be affected
"""

import os
import glob
from django.core.management.base import BaseCommand
from django.db.models import Q
from archive.models import PDFDocument
from archive.utils import local_path_for_field_file, save_field_file_from_path


class Command(BaseCommand):
    help = 'Check for and restore PDF backups created during OCR embedding'

    def add_arguments(self, parser):
        parser.add_argument(
            '--restore-all',
            action='store_true',
            help='Restore all PDFs that have backup files',
        )
        parser.add_argument(
            '--restore',
            type=str,
            help='Restore a specific PDF by slug',
        )
        parser.add_argument(
            '--check',
            action='store_true',
            help='Check which PDFs have backups and which might be affected',
        )
        parser.add_argument(
            '--list',
            action='store_true',
            help='List all PDFs with backup files',
        )

    def handle(self, *args, **options):
        if options['restore_all']:
            self.restore_all_backups()
        elif options['restore']:
            self.restore_backup(options['restore'])
        elif options['check']:
            self.check_backups()
        else:
            self.list_backups()

    def find_backup_files(self):
        """Find all backup files in the media directory"""
        backup_files = []
        # Get the media root from settings
        from django.conf import settings
        media_root = settings.MEDIA_ROOT
        
        # Search for .backup files
        pattern = os.path.join(media_root, '**', '*.backup')
        backup_files = glob.glob(pattern, recursive=True)
        
        return backup_files

    def list_backups(self):
        """List all PDFs that have backup files"""
        self.stdout.write(self.style.SUCCESS('\n=== PDF Backup Files Found ===\n'))
        
        backup_files = self.find_backup_files()
        
        if not backup_files:
            self.stdout.write(self.style.WARNING('No backup files found.'))
            return
        
        # Match backups to documents
        documents_with_backups = []
        for backup_path in backup_files:
            # Remove .backup extension to get original path
            original_path = backup_path[:-7]  # Remove '.backup'
            
            # Try to find the document
            try:
                # Get relative path from media root
                from django.conf import settings
                media_root = settings.MEDIA_ROOT
                relative_path = os.path.relpath(original_path, media_root)
                
                # Find document by file path
                document = PDFDocument.objects.filter(file__icontains=relative_path).first()
                
                if document:
                    documents_with_backups.append({
                        'document': document,
                        'backup_path': backup_path,
                        'original_path': original_path
                    })
                else:
                    self.stdout.write(self.style.WARNING(
                        f'Backup found but no matching document: {backup_path}'
                    ))
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'Error processing backup {backup_path}: {e}'
                ))
        
        if documents_with_backups:
            self.stdout.write(f'Found {len(documents_with_backups)} PDF(s) with backups:\n')
            for item in documents_with_backups:
                doc = item['document']
                self.stdout.write(
                    f"  • {doc.title} (slug: {doc.slug})\n"
                    f"    Backup: {item['backup_path']}\n"
                )
        else:
            self.stdout.write(self.style.WARNING('No matching documents found for backup files.'))

    def check_backups(self):
        """Check which PDFs have backups and which might be affected by cropping"""
        self.stdout.write(self.style.SUCCESS('\n=== PDF Backup Check ===\n'))
        
        # Find all documents
        all_docs = PDFDocument.objects.filter(is_published=True)
        self.stdout.write(f'Total published documents: {all_docs.count()}\n')
        
        # Find documents with backups
        backup_files = self.find_backup_files()
        docs_with_backups = []
        
        for backup_path in backup_files:
            original_path = backup_path[:-7]
            from django.conf import settings
            media_root = settings.MEDIA_ROOT
            relative_path = os.path.relpath(original_path, media_root)
            document = PDFDocument.objects.filter(file__icontains=relative_path).first()
            if document:
                docs_with_backups.append(document)
        
        self.stdout.write(f'Documents with backup files: {len(docs_with_backups)}\n')
        
        # Check which documents have OCR processed (might be affected)
        ocr_processed = all_docs.filter(ocr_processed=True)
        self.stdout.write(f'Documents with OCR processed: {ocr_processed.count()}\n')
        
        # Documents that might be affected (OCR processed but no backup)
        potentially_affected = ocr_processed.exclude(
            id__in=[d.id for d in docs_with_backups]
        )
        
        if potentially_affected.exists():
            self.stdout.write(self.style.WARNING(
                f'\n⚠️  {potentially_affected.count()} document(s) might be affected '
                f'(OCR processed but no backup found):\n'
            ))
            for doc in potentially_affected[:10]:  # Show first 10
                self.stdout.write(f"  • {doc.title} (slug: {doc.slug})")
            if potentially_affected.count() > 10:
                self.stdout.write(f"  ... and {potentially_affected.count() - 10} more")
        
        if docs_with_backups:
            self.stdout.write(self.style.SUCCESS(
                f'\n✓ {len(docs_with_backups)} document(s) have backups and can be restored:\n'
            ))
            for doc in docs_with_backups:
                self.stdout.write(f"  • {doc.title} (slug: {doc.slug})")

    def restore_backup(self, slug):
        """Restore a specific PDF from backup"""
        try:
            document = PDFDocument.objects.get(slug=slug, is_published=True)
        except PDFDocument.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Document with slug "{slug}" not found.'))
            return
        
        backup_path = None
        with local_path_for_field_file(document.file) as pdf_path:
            backup_path = pdf_path + '.backup'

            if not os.path.exists(backup_path):
                self.stdout.write(self.style.WARNING(
                    f'No backup found for {document.title} at {backup_path}'
                ))
                return

            try:
                import shutil
                shutil.copy2(backup_path, pdf_path)
                save_field_file_from_path(document.file, pdf_path)
                self.stdout.write(self.style.SUCCESS(
                    f'✓ Successfully restored {document.title} from backup'
                ))
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'Error restoring {document.title}: {e}'
                ))
            return

    def restore_all_backups(self):
        """Restore all PDFs that have backup files"""
        self.stdout.write(self.style.SUCCESS('\n=== Restoring All PDF Backups ===\n'))
        
        backup_files = self.find_backup_files()
        
        if not backup_files:
            self.stdout.write(self.style.WARNING('No backup files found.'))
            return
        
        restored = 0
        failed = 0
        
        for backup_path in backup_files:
            original_path = backup_path[:-7]
            
            # Check if original still exists
            if not os.path.exists(original_path):
                self.stdout.write(self.style.WARNING(
                    f'Original file not found, skipping: {original_path}'
                ))
                continue
            
            # Find matching document
            from django.conf import settings
            media_root = settings.MEDIA_ROOT
            relative_path = os.path.relpath(original_path, media_root)
            document = PDFDocument.objects.filter(file__icontains=relative_path).first()
            
            if not document:
                self.stdout.write(self.style.WARNING(
                    f'No document found for backup: {backup_path}'
                ))
                continue
            
            try:
                import shutil
                with local_path_for_field_file(document.file) as pdf_path:
                    shutil.copy2(backup_path, pdf_path)
                    save_field_file_from_path(document.file, pdf_path)
                self.stdout.write(self.style.SUCCESS(
                    f'✓ Restored: {document.title}'
                ))
                restored += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'✗ Failed to restore {document.title}: {e}'
                ))
                failed += 1
        
        self.stdout.write(self.style.SUCCESS(
            f'\n=== Restore Complete ===\n'
            f'Restored: {restored}\n'
            f'Failed: {failed}'
        ))

