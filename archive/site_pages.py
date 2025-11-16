"""Database-backed FAQ, contact, and static page sections.

Defaults seed empty tables so a fresh deploy still has content. After that,
Django admin / the custom FAQ admin are the source of truth for the website
and the Android API.
"""

from django.db.utils import OperationalError, ProgrammingError
from django.utils.html import strip_tags
from django.utils.text import slugify

RIGHTS_SECTIONS = [
    {
        'id': 'overview',
        'title': 'Overview',
        'body': 'Documents in the archive may carry Creative Commons licenses, all-rights-reserved notices, or explicit permission from the copyright holder. We do not claim ownership of hosted materials.',
    },
    {
        'id': 'reuse',
        'title': 'Reuse',
        'body': 'Check each document’s license and copyright fields before reuse. When a license is unspecified, contact the copyright holder or us before redistributing.',
    },
    {
        'id': 'takedown',
        'title': 'Takedowns',
        'body': 'Rights holders can request removal at dmca@furryconarchives.org. We also accept courtesy takedown requests from conventions and creators.',
    },
]

PRESERVATION_POLICY_SECTIONS = [
    {
        'id': 'mission',
        'title': 'Our Mission',
        'body': 'The Furry Con Archives preserves and makes accessible historical documents from the furry fandom. We do not claim ownership of the materials we host.',
    },
    {
        'id': 'what-we-archive',
        'title': 'What We Archive',
        'body': 'We prioritize publicly distributed, historically meaningful materials at real risk of being lost: conbooks, zines, flyers, and related ephemera.',
    },
    {
        'id': 'donating',
        'title': 'Donating Materials',
        'body': 'One copy or a whole shelf is welcome. Condition does not have to be perfect. Contact us to arrange intake or a loan-to-digitize.',
    },
    {
        'id': 'copyright',
        'title': 'Copyright and Fair Use',
        'body': 'We operate under fair-use principles for educational and preservation purposes and honor takedown requests from rights holders.',
    },
    {
        'id': 'takedown',
        'title': 'Takedown Requests',
        'body': 'Email dmca@furryconarchives.org. Include the document URL, your relationship to the work, and what you want removed.',
    },
]

PRESERVATION_TIPS_SECTIONS = [
    {
        'id': 'storage',
        'title': 'Storage',
        'body': 'Keep paper out of attics, basements, and damp rooms. Acid-free sleeves and a stable indoor climate do more than any fancy scanner.',
    },
    {
        'id': 'handling',
        'title': 'Handling',
        'body': 'Wash hands, avoid forcing a cracked spine flat, and never use tape on old paper. Photograph or scan before a book gets too brittle.',
    },
    {
        'id': 'digitize',
        'title': 'Digitize what you can',
        'body': 'A phone photo of every page is better than a perfect scan that never happens. Send us files or originals and we will help.',
    },
]


FAQ_SEED = [
    {
        'slug': 'what-is-fca',
        'icon': 'question-circle-fill',
        'question': 'What is the Furry Con Archives?',
        'answer': (
            '<p>Basically, we\'re fandom historians on a mission. The Furry Con Archives is a '
            'community-driven digital library dedicated to preserving the history and culture of '
            'furry conventions—the real stuff that matters. We collect, digitize, and share rare '
            'conbooks, zines, flyers, and other artifacts so fans and historians can explore the '
            'roots and evolution of the fandom. Our goal is to make these materials accessible, '
            'permanent, and easy to discover for everyone.</p>'
        ),
    },
    {
        'slug': 'mission',
        'icon': 'bullseye',
        'question': 'What is your mission?',
        'answer': (
            '<p>Some people collect CDs, video games, or a very specific kind of vintage toy. '
            'Those hobbies look normal because the objects are already treated as valuable. '
            'Collecting furry convention materials is the same kind of special interest—just one '
            'that often gets called obsessive or strange because the stuff was printed to be '
            'disposable.</p>'
            '<p>This archive is the public product of that interest. We digitize and share '
            'conbooks, zines, and related ephemera so they are not only sitting in someone else\'s '
            'drawer, landfill, or forgotten storage box. We cannot casually change what we care '
            'about. We can put that focus to work for the fandom.</p>'
        ),
    },
    {
        'slug': 'commitment',
        'icon': 'shield-lock',
        'question': 'What is your commitment to preservation?',
        'answer': (
            '<p>This archive belongs to the community—not to us, not to any one person. We\'re '
            'serious about long-term preservation. If our team can\'t keep it running, the archive '
            'gets transferred to a trusted community member or established historical group. The '
            'point is: once something\'s here, it stays here. We\'re not going anywhere, and '
            'neither are your favorite conbooks.</p>'
        ),
    },
    {
        'slug': 'what-is-archived',
        'icon': 'file-pdf',
        'question': 'What kinds of documents are archived?',
        'answer': (
            '<p>All the stuff that makes fandom real:</p>'
            '<ul>'
            '<li>Conbooks and event programs</li>'
            '<li>Newsletters, zines, and flyers</li>'
            '<li>Historical documents and records</li>'
            '<li>Community art and artifacts</li>'
            '<li>Pretty much anything with cultural significance</li>'
            '</ul>'
        ),
    },
    {
        'slug': 'what-is-a-conbook',
        'icon': 'journal-bookmark',
        'question': 'What is a conbook?',
        'answer': (
            '<p>Okay, so imagine the yearbook from high school, but actually fun and with way '
            'better art. A conbook is the official "yearbook" of a furry convention—the physical '
            'souvenir that everyone takes home and actually treasures. It\'s part guidebook, part '
            'art portfolio, part autograph book, and 100% nostalgia fuel.</p>'
            '<p>During the day, you\'re flipping through it for maps and schedules to navigate the '
            'madness. At night, you\'re handing it to your favorite artists for signatures and '
            'sketches. Years later, you\'re still pulling it off the shelf going "oh man, I '
            'remember this." Even with fancy digital apps these days, the conbook is still the one '
            'physical thing people hoard as proof they were there. It\'s basically fandom '
            'archaeology.</p>'
        ),
    },
    {
        'slug': 'how-to-search',
        'icon': 'search',
        'question': 'How do I search for documents?',
        'answer': (
            '<p>We\'ve got multiple ways to dig around and find what you\'re looking for:</p>'
            '<ul>'
            '<li><strong>By Title:</strong> Search specific document names</li>'
            '<li><strong>Full-Text Search:</strong> Find keywords in actual document content (OCR-extracted)</li>'
            '<li><strong>By Metadata:</strong> Filter by artist, convention, year, or description</li>'
            '<li><strong>By Category:</strong> Browse by year, convention, or material type</li>'
            '</ul>'
        ),
    },
    {
        'slug': 'downloads',
        'icon': 'download',
        'question': 'Can I download documents?',
        'answer': (
            '<p>Yep! Every published document is yours to grab. Just hit the "Download PDF" button '
            'on any document page. We track PDF views in Umami so we know what people actually '
            'care about and can prioritize what to digitize next.</p>'
        ),
    },
    {
        'slug': 'contribute',
        'icon': 'upload',
        'question': 'How can I contribute documents?',
        'answer': (
            '<p>Please! Seriously, we\'re always hunting for stuff we don\'t have yet. Whether '
            'you\'ve got a dusty scan from a 90s con in your closet or a brand-new digital PDF '
            'from last month, it helps us build a more complete timeline of our history. '
            '<a href="/contact">Contact us</a> with what you\'ve got, and we\'ll figure out the '
            'rest. Bonus: you get bragging rights as a contributor to fandom history.</p>'
        ),
    },
    {
        'slug': 'preservation-policy',
        'icon': 'shield-check',
        'question': 'What is your preservation policy?',
        'answer': (
            '<p>Our preservation policy outlines how we handle documents, copyright, and takedown '
            'requests. For detailed information, please see our '
            '<a href="/preservation-policy">Preservation Policy</a> page.</p>'
        ),
    },
    {
        'slug': 'is-it-free',
        'icon': 'credit-card',
        'question': 'Is the archive really free?',
        'answer': (
            '<p>Absolutely—it\'s free and always will be. We believe history should be accessible '
            'to everyone, not locked behind a paywall. If you want to download a PDF, search for '
            'your favorite con, or just browse at 2am looking for nostalgia, go for it. No ads, '
            'no subscriptions, no weirdness.</p>'
        ),
    },
    {
        'slug': 'dont-throw-away',
        'icon': 'trash',
        'question': 'What should I do with old conbooks and materials I no longer need?',
        'answer': (
            '<p><strong>Please don\'t throw them away.</strong> What looks like clutter to you is '
            'often exactly what we are looking for.</p>'
            '<p>A stack of old conbooks in a closet, leftover inventory from a con, or a box '
            'someone inherited and never opened—those are the materials that usually disappear, '
            'not because anyone hates history, but because nobody knows what else to do with them. '
            'We do.</p>'
            '<p>One copy or a whole shelf: <a href="/contact">contact us</a>. Condition does not '
            'have to be perfect.</p>'
        ),
    },
    {
        'slug': 'leftover-stock',
        'icon': 'box2-heart',
        'question': 'Why do leftover con materials disappear?',
        'answer': (
            '<p>Most of the time, it is not malice. Conventions are built to run events, not to be '
            'museums. Leftover stock, older books, and random boxes of paper become a storage '
            'problem with no clear owner.</p>'
            '<ul>'
            '<li><strong>No plan for the clutter:</strong> Extra copies after registration, damaged '
            'stock, or old promo piles just sit there until someone needs the space.</li>'
            '<li><strong>Forgotten, then gone:</strong> Materials end up in closets, volunteer '
            'houses, or storage units for years. They are not being curated—they are being '
            'postponed. A move, a flood, or a leadership change finishes the job.</li>'
            '<li><strong>Low print runs:</strong> Smaller cons may only have printed a few hundred '
            'copies. Once those are gone, there is often no replacement source.</li>'
            '<li><strong>It does not look valuable:</strong> To people outside this special '
            'interest, a stack of old programs looks like junk mail with better art.</li>'
            '</ul>'
            '<p>If you work for a convention and have leftover stock, older inventory, or boxes '
            'you do not know what to do with: talk to us before they become trash by default. '
            '<a href="/contact">Contact us</a> and we can figure out logistics.</p>'
        ),
    },
    {
        'slug': 'fragile-books',
        'icon': 'exclamation-triangle',
        'question': 'Why are older and low-print-run booklets so fragile?',
        'answer': (
            '<p>A lot of older conbooks were printed cheaply, in small numbers, and then stored '
            'wherever space happened to exist—basements, attics, garages, forgotten storage units. '
            'That combination is rough:</p>'
            '<ul>'
            '<li><strong>Cheap paper:</strong> Yellows, goes brittle, and tears easily after a couple of decades.</li>'
            '<li><strong>Staples:</strong> Rust and stain adjacent pages.</li>'
            '<li><strong>Tiny print runs:</strong> If your copy is damaged or tossed, there may not be many others left.</li>'
            '<li><strong>Lost digital originals:</strong> Especially for 2000s-era books, the layout '
            'files often vanished with old drives and dead software. The printed copy someone took '
            'home may be the only reliable record of how the book looked.</li>'
            '</ul>'
            '<p>If you have older books, even imperfect ones, <a href="/contact">reach out</a> '
            'before they get too fragile to handle or too easy to throw away.</p>'
        ),
    },
    {
        'slug': 'donation-bin',
        'icon': 'inbox',
        'question': 'Can I just set up a donation bin at my con?',
        'answer': (
            '<p>It sounds easy, and we understand why people try it. In practice, unlabeled mixed '
            'piles usually sit until after the event—and then become another box nobody knows what '
            'to do with. Direct contact works better: we can say what we still need, what we '
            'already have, and how to get materials here without them disappearing into limbo.</p>'
            '<p>Leftover stock and bulk extras are still welcome. A pile of extras is not scrap; '
            'it is part of the record. <a href="/contact">Reach out</a> if you want us to take a look.</p>'
        ),
    },
    {
        'slug': 'what-happens-on-donate',
        'icon': 'gift',
        'question': 'What happens if I donate materials to you?',
        'answer': (
            '<p>We digitize what we can, keep materials findable online, and document what we '
            'receive. If you need originals back, loan-to-digitize is often possible. Shipping '
            'support may be available depending on the situation.</p>'
            '<p>We are a small project built around a special interest, not a giant institutional '
            'archive with unlimited facilities. What we <em>do</em> offer is focus: we actually '
            'want these materials, we know what gaps matter, and we will not treat leftover stock '
            'as an inconvenience.</p>'
            '<p><a href="/contact">Contact us</a> and we will work from there.</p>'
        ),
    },
    {
        'slug': 'funding',
        'icon': 'piggy-bank',
        'question': 'What happens if funding runs out?',
        'answer': (
            '<p>We\'re committed to long-term preservation, and we have safeguards in place to '
            'ensure the archive survives even if funding becomes tight. This is a core part of '
            'our mission.</p>'
            '<p>If we ever face financial difficulties that threaten our ability to continue '
            'operations, we will:</p>'
            '<ul>'
            '<li>Prioritize keeping the archive online and accessible</li>'
            '<li>Transfer our complete archive to a trusted community member or established historical organization</li>'
            '<li>Make our entire database and collection publicly available so the community can help preserve it</li>'
            '<li>Ensure no materials are ever lost or discarded</li>'
            '</ul>'
            '<p>This history belongs to the community, not to any single organization. We\'re just '
            'the current custodians, and we take that responsibility seriously.</p>'
        ),
    },
    {
        'slug': 'leftover-tossed',
        'icon': 'exclamation-circle',
        'question': 'Is it really that bad if leftover stock gets tossed?',
        'answer': (
            '<p>Nobody is coming after you. Most people who throw these things out are not trying '
            'to erase history—they are clearing space, ending a storage bill, or dealing with '
            'boxes that never had a next step.</p>'
            '<p>From our side of this hobby, though: once a low-print-run book is gone, it is '
            'often gone. That is why we ask people to <a href="/contact">talk to us first</a> if '
            'they are about to discard a collection, leftover inventory, or a box they do not know '
            'what to do with. We would rather take the "clutter" than watch it become landfill by '
            'default.</p>'
        ),
    },
    {
        'slug': 'money-donate',
        'icon': 'heart-fill',
        'question': 'How can I donate money to support the archive?',
        'answer': (
            '<p>First off: thanks for wanting to help. The archive runs on community '
            'support—storage costs money, digitization equipment costs money, keeping the servers '
            'running costs money.</p>'
            '<p>We\'re working on setting up donation options. Want to help keep this project '
            'alive? <a href="/contact">Get in touch</a> and let us know how you\'d like to '
            'contribute. We\'re building support options like:</p>'
            '<ul>'
            '<li>One-time donations</li>'
            '<li>Monthly recurring support</li>'
            '<li>Specific project sponsorships</li>'
            '<li>Cryptocurrency donations</li>'
            '<li>In-kind contributions (equipment, services, etc.)</li>'
            '</ul>'
            '<p>All donations go directly toward keeping the archive running and helping us '
            'digitize more materials. We deeply appreciate every contribution, whether it\'s $1 '
            'or $1,000!</p>'
        ),
    },
    {
        'slug': 'nonprofit',
        'icon': 'building',
        'question': 'Are you a non-profit organization?',
        'answer': (
            '<p>Not officially (yet). We could pursue non-profit status, but honestly? The '
            'administrative overhead, legal red tape, and tax filings would eat time and energy '
            'that\'s better spent actually preserving stuff. We\'re committed to the mission of '
            'keeping these materials safe and accessible, and we\'re willing to do the boring '
            'parts without the paperwork.</p>'
            '<p><strong>Real talk:</strong> Since we\'re not a registered non-profit, donations '
            'aren\'t tax deductible. We totally get if that matters to you—we\'re still grateful '
            'for any support we can get.</p>'
        ),
    },
]

CONTACT_SEED = [
    {
        'slug': 'telegram',
        'label': 'Telegram',
        'value': '@furryconarchives',
        'url': 'https://t.me/furryconarchives',
        'icon': 'telegram',
        'style_key': 'telegram',
    },
    {
        'slug': 'email',
        'label': 'Email',
        'value': 'info@furryconarchives.org',
        'url': 'mailto:info@furryconarchives.org',
        'icon': 'envelope-fill',
        'style_key': 'email',
    },
    {
        'slug': 'discord',
        'label': 'Discord',
        'value': 'discord.gg/Q9zWSKWVPw',
        'url': 'https://discord.gg/Q9zWSKWVPw',
        'icon': 'discord',
        'style_key': 'discord',
    },
    {
        'slug': 'dmca',
        'label': 'DMCA / takedown',
        'value': 'dmca@furryconarchives.org',
        'url': 'mailto:dmca@furryconarchives.org',
        'icon': 'shield-exclamation',
        'style_key': 'email',
    },
]

SECTION_SEED = [
    *[{'page_slug': 'rights', **item} for item in RIGHTS_SECTIONS],
    *[{'page_slug': 'preservation-policy', **item} for item in PRESERVATION_POLICY_SECTIONS],
    *[{'page_slug': 'preservation-tips', **item} for item in PRESERVATION_TIPS_SECTIONS],
]


def plain_text(html):
    return ' '.join(strip_tags(html or '').split())


def serialize_faq_entry(entry):
    html = entry.answer or ''
    return {
        'id': entry.slug,
        'question': entry.question,
        'answer': plain_text(html),
        'answer_html': html,
        'icon': entry.icon or '',
    }


def serialize_contact_channel(channel):
    return {
        'id': channel.slug,
        'label': channel.label,
        'value': channel.value,
        'url': channel.url or '',
        'icon': channel.icon or '',
        'style_key': channel.style_key or '',
    }


def serialize_page_section(section):
    return {
        'id': section.slug,
        'title': section.title,
        'body': plain_text(section.body),
        'body_html': section.body or '',
    }


def _db_ready(model):
    try:
        model.objects.exists()
        return True
    except (OperationalError, ProgrammingError):
        return False


def ensure_default_site_content():
    """Create the default FAQ / contact / section rows if those tables are empty."""
    from archive.models import ContactChannel, FaqEntry, SitePageSection

    if _db_ready(FaqEntry) and not FaqEntry.objects.exists():
        FaqEntry.objects.bulk_create([
            FaqEntry(
                slug=item['slug'],
                question=item['question'],
                answer=item['answer'],
                icon=item.get('icon') or 'question-circle-fill',
                order=(index + 1) * 10,
                is_published=True,
            )
            for index, item in enumerate(FAQ_SEED)
        ])

    if _db_ready(ContactChannel) and not ContactChannel.objects.exists():
        ContactChannel.objects.bulk_create([
            ContactChannel(
                slug=item['slug'],
                label=item['label'],
                value=item['value'],
                url=item.get('url') or '',
                icon=item.get('icon') or 'envelope',
                style_key=item.get('style_key') or '',
                order=(index + 1) * 10,
                is_published=True,
            )
            for index, item in enumerate(CONTACT_SEED)
        ])

    if _db_ready(SitePageSection) and not SitePageSection.objects.exists():
        SitePageSection.objects.bulk_create([
            SitePageSection(
                page_slug=item['page_slug'],
                slug=item.get('slug') or item.get('id') or slugify(item.get('title') or 'section'),
                title=item.get('title') or item.get('id') or 'Section',
                body=item.get('body') or '',
                order=(index + 1) * 10,
                is_published=True,
            )
            for index, item in enumerate(SECTION_SEED)
        ])


def published_faq_entries():
    from archive.models import FaqEntry

    ensure_default_site_content()
    if _db_ready(FaqEntry):
        rows = list(FaqEntry.objects.filter(is_published=True).order_by('order', 'id'))
        if rows:
            return rows
    return []


def published_faq_payloads():
    rows = published_faq_entries()
    if rows:
        return [serialize_faq_entry(row) for row in rows]
    return [
        {
            'id': item['slug'],
            'question': item['question'],
            'answer': plain_text(item['answer']),
            'answer_html': item['answer'],
            'icon': item.get('icon') or '',
        }
        for item in FAQ_SEED
    ]


def published_contact_channels():
    from archive.models import ContactChannel

    ensure_default_site_content()
    if _db_ready(ContactChannel):
        rows = list(ContactChannel.objects.filter(is_published=True).order_by('order', 'id'))
        if rows:
            return rows
    return []


def published_contact_payloads():
    rows = published_contact_channels()
    if rows:
        return [serialize_contact_channel(row) for row in rows]
    return [
        {
            'id': item['slug'],
            'label': item['label'],
            'value': item['value'],
            'url': item.get('url') or '',
            'icon': item.get('icon') or '',
            'style_key': item.get('style_key') or '',
        }
        for item in CONTACT_SEED
    ]


def published_page_section_payloads(page_slug):
    from archive.models import SitePageSection

    ensure_default_site_content()
    if _db_ready(SitePageSection):
        rows = list(
            SitePageSection.objects.filter(page_slug=page_slug, is_published=True).order_by('order', 'id')
        )
        if rows:
            return [serialize_page_section(row) for row in rows]
    defaults = {
        'rights': RIGHTS_SECTIONS,
        'preservation-policy': PRESERVATION_POLICY_SECTIONS,
        'preservation-tips': PRESERVATION_TIPS_SECTIONS,
    }.get(page_slug, [])
    return list(defaults)
