/**
 * Telegram Chat Widget
 * Displays chat history from Telegram channel in a floating widget
 */

const TELEGRAM_MESSAGES_PROXY = '/internal/api/telegram-messages';

function normalizeTelegramApiUrl(url) {
  const fallback = TELEGRAM_MESSAGES_PROXY;
  if (!url) {
    return fallback;
  }

  const value = String(url).trim();
  if (value.includes('tg.tabs.gay') && value.includes('messages')) {
    return fallback;
  }

  try {
    const parsed = new URL(value, window.location.origin);
    if (parsed.hostname === 'tg.tabs.gay' && parsed.pathname.includes('messages')) {
      return fallback;
    }
    if (parsed.origin === window.location.origin) {
      return parsed.pathname;
    }
  } catch (error) {
    // fall through
  }

  return value.startsWith('/') ? value.split('?')[0] : fallback;
}

class TelegramChatWidget {
  constructor(options = {}) {
    // Merge constructor options with any global config set by template include
    const globalCfg = (typeof window !== 'undefined' && window.telegramWidgetConfig) ? window.telegramWidgetConfig : {};
    this.apiUrl = normalizeTelegramApiUrl(options.apiUrl || globalCfg.apiUrl || TELEGRAM_MESSAGES_PROXY);
    // create lightbox container element once
    this.lightbox = null;
    this.createLightbox();
    this.limit = options.limit || globalCfg.limit || 100;  // Set to 100 to get more messages from the API
    this.currentPage = 1;
    this.messages = [];
    this.users = []; // Store users array for username lookups
    this.isOpen = false;
    this.loading = false;
    this.cacheKey = 'tg_widget_cache';
    this.cacheExpiry = 10 * 60 * 1000; // 10 minutes cache
    this.messagesLoaded = false;
    this.scrollTimeout = null;
    this.hasMoreMessages = true;
    
    this.init();
  }

  init() {
    console.log('Initializing Telegram Widget...');
    this.createDOM();
    this.attachEventListeners();
  }

  createLightbox() {
    // lightbox overlay appended to body once
    this.lightbox = document.createElement('div');
    this.lightbox.id = 'tg-photo-lightbox';
    this.lightbox.style.cssText = `
      position: fixed;
      top:0;left:0;right:0;bottom:0;
      background: rgba(0,0,0,0.8);
      display:none;
      align-items:center;
      justify-content:center;
      z-index:1000000;
      cursor:pointer;
    `;
    const img = document.createElement('img');
    img.id = 'tg-photo-lightbox-img';
    img.style.maxWidth = '90%';
    img.style.maxHeight = '90%';
    img.style.boxShadow = '0 0 30px rgba(0,0,0,0.5)';
    this.lightbox.appendChild(img);

    // add explicit close button so users can tap the X instead of anywhere
    const closeBtn = document.createElement('button');
    closeBtn.id = 'tg-photo-lightbox-close';
    closeBtn.innerHTML = '&times;';
    closeBtn.style.cssText = `
      position: absolute;
      top: 20px;
      right: 20px;
      font-size: 32px;
      color: white;
      background: none;
      border: none;
      cursor: pointer;
      z-index: 1000001;
      padding: 0;
      line-height: 1;
    `;
    // clicking the button should not bubble to the overlay
    closeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.closeLightbox();
    });
    this.lightbox.appendChild(closeBtn);

    document.body.appendChild(this.lightbox);
    this.lightbox.addEventListener('click', () => {
      this.closeLightbox();
    });
  }

  openLightbox(url) {
    if (!this.lightbox) return;
    const img = document.getElementById('tg-photo-lightbox-img');
    img.src = url;
    this.lightbox.style.display = 'flex';
  }

  closeLightbox() {
    if (!this.lightbox) return;
    this.lightbox.style.display = 'none';
    const img = document.getElementById('tg-photo-lightbox-img');
    img.src = '';
  }

  createDOM() {
    const container = document.createElement('div');
    container.className = 'tg-widget-container';
    container.id = 'tg-widget-container';
    
    container.innerHTML = `
      <div class="tg-widget-window" id="tg-widget-window">
        <div class="tg-widget-header">
          <div>
            <h3>Furry Con Archives [Chat]</h3>
            <div class="tg-widget-header-subtitle"><a href="https://t.me/furryconarchives" target="_blank" rel="noopener noreferrer">t.me/furryconarchives</a></div>
          </div>
          <button class="tg-widget-close" id="tg-widget-close">&times;</button>
        </div>
        <div class="tg-widget-messages" id="tg-widget-messages"></div>
        <div class="tg-widget-pagination" id="tg-widget-pagination">
          <button id="tg-prev-page" disabled>← Older</button>
          <span id="tg-page-info">Page 1</span>
          <button id="tg-next-page">Newer →</button>
        </div>
      </div>
      <button class="tg-widget-button" id="tg-widget-button" title="Open Telegram Chat">
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.02-.58.05-1.02-.38-1.58-.75-.88-.59-1.38-.95-2.23-1.52-.98-.66-.34-1.02.22-1.63.15-.16 2.75-2.52 2.8-2.74.01-.12.02-.56-.53-.84-.56-.29-.87-.24-1.23-.15-.25.06-1.45.73-4.1 2.72-.39.26-.74.39-1.08.38-.35-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.34.49-.68 1.3-.92 5.05-1.63 5.95-1.91 6.62-1.93.3 0 .95.07 1.38.45.38.33.6.8.65 1.36z"/>
        </svg>
      </button>
    `;
    
    document.body.appendChild(container);
  }

  attachEventListeners() {
    const button = document.getElementById('tg-widget-button');
    const closeBtn = document.getElementById('tg-widget-close');
    const window = document.getElementById('tg-widget-window');
    const messagesContainer = document.getElementById('tg-widget-messages');

    if (!button || !closeBtn || !window || !messagesContainer) {
      console.error('Widget elements not found', {
        button, closeBtn, window, messagesContainer
      });
      return;
    }

    button.addEventListener('click', () => this.toggle());
    closeBtn.addEventListener('click', () => this.close());
    
    // Add scroll listener with throttling
    messagesContainer.addEventListener('scroll', (e) => {
      if (this.scrollTimeout) {
        clearTimeout(this.scrollTimeout);
      }
      this.scrollTimeout = setTimeout(() => this.handleScroll(e), 100);
    });
  }

  toggle() {
    if (this.isOpen) {
      this.close();
    } else {
      this.open();
    }
  }

  open() {
    const window = document.getElementById('tg-widget-window');
    const button = document.getElementById('tg-widget-button');
    window.classList.add('active');
    button.style.display = 'none';
    this.isOpen = true;
    if (!this.messagesLoaded) {
      this.loadMessages();
    }
    this.startPollingForNewMessages();
  }

  close() {
    const window = document.getElementById('tg-widget-window');
    const button = document.getElementById('tg-widget-button');
    window.classList.remove('active');
    button.style.display = 'flex';
    this.isOpen = false;
    this.stopPollingForNewMessages();
  }

  async loadMessages(page = 1, append = false) {
    this.loading = true;
    this.currentPage = page;
    const container = document.getElementById('tg-widget-messages');
    console.log('loadMessages called with:', { page, apiUrl: this.apiUrl, append });
    if (!append && container.innerHTML === '') {
      container.innerHTML = '<div class="tg-widget-loading">Loading messages</div>';
    }
    try {
      // Build fetch URL while preserving any existing query params on the base API URL.
      const fetchUrl = new URL(this.apiUrl, window.location.origin);
      fetchUrl.searchParams.set('limit', String(this.limit));
      fetchUrl.searchParams.delete('peer');
      
      // For appending (loading older messages), use the ID of the oldest message
      if (append && this.messages.length > 0) {
        const oldestMessage = this.messages[this.messages.length - 1];
        // max_id returns messages with ID < max_id, so subtract 1 to avoid overlap
        const maxId = oldestMessage.id - 1;
        fetchUrl.searchParams.set('max_id', String(maxId));
        console.log('=== LOADING OLDER - Using max_id ===');
        console.log('Oldest current message ID:', oldestMessage.id);
        console.log('Requesting max_id:', maxId, '(will get messages with ID < this)');
        console.log('Fetch URL:', fetchUrl.toString());
      } else {
        // For initial load, use page parameter for consistency
        fetchUrl.searchParams.set('page', String(page));
        console.log('=== INITIAL LOAD ===');
        console.log('Fetch URL:', fetchUrl.toString());
      }
      
      let response = await fetch(fetchUrl.toString());
      console.log('Response status:', response.status, 'URL:', fetchUrl.toString());
      // If we got a 404, try toggling a trailing slash as a fallback
      if (response.status === 404) {
        const triedAltKey = '_triedAlt';
        if (!window[triedAltKey]) {
          window[triedAltKey] = true;
          const currentUrl = fetchUrl.toString();
          const altUrl = currentUrl.endsWith('/') ? currentUrl.slice(0, -1) : currentUrl + '/';
          console.log('Got 404, retrying with alternate URL:', altUrl);
          response = await fetch(altUrl);
          console.log('Retry response status:', response.status, 'URL:', altUrl);
        }
      }

      if (!response.ok) {
        throw new Error(`API returned ${response.status}`);
      }
      const data = await response.json();
      const newMessages = data.messages || [];
      
      console.log('=== API RESPONSE ===');
      console.log('Append mode:', append);
      console.log('Messages received:', newMessages.length);
      if (newMessages.length > 0) {
        console.log('First message ID:', newMessages[0].id);
        console.log('Last message ID:', newMessages[newMessages.length - 1].id);
        console.log('Full message IDs:', newMessages.map(m => m.id).join(', '));
      }
      
      // Store the users array from API response for username lookups
      if (data.users) {
        this.users = data.users;
        console.log('Users stored:', this.users.length);
      }
      if (append) {
        // Stop loading if no new messages
        if (newMessages.length === 0) {
          console.log('❌ No new messages received. Reached end.');
          this.hasMoreMessages = false;
          this.loading = false;
          return;
        }
        
        // Check for duplicates - new batch should contain messages older (lower ID) than what we have
        if (this.messages.length > 0 && newMessages.length > 0) {
          const newestNewMessage = newMessages[0].id; // Newest message in new batch
          const oldestExistingMessage = this.messages[this.messages.length - 1].id; // Oldest in existing
          
          console.log('🔍 Duplicate check');
          console.log('  Newest in new batch:', newestNewMessage);
          console.log('  Oldest in existing:', oldestExistingMessage);
          console.log('  Valid?', newestNewMessage < oldestExistingMessage);
          
          // If the newest message in the new batch is >= our oldest, we have an overlap/duplicate
          if (newestNewMessage >= oldestExistingMessage) {
            console.log('❌ Reached end or duplicate detected. No more messages to load.');
            this.hasMoreMessages = false;
            this.loading = false;
            return;
          }
        }
        
        console.log('✓ APPENDING', newMessages.length, 'older messages');
        console.log('  Current total:', this.messages.length);
        console.log('  New total will be:', this.messages.length + newMessages.length);
        
        // Store scroll position to restore after render
        const scrollHeightBefore = this.getScrollHeight();
        // Append older messages to the END (they are older chronologically)
        this.messages = [...this.messages, ...newMessages];
        
        console.log('  Messages combined. Rendering...');
        
        // Render and maintain scroll position
        this.renderMessages(append, scrollHeightBefore, newMessages.length);
      } else {
        this.messages = newMessages;
        // The wrapper API at tg.tabs.gay doesn't support proper pagination
        // It only returns the newest messages. Set hasMoreMessages to false.
        this.hasMoreMessages = false;
        console.log('Loaded initial', newMessages.length, 'messages');
        console.log('NOTE: Telegram API wrapper only supports newest messages (no pagination)');
        this.renderMessages(append);
      }
      // Mark as loaded
      this.messagesLoaded = true;
      // Check for new messages periodically when window is open
      if (!append) {
        this.startPollingForNewMessages();
      }
    } catch (error) {
      console.error('Error loading messages:', error);
      if (!append) {
        container.innerHTML = '<div class="tg-widget-empty">Failed to load messages</div>';
      }
    } finally {
      this.loading = false;
    }
  }

  getCachedMessages() {
    try {
      const cached = localStorage.getItem(this.cacheKey);
      if (!cached) return null;
      
      const { data, timestamp } = JSON.parse(cached);
      
      // Check if cache expired
      if (Date.now() - timestamp > this.cacheExpiry) {
        localStorage.removeItem(this.cacheKey);
        return null;
      }
      
      return data;
    } catch (e) {
      return null;
    }
  }

  cacheMessages(data) {
    try {
      localStorage.setItem(this.cacheKey, JSON.stringify({
        data: data,
        timestamp: Date.now()
      }));
    } catch (e) {
      console.warn('Failed to cache messages:', e);
    }
  }

  startPollingForNewMessages() {
    // Poll for new messages every 10 seconds when widget is open
    if (this.pollInterval) clearInterval(this.pollInterval);
    
    this.pollInterval = setInterval(() => {
      if (this.isOpen && !this.loading) {
        this.checkForNewMessages();
      }
    }, 60000);
  }

  stopPollingForNewMessages() {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }

  async checkForNewMessages() {
    try {
      const url = new URL(this.apiUrl, window.location.origin);
      url.searchParams.set('limit', String(this.limit));
      url.searchParams.set('page', '1');
      url.searchParams.delete('peer');
      const response = await fetch(url.toString());
      const data = await response.json();
      const newMessages = data.messages || [];
      
      // Check if there are new messages
      if (newMessages.length > 0 && this.messages.length > 0) {
        const lastMessageId = this.messages[0].id;
        const hasNewMessages = newMessages.some(msg => msg.id === lastMessageId);
        
        if (!hasNewMessages && newMessages[0].id !== this.messages[0].id) {
          // New messages available, add them
          console.log('New messages available');
          this.messages = [...newMessages, ...this.messages];
          this.renderMessages(false);
          this.cacheMessages(data);
        }
      }
    } catch (e) {
      console.error('Error checking for new messages:', e);
    }
  }


  renderMessages(append = false, scrollHeightBefore = null, newMessagesCount = 0) {
    const container = document.getElementById('tg-widget-messages');
    
    if (!container) {
      console.error('Messages container not found');
      return;
    }
    
    if (!this.messages || this.messages.length === 0) {
      console.log('No messages to display');
      container.innerHTML = '<div class="tg-widget-empty">No messages</div>';
      return;
    }

    console.log('renderMessages called - append:', append, 'total messages:', this.messages.length);

    // Use requestAnimationFrame to prevent blocking the main thread
    requestAnimationFrame(() => {
      if (!append) {
        container.innerHTML = '';
      }
      
      // Create message elements, grouping consecutive media from same user
      const fragment = document.createDocumentFragment();
      let renderedCount = 0;
      
      // If appending, only render the newly added messages (at the end of the array)
      const messagesToRender = append && newMessagesCount > 0 
        ? this.messages.slice(-newMessagesCount)
        : this.messages;
      
      let i = 0;
      while (i < messagesToRender.length) {
        const msg = messagesToRender[i];
        
        // Check if this is a media-only message
        if (msg.media && !msg.message) {
          // Collect consecutive media messages from the same user (within 5 minutes)
          const mediaGroup = [msg];
          let j = i + 1;
          const timeWindow = 5 * 60; // 5 minutes in seconds
          
          while (j < messagesToRender.length) {
            const nextMsg = messagesToRender[j];
            // Group if: same user, media-only, and within time window
            const timeDiff = Math.abs(nextMsg.date - msg.date);
            if (nextMsg.from_id === msg.from_id && nextMsg.media && !nextMsg.message && timeDiff <= timeWindow) {
              mediaGroup.push(nextMsg);
              j++;
            } else {
              break;
            }
          }
          
          // Always create grouped media element for 2+ photos
          if (mediaGroup.length >= 2) {
            const groupEl = this.createMediaGroupElement(mediaGroup);
            if (groupEl) {
              fragment.appendChild(groupEl);
              renderedCount++;
            }
            i = j;
          } else {
            // Single media message - create a mini group container
            const groupEl = this.createMediaGroupElement(mediaGroup);
            if (groupEl) {
              fragment.appendChild(groupEl);
              renderedCount++;
            }
            i++;
          }
        } else {
          // Regular message
          const messageEl = this.createMessageElement(msg);
          if (messageEl) {
            fragment.appendChild(messageEl);
            renderedCount++;
          }
          i++;
        }
      }
      
      console.log('Created', renderedCount, 'message elements');
      
      if (append) {
        // Append new (older) messages to the bottom
        container.appendChild(fragment);
        console.log('Appended', renderedCount, 'older messages to bottom');
      } else {
        container.appendChild(fragment);
        // Scroll to bottom to show newest messages
        container.scrollTop = container.scrollHeight;
        console.log('Initial render - scrolled to bottom');
      }
    });
  }

  getScrollHeight() {
    const container = document.getElementById('tg-widget-messages');
    return container ? container.scrollHeight : 0;
  }

  createMessageElement(message) {
    // Check for different types of content
    const hasTextContent = message.message && message.message.trim();
    const hasReactions = message.reactions && message.reactions.length > 0;
    const hasAction = message.action; // Service messages like user joins
    const hasMedia = message.media; // Photos, videos, etc.
    
    // Skip only if there's absolutely no content of any kind
    if (!hasTextContent && !hasReactions && !hasAction && !hasMedia) {
      return null;
    }

    const div = document.createElement('div');
    div.className = 'tg-widget-message';
    
    // Add special styling for service messages
    if (hasAction) {
      div.classList.add('tg-message-service');
    }

    const date = new Date(message.date * 1000);
    const timeStr = date.toLocaleDateString('en-US', { 
      month: 'short',
      day: 'numeric',
      year: date.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined
    }) + ' at ' + date.toLocaleTimeString('en-US', { 
      hour: '2-digit', 
      minute: '2-digit'
    });

    // Create header with profile pic, username and timestamp
    const headerDiv = document.createElement('div');
    headerDiv.className = 'tg-message-header';
    
    // Add profile picture if peer_id exists
    if (message.from_id) {
      // Look up username from users array
      const user = this.users.find(u => u.id === message.from_id);
      const username = user?.username;
      
      const pfpImg = document.createElement('img');
      pfpImg.className = 'tg-message-pfp';
      pfpImg.alt = 'User avatar';
      pfpImg.style.backgroundColor = '#333';
      
      // Use server-cached avatar URL
      const avatarUrl = username ? `/internal/api/telegram-avatar/${username}` : `/internal/api/telegram-avatar/${message.from_id}`;
      pfpImg.src = avatarUrl;
      
      pfpImg.onerror = (e) => {
        console.error('Avatar failed to load:', avatarUrl, e);
        pfpImg.style.display = 'none';
      };
      
      pfpImg.onload = () => {
        console.log('Avatar loaded successfully:', avatarUrl);
      };
      
      headerDiv.appendChild(pfpImg);
    }
    
    const usernameSpan = document.createElement('span');
    usernameSpan.className = 'tg-message-username';
    const username = this.getUserDisplayName(message);
    usernameSpan.textContent = username;
    headerDiv.appendChild(usernameSpan);
    
    const timeSpan = document.createElement('span');
    timeSpan.className = 'tg-message-time';
    timeSpan.textContent = timeStr;
    headerDiv.appendChild(timeSpan);
    
    div.appendChild(headerDiv);

    // Handle service messages (user joins, etc.)
    if (hasAction) {
      const actionDiv = document.createElement('div');
      actionDiv.className = 'tg-message-action';
      actionDiv.textContent = this.formatAction(message.action, message);
      div.appendChild(actionDiv);
    }

    // Handle media (photos, videos, etc.)
    if (hasMedia && !hasTextContent && !hasAction) {
      const mediaDiv = document.createElement('div');
      mediaDiv.className = 'tg-message-media';
      
      if (message.media.photo) {
        // try preview API first; fallback to static data URL
        let url = this.getPreviewUrl(message);
        if (!url) {
          url = this.getPhotoUrl(message.media.photo);
        }
        if (url) {
          const img = document.createElement('img');
          img.src = url;
          img.alt = 'Photo';
          img.className = 'tg-message-photo';          img.style.cursor = 'zoom-in';
          img.addEventListener('click', (e) => {
            e.stopPropagation();
            this.openLightbox(url);
          });          mediaDiv.appendChild(img);
        } else {
          mediaDiv.innerHTML = '<em>Photo</em>';
        }
      } else if (message.media.document) {
        // display video/gif previews
        const doc = message.media.document;
        const mime = (doc.mime_type || '').toLowerCase();
        const isVideo = mime.startsWith('video/');
        const isGif = mime === 'image/gif' || (doc.attributes||[]).some(a=>a._==='documentAttributeAnimated');
        if (isVideo || isGif) {
          let thumbUrl = '';
          if (doc.thumbs && doc.thumbs.length) {
            const thumb = doc.thumbs[0];
            if (thumb.type === 'i' && thumb.bytes?.bytes) {
              thumbUrl = 'data:image/jpeg;base64,' + thumb.bytes.bytes;
            }
          }
          const linkUrl = `https://t.me/furryconarchives/${message.id}`;
          if (thumbUrl) {
            const wrapper = document.createElement('div');
            wrapper.style.position = 'relative';
            const img = document.createElement('img');
            img.src = thumbUrl;
            img.alt = isVideo ? 'Video' : 'GIF';
            img.className = 'tg-message-photo';
            img.style.cursor = 'pointer';
            img.addEventListener('click', () => {
              window.open(linkUrl, '_blank');
            });
            wrapper.appendChild(img);
            const play = document.createElement('div');
            play.className = 'tg-play-overlay';
            wrapper.appendChild(play);
            mediaDiv.appendChild(wrapper);
          } else {
            const a = document.createElement('a');
            a.href = linkUrl;
            a.target = '_blank';
            a.textContent = isVideo ? 'View video on Telegram' : 'View GIF on Telegram';
            mediaDiv.appendChild(a);
          }
        } else {
          mediaDiv.innerHTML = '<em>Document</em>';
        }
      } else if (message.media.video) {
        mediaDiv.innerHTML = '<em>Video</em>';
      } else {
        mediaDiv.innerHTML = '<em>Media</em>';
      }
      
      div.appendChild(mediaDiv);
    }

    // Create message content
    if (hasTextContent) {
      let content = this.parseMessageContent(message.message || '');

      const contentDiv = document.createElement('div');
      contentDiv.className = 'tg-message-content';
      contentDiv.innerHTML = content;
      div.appendChild(contentDiv);
    }

    // Show reactions if available
    if (hasReactions) {
      const reactionsDiv = document.createElement('div');
      reactionsDiv.className = 'tg-message-reactions';
      
      // Handle both array of reaction objects and reactions.results structure
      const reactionList = Array.isArray(message.reactions) 
        ? message.reactions 
        : (message.reactions?.results || []);
      
      reactionList.forEach((reaction) => {
        const reactionSpan = document.createElement('span');
        reactionSpan.className = 'tg-reaction';
        
        // Handle different reaction formats
        const emoticon = reaction.emoji || reaction.reaction?.emoticon || reaction.reaction || '👍';
        const reactionText = typeof emoticon === 'string' ? emoticon : '👍';
        
        reactionSpan.title = reactionText;
        reactionSpan.textContent = reactionText;
        reactionsDiv.appendChild(reactionSpan);
      });
      
      div.appendChild(reactionsDiv);
    }

    return div;
  }

  createMediaGroupElement(mediaMessages) {
    // Create a grouped media element for multiple photos from same user
    if (!mediaMessages || mediaMessages.length === 0) return null;

    const firstMsg = mediaMessages[0];
    const div = document.createElement('div');
    div.className = 'tg-widget-message tg-message-media-group';

    const date = new Date(firstMsg.date * 1000);
    const timeStr = date.toLocaleDateString('en-US', { 
      month: 'short',
      day: 'numeric',
      year: date.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined
    }) + ' at ' + date.toLocaleTimeString('en-US', { 
      hour: '2-digit', 
      minute: '2-digit'
    });

    // Create header with profile pic and username
    const headerDiv = document.createElement('div');
    headerDiv.className = 'tg-message-header';
    
    // Add profile picture
    if (firstMsg.from_id) {
      const user = this.users.find(u => u.id === firstMsg.from_id);
      const username = user?.username;
      
      const pfpImg = document.createElement('img');
      pfpImg.className = 'tg-message-pfp';
      pfpImg.alt = 'User avatar';
      pfpImg.style.backgroundColor = '#333';
      
      const avatarUrl = username ? `/internal/api/telegram-avatar/${username}` : `/internal/api/telegram-avatar/${firstMsg.from_id}`;
      pfpImg.src = avatarUrl;
      
      pfpImg.onerror = (e) => {
        console.error('Avatar failed to load:', avatarUrl, e);
        pfpImg.style.display = 'none';
      };
      
      headerDiv.appendChild(pfpImg);
    }
    
    const usernameSpan = document.createElement('span');
    usernameSpan.className = 'tg-message-username';
    const username = this.getUserDisplayName(firstMsg);
    usernameSpan.textContent = username;
    headerDiv.appendChild(usernameSpan);
    
    const timeSpan = document.createElement('span');
    timeSpan.className = 'tg-message-time';
    timeSpan.textContent = timeStr;
    headerDiv.appendChild(timeSpan);
    
    div.appendChild(headerDiv);

    // Create gallery container for photos
    const galleryDiv = document.createElement('div');
    galleryDiv.className = 'tg-message-gallery';
    
    // Adjust grid layout based on count
    if (mediaMessages.length === 1) {
      galleryDiv.style.gridTemplateColumns = '1fr';
    } else if (mediaMessages.length === 2) {
      galleryDiv.style.gridTemplateColumns = 'repeat(2, 1fr)';
    } else {
      galleryDiv.style.gridTemplateColumns = 'repeat(2, 1fr)';
    }
    
    mediaMessages.forEach((msg, index) => {
      const photoDiv = document.createElement('div');
      photoDiv.className = 'tg-gallery-item';
      photoDiv.title = `Photo ${index + 1}`;
      // attempt to render thumbnail inside group
      // try preview api with parent message info if available
      let url = this.getPreviewUrl(msg);
      if (!url) {
        url = this.getPhotoUrl(msg.media?.photo);
      }
      if (url) {
        const img = document.createElement('img');
        img.src = url;
        img.alt = `Photo ${index + 1}`;
        img.style.maxWidth = '100%';
        img.style.borderRadius = '4px';
        img.style.cursor = 'zoom-in';
        img.addEventListener('click', (e) => {
          e.stopPropagation();
          this.openLightbox(url);
        });
        photoDiv.appendChild(img);
      }
      galleryDiv.appendChild(photoDiv);
    });
    
    div.appendChild(galleryDiv);
    
    // Add count indicator only for 2+ photos
    if (mediaMessages.length >= 2) {
      const countDiv = document.createElement('div');
      countDiv.className = 'tg-media-count';
      countDiv.textContent = `${mediaMessages.length} photos`;
      div.appendChild(countDiv);
    }

    return div;
  }

  getPhotoUrl(photo) {
    // Prefer using tg.tabs.gay media preview API if photo id is available
    if (photo && photo.id) {
      // return URL; the service may reject non-Amp requests, so we'll fall back below if it fails
      return `/internal/api/telegram-media-preview?photo_id=${photo.id}`;
    }
    // attempt to return a usable URL for a photo object from the API fallback
    if (!photo || !photo.sizes || !Array.isArray(photo.sizes)) {
      return '';
    }
    // look for stripped (thumbnail) size first
    for (const sz of photo.sizes) {
      if (sz.type === 'i' && sz.bytes && sz.bytes.bytes) {
        return 'data:image/jpeg;base64,' + sz.bytes.bytes;
      }
    }
    // fallback to any other base64-encoded size
    for (const sz of photo.sizes) {
      if (sz.bytes && sz.bytes.bytes) {
        return 'data:image/jpeg;base64,' + sz.bytes.bytes;
      }
    }
    return '';
  }

  // construct preview URL when message-specific info is available
  getPreviewUrl(message) {
    if (!message || !message.peer_id || !message.id) return '';
    // direct API path per user suggestion
    return `/internal/api/telegram-media-preview?peer=${message.peer_id}&id=${message.id}`;
  }

  formatAction(action, message) {
    // Handle different types of service messages
    if (!action || typeof action !== 'object') {
      return '';
    }

    const username = this.getUserDisplayNameFromMessage(message);
    
    // ChatAddUser / MessageActionChatAddUser
    if (action._ === 'MessageActionChatAddUser' || action.users) {
      return `${username} joined the group`;
    }
    
    // ChatDeleteUser / MessageActionChatDeleteUser
    if (action._ === 'MessageActionChatDeleteUser') {
      return `${username} left the group`;
    }
    
    // ChatEditTitle / MessageActionChatEditTitle
    if (action._ === 'MessageActionChatEditTitle' && action.title) {
      return `${username} changed the group name to "${action.title}"`;
    }
    
    // ChatEditPhoto / MessageActionChatEditPhoto
    if (action._ === 'MessageActionChatEditPhoto') {
      return `${username} changed the group photo`;
    }
    
    // ChatDeletePhoto / MessageActionChatDeletePhoto
    if (action._ === 'MessageActionChatDeletePhoto') {
      return `${username} removed the group photo`;
    }
    
    // PinMessage / MessageActionPinMessage
    if (action._ === 'MessageActionPinMessage') {
      return `${username} pinned a message`;
    }
    
    // ChatJoinedByLink / MessageActionChatJoinedByLink
    if (action._ === 'MessageActionChatJoinedByLink') {
      return `${username} joined via invite link`;
    }
    
    // ChatCreate / MessageActionChatCreate
    if (action._ === 'MessageActionChatCreate' && action.title) {
      return `${username} created the group "${action.title}"`;
    }
    
    // ChatCreate / MessageActionChatCreate
    if (action._ === 'MessageActionChatCreate' && action.title) {
      return `${username} created the group "${action.title}"`;
    }

    if (action._ === 'messageActionChatJoinedByRequest') {
      return `${username} joined the group`;
    }

    // Default fallback for unknown action types
    return `${username} performed an action`;
  }

  getUserDisplayNameFromMessage(message) {
    // First, try from_id (numeric user ID)
    if (message.from_id && typeof message.from_id === 'number') {
      const user = this.users.find(u => u.id === message.from_id);
      if (user) {
        const first = user.first_name || '';
        const last = user.last_name || '';
        const combined = `${first} ${last}`.trim();
        if (combined) return combined;
        if (user.username) return `@${user.username}`;
      }
    }
    
    // Try from object
    if (typeof message.from === 'object' && message.from) {
      const first = message.from.first_name || '';
      const last = message.from.last_name || '';
      const combined = `${first} ${last}`.trim();
      if (combined) return combined;
      if (message.from.username) return `@${message.from.username}`;
    }
    
    // Try from_name field
    if (message.from_name && String(message.from_name).trim()) {
      return String(message.from_name).trim();
    }
    
    // Try from field (string)
    if (message.from && typeof message.from === 'string' && String(message.from).trim()) {
      return String(message.from).trim();
    }
    
    // Default
    return 'User';
  }

  getUserDisplayName(message) {
    // First, try to look up user by from_id in the users array
    if (message.from_id && typeof message.from_id === 'number') {
      const user = this.users.find(u => u.id === message.from_id);
      if (user) {
        // Prioritize full name (first_name + last_name)
        const first = user.first_name || '';
        const last = user.last_name || '';
        const combined = `${first} ${last}`.trim();
        if (combined) return combined;
        
        // Fallback to username if no full name
        if (user.username) return `@${user.username}`;
      }
    }
    
    // Try from object with first_name and last_name
    if (typeof message.from === 'object' && message.from) {
      const first = message.from.first_name || '';
      const last = message.from.last_name || '';
      const combined = `${first} ${last}`.trim();
      if (combined) return combined;
      if (message.from.username) return `@${message.from.username}`;
    }
    
    // Try from_id object with first_name and last_name
    if (message.from_id && typeof message.from_id === 'object') {
      const first = message.from_id.first_name || '';
      const last = message.from_id.last_name || '';
      const combined = `${first} ${last}`.trim();
      if (combined) return combined;
      if (message.from_id.username) return `@${message.from_id.username}`;
    }
    
    // Check for username in from object
    if (typeof message.from === 'object' && message.from?.username) {
      return `@${message.from.username}`;
    }
    
    // Check for username in from_id object
    if (message.from_id && typeof message.from_id === 'object' && message.from_id?.username) {
      return `@${message.from_id.username}`;
    }
    
    // Try from_name (common in Telegram API responses)
    if (message.from_name && String(message.from_name).trim()) {
      return String(message.from_name).trim();
    }
    
    // Try top-level from field (string)
    if (message.from && String(message.from).trim()) {
      return String(message.from).trim();
    }
    
    // Try actor field (sometimes used in Telegram API)
    if (message.actor && String(message.actor).trim()) {
      return String(message.actor).trim();
    }
    
    // Try sender_id with title
    if (message.sender_id?.title) {
      return String(message.sender_id.title).trim();
    }
    
    // Default fallback
    return 'Telegram User';
  }

  escapeHtml(text) {
    const map = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
  }

  parseMessageContent(text) {
    if (!text) return '';
    
    let content = text;
    
    // First, check if the text contains actual HTML tags
    const hasHtmlTags = /<[^>]+>/g.test(content);
    
    if (hasHtmlTags) {
      // If it contains HTML, parse it carefully without escaping first
      
      // Handle <a> tags with various formats
      content = content.replace(/<a\s+href=["']([^"']+)["'][^>]*>([^<]*)<\/a>/gi, 
        '<a href="$1" target="_blank" rel="noopener">$2</a>');
      // Handle unclosed or incomplete <a> tags
      content = content.replace(/<a\s+href=["']([^"']+)["'][^>]*>([^<]+)$/gi, 
        '<a href="$1" target="_blank" rel="noopener">$2</a>');
      
      // Handle <br> tags
      content = content.replace(/<br\s*\/?>/gi, '<br>');
      
      // Handle <b> and <strong> tags
      content = content.replace(/<b>([^<]*)<\/b>/gi, '<strong>$1</strong>');
      content = content.replace(/<strong>([^<]*)<\/strong>/gi, '<strong>$1</strong>');
      
      // Handle <i> and <em> tags
      content = content.replace(/<i>([^<]*)<\/i>/gi, '<em>$1</em>');
      content = content.replace(/<em>([^<]*)<\/em>/gi, '<em>$1</em>');
      
      // Handle Telegram-specific tags
      content = content.replace(/<tg-emoji[^>]*>([^<]*)<\/tg-emoji>/gi, '$1');
      
      // Remove any other unhandled HTML tags for safety
      content = content.replace(/<[^>]+>/g, '');
    } else {
      // If no HTML tags, escape HTML entities and then parse encoded HTML
      content = this.escapeHtml(content);
      
      // Handle encoded HTML tags
      // <a> tags
      content = content.replace(/&lt;a\s+href=["']?([^"'&]+)["']?[^&]*?&gt;([^&]*?)&lt;\/a&gt;/gi, 
        '<a href="$1" target="_blank" rel="noopener">$2</a>');
      content = content.replace(/&lt;a\s+href=["']?([^"'&]+)["']?[^&]*?&gt;([^&]+)$/gi, 
        '<a href="$1" target="_blank" rel="noopener">$2</a>');
      
      // <br> tags
      content = content.replace(/&lt;br\s*\/?&gt;/gi, '<br>');
      
      // <b> tags
      content = content.replace(/&lt;b&gt;(.*?)&lt;\/b&gt;/gi, '<strong>$1</strong>');
      content = content.replace(/&lt;strong&gt;(.*?)&lt;\/strong&gt;/gi, '<strong>$1</strong>');
      
      // <i> tags
      content = content.replace(/&lt;i&gt;(.*?)&lt;\/i&gt;/gi, '<em>$1</em>');
      content = content.replace(/&lt;em&gt;(.*?)&lt;\/em&gt;/gi, '<em>$1</em>');
      
      // Telegram emoji tags
      content = content.replace(/&lt;tg-emoji[^&]*?&gt;([^&]*?)&lt;\/tg-emoji&gt;/gi, '$1');
    }
    
    return content;
  }

  handleScroll() {
    const container = document.getElementById('tg-widget-messages');
    if (!container) return;
    
    // Check if scrolled near the top (within 50px)
    const isNearTop = container.scrollTop < 50;
    
    console.log('=== SCROLL EVENT (always log) ===');
    console.log('Scroll position:', container.scrollTop, 'isNearTop:', isNearTop);
    console.log('Loading state:', this.loading, 'hasMoreMessages:', this.hasMoreMessages);
    console.log('Messages count:', this.messages.length);
    
    if (isNearTop && !this.loading && this.hasMoreMessages) {
      console.log('✓ ALL CONDITIONS MET - LOADING OLDER MESSAGES');
      console.log('Current messages:', this.messages.length);
      console.log('Oldest message ID:', this.messages[this.messages.length - 1]?.id);
      this.loadMessages(1, true);  // Don't increment page, let max_id handle pagination
    } else if (isNearTop) {
      if (this.loading) console.log('✗ BLOCKED: currently loading');
      if (!this.hasMoreMessages) console.log('✗ BLOCKED: no more messages');
    }
  }
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM Ready - Creating Widget');
    window.telegramWidget = new TelegramChatWidget();
  });
} else {
  console.log('Document already loaded - Creating Widget');
  window.telegramWidget = new TelegramChatWidget();
}
