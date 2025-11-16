"""Fetch Telegram chat history and serialize it for the public API."""

from datetime import datetime, timezone

import requests
from django.conf import settings
from django.core.cache import cache

from archive.api.serializers import absolute_url

TELEGRAM_HISTORY_URL = 'https://tg.tabs.gay/api/messages.getHistory'
CACHE_TTL_SECONDS = 60
MAX_LIMIT = 432


def _telegram_peer():
    return (getattr(settings, 'TELEGRAM_PEER', None) or '').strip().lstrip('@')


def _telegram_channel_url():
    peer = _telegram_peer()
    return f'https://t.me/{peer}' if peer else ''


def fetch_telegram_history(limit=100, page=1, max_id=None):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    limit = 100 if limit < 1 or limit > MAX_LIMIT else limit
    page = 1 if page < 1 else page

    params = {'peer': _telegram_peer(), 'limit': limit}
    if max_id not in (None, ''):
        try:
            params['max_id'] = int(max_id)
        except (TypeError, ValueError):
            params['offset'] = (page - 1) * limit
    else:
        params['offset'] = (page - 1) * limit

    cache_key = f'tg_history:{_telegram_peer()}:{params.get("limit")}:{params.get("offset", 0)}:{params.get("max_id", "")}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, limit, page

    response = requests.get(TELEGRAM_HISTORY_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get('success') and data.get('response'):
        data = data['response']
    cache.set(cache_key, data, CACHE_TTL_SECONDS)
    return data, limit, page


def serialize_telegram_history(payload, request, *, limit=100, page=1):
    users = {_user_id(user): user for user in (payload.get('users') or []) if _user_id(user)}
    chats = {_chat_id(chat): chat for chat in (payload.get('chats') or []) if _chat_id(chat)}
    channel = next(iter(chats.values()), None) or {}

    results = []
    for raw in payload.get('messages') or []:
        parsed = serialize_telegram_message(raw, users, request)
        if parsed:
            results.append(parsed)

    return {
        'channel': {
            'name': channel.get('title') or 'Furry Con Archives [Chat]',
            'username': channel.get('username') or _telegram_peer(),
            'url': _telegram_channel_url(),
        },
        'count': payload.get('count') if payload.get('count') is not None else len(results),
        'page': page,
        'limit': limit,
        'results': results,
    }


def serialize_telegram_message(message, users, request):
    if not isinstance(message, dict):
        return None

    user_id = _from_user_id(message.get('from_id'))
    user = users.get(user_id) if user_id else None
    username = _username(user, message)
    name = _display_name(user, message)
    text = (message.get('message') or '').strip()
    action_text = _format_action(message.get('action'), name)
    if not text and action_text:
        text = action_text
    media_url = _media_url(message, request)
    if not text and not media_url:
        return None

    avatar_key = username or (str(user_id) if user_id else '')
    pfp = absolute_url(request, f'/internal/api/telegram-avatar/{avatar_key}') if avatar_key else None

    date = _iso_date(message.get('date'))
    return {
        'id': message.get('id'),
        'date': date,
        'name': name,
        'username': username or '',
        'pfp': pfp,
        'message': text,
        'type': 'service' if message.get('action') else 'message',
        'media_url': media_url,
    }


def _user_id(user):
    if not isinstance(user, dict):
        return None
    return user.get('id')


def _chat_id(chat):
    if not isinstance(chat, dict):
        return None
    return chat.get('id')


def _from_user_id(value):
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        return value.get('user_id') or value.get('id')
    return None


def _username(user, message):
    if isinstance(user, dict) and user.get('username'):
        return str(user['username']).lstrip('@')
    for source in (message.get('from'), message.get('from_id')):
        if isinstance(source, dict) and source.get('username'):
            return str(source['username']).lstrip('@')
    return ''


def _display_name(user, message):
    if isinstance(user, dict):
        combined = ' '.join(part for part in (user.get('first_name'), user.get('last_name')) if part).strip()
        if combined:
            return combined
        if user.get('username'):
            return f'@{user["username"]}'

    for source in (message.get('from'), message.get('from_id')):
        if isinstance(source, dict):
            combined = ' '.join(
                part for part in (source.get('first_name'), source.get('last_name')) if part
            ).strip()
            if combined:
                return combined
            if source.get('username'):
                return f'@{source["username"]}'
        elif isinstance(source, str) and source.strip():
            return source.strip()

    if message.get('from_name'):
        return str(message['from_name']).strip()
    return 'Telegram User'


def _iso_date(value):
    if value in (None, ''):
        return None
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _media_url(message, request):
    media = message.get('media') or {}
    if not isinstance(media, dict) or not media:
        return None
    photo = media.get('photo') or {}
    photo_id = photo.get('id') if isinstance(photo, dict) else None
    if photo_id:
        return absolute_url(request, f'/internal/api/telegram-media-preview?photo_id={photo_id}')
    peer_id = message.get('peer_id')
    if isinstance(peer_id, dict):
        peer_id = peer_id.get('channel_id') or peer_id.get('chat_id') or peer_id.get('user_id')
    if peer_id and message.get('id'):
        return absolute_url(
            request,
            f'/internal/api/telegram-media-preview?peer={peer_id}&id={message["id"]}',
        )
    return None


def _format_action(action, name):
    if not isinstance(action, dict):
        return ''
    kind = action.get('_') or ''
    if kind in ('MessageActionChatAddUser', 'messageActionChatAddUser') or action.get('users'):
        return f'{name} joined the group'
    if kind in ('MessageActionChatDeleteUser', 'messageActionChatDeleteUser'):
        return f'{name} left the group'
    if kind in ('MessageActionChatEditTitle', 'messageActionChatEditTitle') and action.get('title'):
        return f'{name} changed the group name to "{action["title"]}"'
    if kind in ('MessageActionChatEditPhoto', 'messageActionChatEditPhoto'):
        return f'{name} changed the group photo'
    if kind in ('MessageActionChatDeletePhoto', 'messageActionChatDeletePhoto'):
        return f'{name} removed the group photo'
    if kind in ('MessageActionPinMessage', 'messageActionPinMessage'):
        return f'{name} pinned a message'
    if kind in (
        'messageActionChatJoinedByLink',
        'MessageActionChatJoinedByLink',
        'messageActionChatJoinedByRequest',
        'MessageActionChatJoinedByRequest',
    ):
        return f'{name} joined the group'
    if kind in ('messageActionChatCreate', 'MessageActionChatCreate') and action.get('title'):
        return f'{name} created the group "{action["title"]}"'
    if kind:
        return f'{name} performed an action'
    return ''
