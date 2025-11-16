from django import forms
from .models import PDFDocument, Category, Tag, SiteBanner, FaqEntry


class PDFUploadForm(forms.ModelForm):
    """Form for uploading PDF documents"""
    
    class Meta:
        model = PDFDocument
        fields = [
            'title',
            'slug',
            'file',
            'description',
            'category',
            'year',
            'convention_name',
            'author',
            'donated_by',
            'donated_by_telegram',
            'contributor_notes',
            'tags',
            'creative_commons_license',
            'copyright_holder',
            'copyright_year',
            'is_scanned',
            'is_published'
            , 'takedown_by_request', 'takedown_explanation'
        ]
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter document title'
            }),
            'slug': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Auto-generated from title (leave blank)'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Brief description of the document'
            }),
            'year': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'YYYY'
            }),
            'convention_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Convention name (if applicable)'
            }),
            'author': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Original author or creator'
            }),
            'donated_by': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Name of donor (optional)'
            }),
            'donated_by_telegram': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Telegram username without @ (optional)'
            }),
            'contributor_notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Public notes from the contributor (optional)'
            }),
            'creative_commons_license': forms.Select(attrs={
                'class': 'form-control'
            }),
            'copyright_holder': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Copyright holder name (optional)'
            }),
            'copyright_year': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Copyright year (optional)'
            }),
            'category': forms.Select(attrs={
                'class': 'form-control'
            }),
            'tags': forms.Select(attrs={
                'class': 'form-control'
            }),
            'is_scanned': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'is_published': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            })
        }
        help_texts = {
            'file': 'Upload PDF files only. Page count will be auto-detected.',
            'slug': 'URL-friendly version of the title. Auto-generated if left blank.',
            'creative_commons_license': 'Select the license type for this document. See Rights page for details.',
            'copyright_holder': 'Name of the copyright holder (e.g., convention name, organization)',
            'copyright_year': 'Year of copyright',
            'contributor_notes': 'Public notes from the contributor about this document. This will be displayed on the document page.',
            'is_scanned': 'Check if this is a scanned document (image-based). Must be manually set.',
            'is_published': 'Uncheck to hide this document from public view'
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make is_published checked by default
        if not self.instance.pk:
            self.fields['is_published'].initial = True
        
        # Make slug optional and help with auto-generation
        self.fields['slug'].required = False
        if self.instance and self.instance.pk:
            # For existing documents, show current slug
            self.fields['slug'].help_text = 'Leave blank to auto-generate from title'
        else:
            # For new documents, slug will be auto-generated
            self.fields['slug'].help_text = 'Will be auto-generated from title if left blank'

        # Tags field: show existing tags and allow selection
        self.fields['tags'].required = False
        try:
            self.fields['tags'].queryset = Tag.objects.all().order_by('name')
        except Exception:
            # In migration/detection phases Tag may not exist yet
            self.fields['tags'].queryset = Tag.objects.none()


class CategoryForm(forms.ModelForm):
    """Form for creating categories"""
    logo_file = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': 'image/*'
        }),
        help_text='Upload a logo image (will be stored as base64)'
    )
    
    class Meta:
        model = Category
        fields = ['name', 'description', 'parent', 'order', 'logo', 'location']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Category name'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'Optional description'
            }),
            'parent': forms.Select(attrs={
                'class': 'form-control'
            }),
            'order': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '0'
            }),
            'logo': forms.HiddenInput(),
            'location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Location (e.g., city, state)'
            })
        }
        help_texts = {
            'parent': 'Leave empty for top-level category',
            'order': 'Lower numbers appear first',
            'logo': 'Base64-encoded logo (auto-generated from uploaded file)',
            'location': 'Location of the convention (e.g., city, state)'
        }
    
    def save(self, commit=True):
        import base64
        category = super().save(commit=False)
        
        # If a new logo file was uploaded, encode it to base64
        if 'logo_file' in self.files and self.files['logo_file']:
            logo_file = self.files['logo_file']
            logo_data = logo_file.read()
            logo_base64 = base64.b64encode(logo_data).decode('utf-8')
            
            # Determine MIME type from file
            import mimetypes
            mime_type, _ = mimetypes.guess_type(logo_file.name)
            if not mime_type:
                # Fallback based on file extension
                ext = logo_file.name.lower().split('.')[-1] if '.' in logo_file.name else 'png'
                mime_types = {
                    'jpg': 'image/jpeg',
                    'jpeg': 'image/jpeg',
                    'png': 'image/png',
                    'gif': 'image/gif',
                    'webp': 'image/webp',
                    'svg': 'image/svg+xml'
                }
                mime_type = mime_types.get(ext, 'image/png')
            
            # Store as data URI
            category.logo = f'data:{mime_type};base64,{logo_base64}'
        elif not self.cleaned_data.get('logo') and not self.instance.pk:
            # If no logo and creating new category, clear the field
            category.logo = ''
        
        if commit:
            category.save()
        return category


class SiteBannerForm(forms.ModelForm):
    class Meta:
        model = SiteBanner
        fields = ['text', 'url', 'color', 'is_enabled']
        widgets = {
            'text': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Example: Site maintenance tonight at 11:00 PM ET.'
            }),
            'url': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'https://example.com or /documents'
            }),
            'color': forms.TextInput(attrs={
                'class': 'form-control',
                'type': 'color'
            }),
            'is_enabled': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
        }
        help_texts = {
            'text': 'Text shown inside the site-wide banner bar.',
            'url': 'Optional URL opened when someone clicks the banner.',
            'color': 'Pick the banner bar color.',
            'is_enabled': 'Turn banner visibility on/off for public pages.',
        }


class FaqEntryForm(forms.ModelForm):
    class Meta:
        model = FaqEntry
        fields = ['question', 'answer', 'icon', 'order', 'is_published', 'slug']
        widgets = {
            'question': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'What is a conbook?',
            }),
            'answer': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 8,
                'placeholder': '<p>HTML is allowed.</p>',
            }),
            'icon': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'question-circle-fill',
            }),
            'order': forms.NumberInput(attrs={'class': 'form-control'}),
            'is_published': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'slug': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Leave blank to generate from the question',
            }),
        }
        help_texts = {
            'answer': 'HTML is allowed. The website shows it as-is; the app gets a plain-text version.',
            'icon': 'Bootstrap Icons name without bi-, e.g. question-circle-fill.',
            'slug': 'Stable id used by the API. Leave blank to generate from the question.',
            'order': 'Lower numbers appear first.',
        }

