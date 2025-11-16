# Furry Con Archives

Django app that holds convention booklets, programs, and other PDFs from conventions.

This repository is kept for historical preservation. It is not maintained. PRs will be accepted if there is bugs or features that would greatly imporve this project.

Live site: https://furryconarchives.org

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).

## Run locally

Python 3. Copy `.env` from whatever you have; the file is gitignored. `USE_B2_STORAGE=true` sends uploads to Backblaze B2. Leave it false and files land in `media/`. Default database is SQLite (`db.sqlite3`).

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic
python manage.py createsuperuser
python manage.py runserver
```

Upload from `/admin/upload` once you are staff (via createsuperuser).

Tesseract is optional. Without it, OCR is skipped and search still uses titles, descriptions, and metadata.

```bash
# macOS
brew install tesseract

# Debian/Ubuntu
sudo apt-get install tesseract-ocr
```

Windows: https://github.com/UB-Mannheim/tesseract/wiki

OCR for documents that were uploaded before Tesseract was available:

```bash
python manage.py process_ocr
python manage.py process_ocr --all
python manage.py process_ocr --limit 10
python manage.py process_ocr --force
python manage.py process_ocr --skip-scanned
```git