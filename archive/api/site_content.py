"""Structured public site content for /v1/.

These payloads let a future standalone frontend render chrome and static pages
without scraping Django templates.
"""

from archive.site_pages import (
    CONTACT_SEED,
    FAQ_SEED,
    PRESERVATION_POLICY_SECTIONS,
    PRESERVATION_TIPS_SECTIONS,
    RIGHTS_SECTIONS,
    plain_text,
)

NAV_LINKS = [
    {'id': 'conventions', 'label': 'Conventions', 'path': '/conventions', 'api_path': '/v1/categories/'},
    {'id': 'documents', 'label': 'Documents', 'path': '/documents', 'api_path': '/v1/documents/'},
    {'id': 'schedules', 'label': 'Schedules', 'path': '/schedules/', 'api_path': '/v1/schedules/'},
    {'id': 'vault', 'label': 'The Vault', 'path': '/the-vault', 'api_path': '/v1/vault/'},
]

FOOTER_LINKS = [
    {'id': 'faq', 'label': 'FAQ', 'path': '/faq', 'api_path': '/v1/pages/faq/'},
    {'id': 'contact', 'label': 'Contact', 'path': '/contact', 'api_path': '/v1/pages/contact/'},
    {'id': 'staff', 'label': 'Staff', 'path': '/staff', 'api_path': '/v1/pages/staff/'},
    {'id': 'rights', 'label': 'Rights', 'path': '/rights', 'api_path': '/v1/pages/rights/'},
    {'id': 'preservation-policy', 'label': 'Preservation Policy', 'path': '/preservation-policy', 'api_path': '/v1/pages/preservation-policy/'},
    {'id': 'preservation-tips', 'label': 'Preservation Tips', 'path': '/preservation-tips', 'api_path': '/v1/pages/preservation-tips/'},
]

CONTACT_CHANNELS = [
    {
        'id': item['slug'],
        'label': item['label'],
        'value': item['value'],
        'url': item.get('url') or '',
    }
    for item in CONTACT_SEED
]

FAQ_ITEMS = [
    {
        'id': item['slug'],
        'question': item['question'],
        'answer': plain_text(item['answer']),
    }
    for item in FAQ_SEED
]

PAGE_CATALOG = [
    {
        'slug': 'home',
        'title': 'Home',
        'path': '/',
        'api_path': '/v1/home/',
        'summary': 'Featured conbooks, site stats, and the current banner.',
    },
    {
        'slug': 'faq',
        'title': 'Frequently Asked Questions',
        'path': '/faq',
        'api_path': '/v1/pages/faq/',
        'summary': 'What the archive is, how to search, donate, and request takedowns.',
    },
    {
        'slug': 'contact',
        'title': 'Contact',
        'path': '/contact',
        'api_path': '/v1/pages/contact/',
        'summary': 'Telegram, email, Discord, and the DMCA address.',
    },
    {
        'slug': 'staff',
        'title': 'Staff',
        'path': '/staff',
        'api_path': '/v1/pages/staff/',
        'summary': 'Board and volunteers who run the archive.',
    },
    {
        'slug': 'rights',
        'title': 'Rights & Licensing',
        'path': '/rights',
        'api_path': '/v1/pages/rights/',
        'summary': 'How we handle copyright, licenses, and reuse.',
    },
    {
        'slug': 'preservation-policy',
        'title': 'Preservation Policy',
        'path': '/preservation-policy',
        'api_path': '/v1/pages/preservation-policy/',
        'summary': 'Mission, donations, fair use, and takedown process.',
    },
    {
        'slug': 'preservation-tips',
        'title': 'Preservation Tips',
        'path': '/preservation-tips',
        'api_path': '/v1/pages/preservation-tips/',
        'summary': 'Practical advice for keeping paper con materials alive.',
    },
]

STAFF_MEMBERS = [
    {
        'username': 'furtabs',
        'display_name_override': 'Tabitha',
        'pronouns': 'she/her/hers',
        'role': 'Chairgoat',
        'bluesky_handle': 'tabs.gay',
        'discord_username': 'furtabs',
        'discord_id': '1009059379003265134',
        'description': 'Tabitha found her home in the furry/pony community after taking a leap of faith to attend a convention in early 2025. As "Chairgoat," she channels her archiving passion—previously focused on other genres—into preserving furry ephemera. While her special interest is often misunderstood due to extreme rarity and disposable nature of these books, it is no different than collecting vintage CDs or toys. Living with invisible disabilities like BPD and ASD Tabitha struggles with traditional employment. Instead, volunteering and safeguarding these overlooked pieces of furry history give her a vital sense of purpose and community connection.',
    },
    {
        'username': 'rabbitasaur',
        'display_name_override': 'Rabbitasaur',
        'pronouns': 'they/she',
        'role': 'Very Important Rabbitasaur',
        'description': 'Rabbitasaur is a long-time con staffer and con vendor. They first started out helping start up and run BronyCAN from 2013 till 2017. Within the furry and MLP fandom, Rabbitasaur has helped run different departments, such as VIP Guests and art/design. Over the years they have sold their art at conventions all across the USA and Canada! Rabbitasaur loves dinosaurs and has a passion for creating many different forms of art!',
        'bluesky_handle': 'rabbitasaur.bsky.social',
        'twitter_username': 'rabbitasaur',
        'discord_id': '143011032120426496',
        'discord_username': 'rabbitasaur',
    },
    {
        'username': 'cosmocat95',
        'display_name_override': 'Pepper',
        'pronouns': 'they/them/theirs',
        'role': 'Brony Liaison',
        'description': '',
        'discord_id': '166282912071811074',
        'discord_username': 'phillypossum',
    },
    {
        'username': 'x1BitJay',
        'display_name_override': '1BitJay',
        'pronouns': 'they/them',
        'role': 'Staff',
        'discord_username': '1bitjay',
        'discord_id': '194335417787613186',
        'bluesky_handle': '1bitjay.bsky.social',
        'description': '1BitJay is a con-goer and staffer for several conventions. They started helping with Biggest Little Fur Con in 2023 and have since taken on the role assisting with Indy Fur Con and FurSquared. They have been active in the furry fandom since 2016. They are also active in the warrior cat community having participated in a few archiving projects and several moderation teams. Currently an IT technician by day, bat-wolf by night, you can find them around their computers, pets and local library!',
    },
    {
        'username': 'ash_1595',
        'display_name_override': 'Ash',
        'pronouns': 'she/her/hers',
        'role': 'Assistant to the Chairgoat',
        'description': 'I’m Ashley (Ash), and I’m Tab’s assistant and partner :3 I’ve been in the fandom since around 2020. My first fur con was Anthro New England 2024, and I’ve been to a few more since then.',
        'discord_id': '349611069255319552',
        'discord_username': '____ash____',
    },
    {
        'username': 'BrambleWah',
        'display_name_override': 'Bramble',
        'pronouns': 'he/him',
        'role': 'Web Developer',
        'description': 'An australian red panda who likes to dabble in all things tech. Professionally a software engineer, and likes to help out at cons and events all around the world.',
        'discord_id': '173827026493505543',
        'discord_username': 'bramble.wah',
        'bluesky_handle': 'bramble.red',
        'twitter_username': 'BrambleWah',
    },
    {
        'username': 'kittrel',
        'display_name_override': 'Kittrel',
        'pronouns': 'she/they',
        'role': 'Staff',
        'discord_username': 'kittrel',
        'discord_id': '595717487254044683',
        'description': 'Kittrel is a freelance designer and illustrator who created Megaplex conbooks from 2014-2019. Their fursona is a coyote and they have been involved in the furry fandom for more of their life than not! Their first convention was Anthrocon in 1999. Kitt is excited to help preserve the art and stories that conventions of the past have to tell.',
    },
    {
        'username': 'zielvos',
        'display_name_override': 'Caffeinated Crux',
        'role': 'Staff',
        'description': 'Zielvos (AKA Caffeinated Crux) has been in the fandom since 2005. He has done his best to document things from the conventions he attends such as artwork and swag ,while also recording some events. After a short Hiatus from the fandom from 2011 to 2014, he attended FurSquared and learned to love volunteering and eventually became Staff as the head of Go-Team. Today, he helps out in Registration. When he isn’t working at F2, you can find him at other conventions around the Midwest assisting artists in Dealers Den with set up, selling, and packing up their booths.',
        'pronouns': 'any pronouns',
    },
    {
        'username': 'Echoenbatbat',
        'display_name_override': 'Echoen',
        'role': 'Staff',
        'description': "Charity Lead, staffer, and fursultant for multiple furry conventions. Always has hir ears perked for opportunities to do the most good. Specializes in growing departments and transforming teams, shi is the bat you call for when you need help. A corrupting force for good especially in the nonprofit sector or adult communities",
        'pronouns': 'shi/hir',
        'bluesky_handle': 'echoen.bsky.social',
        'discord_username': 'Echoenbatbat',
        'discord_id': '161687460051419137',
    },
]


def serialize_staff_member(member):
    username = member.get('username') or ''
    discord_id = member.get('discord_id')
    return {
        'username': username,
        'display_name': member.get('display_name_override') or username,
        'role': member.get('role') or '',
        'pronouns': member.get('pronouns') or '',
        'description': member.get('description') or '',
        'telegram_username': username,
        'telegram_url': f'https://t.me/{username}' if username else None,
        'bluesky_handle': member.get('bluesky_handle') or '',
        'bluesky_url': (
            f"https://bsky.app/profile/{member['bluesky_handle']}"
            if member.get('bluesky_handle') else None
        ),
        'twitter_username': member.get('twitter_username') or '',
        'discord_username': member.get('discord_username') or '',
        'discord_id': str(discord_id) if discord_id else '',
    }


def page_catalog_entry(slug):
    for page in PAGE_CATALOG:
        if page['slug'] == slug:
            return dict(page)
    return None
