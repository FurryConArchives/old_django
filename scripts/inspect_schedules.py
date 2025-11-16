import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'furry_archive.settings')
django.setup()

from archive.models import Schedule

def print_matches():
    print('Searching schedules for "all"...')
    qs = Schedule.objects.filter(slug__icontains='all')
    if not qs.exists():
        qs = Schedule.objects.filter(convention_name__icontains='All')

    if not qs.exists():
        print('No schedules found matching "all" in slug or convention_name.')
    else:
        for s in qs:
            print(s.id, s.slug, s.type, s.year, 'deleted' if s.deleted else 'active', 'has_events' if s.events_json else 'no_events', 'error='+str(s.error))

    print('\nRecent schedules (last 50):')
    for s in Schedule.objects.order_by('-id')[:50]:
        print(s.id, s.slug, s.type, s.year, s.deleted)

if __name__ == '__main__':
    print_matches()
