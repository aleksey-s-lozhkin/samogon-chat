const {
    isAuthenticated,
    roomSlug,
    username: currentUsername,
    canModerateMessages,
    attachmentUploadTemplate,
    audioMessageUrl,
    messageDeleteTemplate,
    messageReportTemplate,
    noteCreateUrl,
    focusMessageId,
} = chatConfig;
const MESSAGE_MAX_LENGTH = 1000;
const BARTENDER_USERNAME = "Семён";
const MESSAGE_SOUND_STORAGE_KEY = "samogon-message-sound-enabled";
const REACTION_EMOJI = ["👍", "👎", "❤️", "😂", "🔥", "😮", "😢", "🤔", "🤝", "🎉"];
const TYPING_DEBOUNCE_MS = 250;
const TYPING_IDLE_MS = 1600;
const TYPING_TTL_MS = 3500;
const AUDIO_MAX_DURATION_MS = 180000;

let chatSocket = null;
let reconnectTimer = null;
let reconnectAttempts = 0;
let socketWasConnected = false;
let hiddenAt = null;
let directRecipient = null;
let bartenderMode = false;
let bartenderPrivate = false;
let noteMode = false;
let loadingHistory = false;
let lastMessageDay = null;
let presenceUsers = [];
let presenceOnlineUsers = [];
let selectedAttachments = [];
let pendingAttachmentUpload = null;
let selectedMessageElement = null;
let replyTarget = null;
let pendingDeletionMessageId = null;
let pendingReportMessageId = null;
let bartenderTyping = false;
let typingDebounceTimer = null;
let typingIdleTimer = null;
let typingActive = false;
let typingRecipient = null;
let messageSoundContext = null;
let presenceHeartbeatTimer = null;
let audioRecorder = null;
let audioStream = null;
let audioChunks = [];
let audioStartedAt = 0;
let audioTimer = null;
let audioStopTimer = null;
let recordedAudio = null;
let recordedAudioDurationMs = 0;
let cancelAudioOnStop = false;
let sendAudioOnStop = false;
let audioPointerId = null;
let audioPointerStartX = 0;
let audioGestureCanceled = false;
const typingUsers = new Map();
const USE_VISUAL_VIEWPORT_HEIGHT = /Android/i.test(navigator.userAgent);
const IS_IPHONE = /iPhone|iPod/i.test(navigator.userAgent);
const SOCKET_RECONNECT_MAX_DELAY_MS = 30000;
const PRESENCE_HEARTBEAT_INTERVAL_MS = 25000;
const SOCKET_FATAL_CLOSE_CODES = new Set([4401, 4403, 4404]);

const FALLBACK_TAGLINES = [
    "Семён протирает стакан и слушает логи.",
    "Здесь баги разбирают по душам.",
    "Заходите с вопросом, выходите с планом.",
    "Связь есть. Наливаю первую тему.",
    "У стойки спорят о табах и мирятся на пробелах.",
];
const TAGLINES = chatConfig.atmosphereLines?.length
    ? chatConfig.atmosphereLines
    : FALLBACK_TAGLINES;
const COMPOSER_HINTS = window.SAMOGON_COMPOSER_HINTS || ["Ваша реплика…"];
let taglineIndex = 0;
let composerHintIndex = 0;

if (isAuthenticated) {
    connectWebSocket();
}

updateAppHeight();
initializeViewportDiagnostics();
window.visualViewport?.addEventListener("resize", updateAppHeight);
window.visualViewport?.addEventListener("scroll", updateAppHeight);
window.addEventListener("resize", updateAppHeight);
window.addEventListener("orientationchange", updateAppHeight);
window.addEventListener("online", reconnectWebSocketNow);
window.addEventListener("pageshow", () => {
    updateAppHeight();
    ensureWebSocketConnection();
});
document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
        hiddenAt = Date.now();
        return;
    }

    updateAppHeight();
    const wasSuspended = hiddenAt !== null && Date.now() - hiddenAt > 2000;
    hiddenAt = null;
    if (wasSuspended || !chatSocket || chatSocket.readyState > WebSocket.OPEN) {
        reconnectWebSocketNow();
    }
});

const presenceStatusSelect = document.getElementById("presence-status-select");
presenceStatusSelect?.addEventListener("change", () => {
    if (!chatSocket || chatSocket.readyState !== WebSocket.OPEN) {
        showError("Нет связи с чатом. Попробуйте изменить статус ещё раз.");
        return;
    }
    chatSocket.send(JSON.stringify({
        type: "presence_status",
        status: presenceStatusSelect.value,
    }));
});

function connectWebSocket() {
    if (!isAuthenticated || document.hidden) {
        return;
    }
    if (
        chatSocket
        && (chatSocket.readyState === WebSocket.OPEN
            || chatSocket.readyState === WebSocket.CONNECTING)
    ) {
        return;
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const focusQuery = focusMessageId ? `?focus=${encodeURIComponent(focusMessageId)}` : "";
    const url = `${protocol}//${window.location.host}/ws/chat/${encodeURIComponent(roomSlug)}/${focusQuery}`;

    const socket = new WebSocket(url);
    chatSocket = socket;
    socket.onopen = () => {
        if (socket !== chatSocket) {
            return;
        }
        reconnectAttempts = 0;
        if (socketWasConnected) {
            showSuccess("Связь восстановлена.");
        }
        socketWasConnected = true;
        startPresenceHeartbeat(socket);
    };
    socket.onmessage = ({ data }) => {
        if (socket === chatSocket) {
            handleServerEvent(JSON.parse(data));
        }
    };
    socket.onclose = ({ code }) => {
        if (socket !== chatSocket) {
            return;
        }
        chatSocket = null;
        stopPresenceHeartbeat();
        stopTyping();
        if (SOCKET_FATAL_CLOSE_CODES.has(code)) {
            showError("Доступ к чату закрыт. Обновите страницу после входа.");
            return;
        }
        showConnectionLost();
        scheduleWebSocketReconnect();
    };
    socket.onerror = () => {
        if (socket === chatSocket) {
            socket.close();
        }
    };
}

function startPresenceHeartbeat(socket) {
    stopPresenceHeartbeat();
    presenceHeartbeatTimer = window.setInterval(() => {
        if (socket === chatSocket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({type: "presence_ping"}));
        }
    }, PRESENCE_HEARTBEAT_INTERVAL_MS);
}

function stopPresenceHeartbeat() {
    window.clearInterval(presenceHeartbeatTimer);
    presenceHeartbeatTimer = null;
}

function scheduleWebSocketReconnect() {
    if (reconnectTimer || document.hidden || !navigator.onLine) {
        return;
    }
    const delay = Math.min(
        1000 * (2 ** reconnectAttempts),
        SOCKET_RECONNECT_MAX_DELAY_MS,
    );
    reconnectAttempts += 1;
    reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        connectWebSocket();
    }, delay);
}

function reconnectWebSocketNow() {
    if (!isAuthenticated || document.hidden || !navigator.onLine) {
        return;
    }
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
    if (chatSocket) {
        const previousSocket = chatSocket;
        chatSocket = null;
        previousSocket.onclose = null;
        previousSocket.close();
    }
    connectWebSocket();
}

function ensureWebSocketConnection() {
    if (!chatSocket || chatSocket.readyState > WebSocket.OPEN) {
        reconnectWebSocketNow();
    }
}

function showConnectionLost() {
    const errorElement = document.getElementById("error-message");
    if (!errorElement) {
        return;
    }
    errorElement.classList.remove("is-success");
    errorElement.textContent = navigator.onLine
        ? "Связь потеряна. Переподключаемся…"
        : "Нет сети. Подключимся после её восстановления.";
}

function updateAppHeight() {
    let height = null;
    if (USE_VISUAL_VIEWPORT_HEIGHT) {
        height = window.visualViewport?.height || window.innerHeight;
    } else if (IS_IPHONE && isStandalonePwa()) {
        const portrait = window.matchMedia("(orientation: portrait)").matches;
        height = portrait
            ? Math.max(window.screen.width, window.screen.height)
            : Math.min(window.screen.width, window.screen.height);
    }

    if (!height) {
        document.documentElement.style.removeProperty("--app-height");
        return;
    }
    document.documentElement.style.setProperty("--app-height", `${Math.round(height)}px`);
}

function isStandalonePwa() {
    return window.navigator.standalone === true
        || window.matchMedia("(display-mode: standalone)").matches;
}

function initializeViewportDiagnostics() {
    let tapCount = 0;
    let resetTimer = null;
    document.querySelector(".brand h1")?.addEventListener("click", () => {
        tapCount += 1;
        window.clearTimeout(resetTimer);
        resetTimer = window.setTimeout(() => {
            tapCount = 0;
        }, 2500);
        if (tapCount >= 7) {
            tapCount = 0;
            openViewportDiagnostics();
        }
    });

    const params = new URLSearchParams(window.location.search);
    if (params.get("viewport_debug") === "1") {
        openViewportDiagnostics();
    }
}

function openViewportDiagnostics() {
    if (document.querySelector(".viewport-diagnostics")) {
        return;
    }

    const panel = document.createElement("aside");
    panel.className = "viewport-diagnostics";
    panel.setAttribute("aria-label", "Диагностика экрана");
    panel.innerHTML = `
        <div class="viewport-diagnostics__header">
            <strong>Viewport debug</strong>
            <div>
                <button type="button" data-viewport-copy>Копировать</button>
                <button type="button" data-viewport-close aria-label="Закрыть">×</button>
            </div>
        </div>
        <pre data-viewport-output></pre>
        <div class="viewport-safe-area-probe" aria-hidden="true"></div>
    `;
    document.body.append(panel);

    const output = panel.querySelector("[data-viewport-output]");
    const update = () => {
        if (!panel.isConnected) {
            return;
        }
        output.textContent = collectViewportDiagnostics(panel);
    };
    const scheduleUpdate = () => window.requestAnimationFrame(update);

    panel.querySelector("[data-viewport-close]")?.addEventListener("click", () => {
        panel.remove();
    });
    panel.querySelector("[data-viewport-copy]")?.addEventListener("click", async (event) => {
        try {
            await navigator.clipboard.writeText(output.textContent);
            event.currentTarget.textContent = "Скопировано";
        } catch (error) {
            output.focus();
            window.getSelection()?.selectAllChildren(output);
            event.currentTarget.textContent = "Выделено";
        }
    });

    update();
    window.addEventListener("resize", scheduleUpdate);
    window.addEventListener("orientationchange", scheduleUpdate);
    window.addEventListener("pageshow", scheduleUpdate);
    window.visualViewport?.addEventListener("resize", scheduleUpdate);
    window.visualViewport?.addEventListener("scroll", scheduleUpdate);
    document.addEventListener("visibilitychange", scheduleUpdate);
}

function collectViewportDiagnostics(panel) {
    const visualViewport = window.visualViewport;
    const rootStyles = getComputedStyle(document.documentElement);
    const probeStyles = getComputedStyle(
        panel.querySelector(".viewport-safe-area-probe"),
    );
    const orientation = window.matchMedia("(orientation: portrait)").matches
        ? "portrait"
        : "landscape";
    const rect = (selector) => {
        const element = document.querySelector(selector);
        if (!element) {
            return "missing";
        }
        const bounds = element.getBoundingClientRect();
        return ["top", "bottom", "height"]
            .map((key) => `${key}=${Math.round(bounds[key] * 10) / 10}`)
            .join(" ");
    };

    return [
        `time=${new Date().toISOString()}`,
        `platform=${navigator.platform || "unknown"}`,
        `orientation=${orientation}`,
        `standalone.navigator=${window.navigator.standalone === true}`,
        `standalone.media=${window.matchMedia("(display-mode: standalone)").matches}`,
        `window.inner=${window.innerWidth}x${window.innerHeight}`,
        `document.client=${document.documentElement.clientWidth}x${document.documentElement.clientHeight}`,
        `screen=${window.screen.width}x${window.screen.height}`,
        `devicePixelRatio=${window.devicePixelRatio}`,
        `visualViewport=${visualViewport
            ? `${visualViewport.width}x${visualViewport.height} offset=${visualViewport.offsetLeft},${visualViewport.offsetTop} scale=${visualViewport.scale}`
            : "unavailable"}`,
        `--app-height=${rootStyles.getPropertyValue("--app-height").trim() || "unset"}`,
        `safeArea=top:${probeStyles.paddingTop} right:${probeStyles.paddingRight} bottom:${probeStyles.paddingBottom} left:${probeStyles.paddingLeft}`,
        `.chat-page ${rect(".chat-page")}`,
        `.chat-header ${rect(".chat-page > .chat-header")}`,
        `.chat-layout ${rect(".chat-layout")}`,
        `.chat-main ${rect(".chat-main")}`,
        `.chat-log ${rect(".chat-log")}`,
        `.chat-composer ${rect(".chat-composer")}`,
    ].join("\n");
}

function handleServerEvent(data) {
    if (data.type === "history") {
        loadingHistory = true;
        lastMessageDay = null;
        clearHistorySkeleton();
        const chatLog = document.getElementById("chat-log");
        chatLog?.querySelectorAll(".message, .day-divider, .chat-empty-state")
            .forEach((element) => element.remove());
        data.messages.forEach(addMessage);
        loadingHistory = false;
        finishHistoryLoading();
    }

    if (data.type === "message") {
        playIncomingMessageSound(data);
        if (!data.room_slug || data.room_slug === roomSlug) {
            addMessage(data);
        } else {
            increaseUnreadCount(data);
        }
    }

    if (data.type === "attachments" && data.room_slug === roomSlug) {
        updateMessageAttachments(data.message_id, data.attachments);
    }

    if (data.type === "message_deleted" && data.room_slug === roomSlug) {
        removeMessage(data.message_id);
    }

    if (data.type === "reaction_update" && data.room_slug === roomSlug) {
        updateMessageReaction(data);
    }

    if (data.type === "typing_update" && data.room_slug === roomSlug) {
        updateTypingUser(data);
    }

    if (data.type === "user_presence") {
        updateUserPresence(data.users, data.online);
    }

    if (data.type === "error") {
        setBartenderTyping(false);
        showError(data.message);
    }
}

function isMessageSoundEnabled() {
    try {
        return window.localStorage.getItem(MESSAGE_SOUND_STORAGE_KEY) === "true";
    } catch (_error) {
        return false;
    }
}

function setMessageSoundEnabled(enabled) {
    try {
        window.localStorage.setItem(MESSAGE_SOUND_STORAGE_KEY, String(enabled));
    } catch (_error) {
        // Чат продолжает работать, даже если браузер запретил localStorage.
    }
    updateMessageSoundControl(enabled);
}

function updateMessageSoundControl(enabled = isMessageSoundEnabled()) {
    const toggle = document.getElementById("message-sound-toggle");
    const label = document.getElementById("message-sound-label");
    if (!toggle || !label) {
        return;
    }
    toggle.setAttribute("aria-pressed", String(enabled));
    label.textContent = enabled ? "Звук включён" : "Звук выключен";
}

function getMessageSoundContext() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
        return null;
    }
    messageSoundContext ||= new AudioContextClass();
    return messageSoundContext;
}

async function playMessageSound() {
    const context = getMessageSoundContext();
    if (!context) {
        return false;
    }
    if (context.state === "suspended") {
        await context.resume();
    }

    const startedAt = context.currentTime;
    const gain = context.createGain();
    gain.gain.setValueAtTime(0.0001, startedAt);
    gain.gain.exponentialRampToValueAtTime(0.075, startedAt + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, startedAt + 0.24);
    gain.connect(context.destination);

    [660, 880].forEach((frequency, index) => {
        const oscillator = context.createOscillator();
        const offset = index * 0.07;
        oscillator.type = "sine";
        oscillator.frequency.setValueAtTime(frequency, startedAt + offset);
        oscillator.connect(gain);
        oscillator.start(startedAt + offset);
        oscillator.stop(startedAt + offset + 0.15);
    });
    return true;
}

function playIncomingMessageSound(data) {
    if (
        !isMessageSoundEnabled()
        || document.visibilityState !== "visible"
        || normalizeUsername(data.username) === normalizeUsername(currentUsername)
    ) {
        return;
    }
    playMessageSound().catch(() => undefined);
}

function finishHistoryLoading() {
    const chatLog = document.getElementById("chat-log");
    if (!chatLog) {
        return;
    }

    if (!chatLog.querySelector(".message")) {
        renderEmptyState(chatLog);
        return;
    }
    if (focusMessageId) {
        window.requestAnimationFrame(() => {
            window.requestAnimationFrame(() => scrollToMessage(focusMessageId));
        });
        return;
    }
    chatLog.scrollTop = chatLog.scrollHeight;
}

function clearHistorySkeleton() {
    document.querySelector("#chat-log .chat-history-skeleton")?.remove();
}

function increaseUnreadCount(data) {
    const isIncoming = normalizeUsername(data.username) !== normalizeUsername(currentUsername);
    if (!isIncoming || !(data.private || data.room_private)) {
        return;
    }

    const roomLink = document.querySelector(
        `.room-navigation-item[data-room-slug="${CSS.escape(data.room_slug)}"]`,
    );
    if (!roomLink) {
        return;
    }

    const badge = roomLink.querySelector(".room-unread-count");
    const currentCount = Number.parseInt(badge?.textContent || "0", 10);
    if (badge) {
        badge.textContent = String(currentCount + 1);
        return;
    }

    const unread = document.createElement("span");
    unread.className = "room-unread-count";
    unread.textContent = "1";
    roomLink.append(unread);
}

function updateUserPresence(users, online) {
    presenceUsers = users;
    presenceOnlineUsers = online;
    const onlineUsers = new Set(
        online.map((user) => normalizeUsername(user.username || user)),
    );
    const contacts = users.filter(
        (user) => normalizeUsername(user.username || user) !== normalizeUsername(currentUsername),
    );

    renderUserList(
        "online-users-list",
        contacts.filter((user) => onlineUsers.has(normalizeUsername(user.username || user))),
        "online-user",
        "Сейчас вы один у стойки",
        "online-users-count",
        "toggle-online-users",
    );
    renderUserList(
        "offline-users-list",
        contacts.filter((user) => !onlineUsers.has(normalizeUsername(user.username || user))),
        "offline-user",
        "Все сейчас в баре",
        "offline-users-count",
        "toggle-offline-users",
    );
}

function normalizeUsername(username) {
    return String(username || "").trim().toLocaleLowerCase();
}

function userDetails(user) {
    if (typeof user === "string") {
        return { username: user, avatarUrl: null, status: "", lastSeenAt: null };
    }

    return {
        username: String(user?.username || ""),
        avatarUrl: user?.avatar_url || null,
        status: String(user?.status || ""),
        lastSeenAt: user?.last_seen_at || null,
    };
}

function formatLastSeen(value) {
    const seenAt = value ? new Date(value) : null;
    if (!seenAt || Number.isNaN(seenAt.getTime())) {
        return "Давно не заходил(а)";
    }

    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startOfSeenDay = new Date(
        seenAt.getFullYear(),
        seenAt.getMonth(),
        seenAt.getDate(),
    );
    const daysAgo = Math.round((startOfToday - startOfSeenDay) / 86400000);
    const time = seenAt.toLocaleTimeString("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
    });
    if (daysAgo === 0) {
        return `Был(а) в ${time}`;
    }
    if (daysAgo === 1) {
        return `Был(а) вчера в ${time}`;
    }
    const date = seenAt.toLocaleDateString("ru-RU", {
        day: "numeric",
        month: "short",
        ...(seenAt.getFullYear() === now.getFullYear() ? {} : {year: "numeric"}),
    });
    return `Был(а) ${date}`;
}

function userInitials(username) {
    return Array.from(String(username || "?").trim())
        .slice(0, 2)
        .join("")
        .toUpperCase();
}

function createUserAvatar(user, className = "user-avatar") {
    const details = userDetails(user);
    const avatar = document.createElement("span");
    avatar.className = className;

    if (details.avatarUrl) {
        const image = document.createElement("img");
        image.src = details.avatarUrl;
        image.alt = "";
        image.loading = "lazy";
        avatar.append(image);
        return avatar;
    }

    avatar.textContent = userInitials(details.username);
    return avatar;
}

function renderUserList(
    containerId,
    usernames,
    className,
    emptyText,
    countId,
    toggleId,
) {
    const container = document.getElementById(containerId);
    if (!container) {
        return;
    }

    const count = document.getElementById(countId);
    const toggle = document.getElementById(toggleId);
    if (count) {
        count.textContent = String(usernames.length);
    }

    if (!usernames.length) {
        const empty = document.createElement("span");
        empty.className = "users-empty";
        empty.textContent = emptyText;
        container.replaceChildren(empty);
        toggle?.classList.add("hidden");
        return;
    }

    container.replaceChildren(
        ...usernames.map((user) => {
            const details = userDetails(user);
            const button = document.createElement("button");
            button.type = "button";
            button.className = `online-user user-contact ${className}`;
            const name = document.createElement("span");
            const identity = document.createElement("span");
            const status = document.createElement("span");
            identity.className = "user-contact-identity";
            name.className = "user-contact-name";
            name.textContent = details.username;
            name.title = details.username;
            status.className = "user-contact-status";
            status.textContent = className === "online-user"
                ? (details.status || "Сейчас в беседе")
                : formatLastSeen(details.lastSeenAt);
            identity.append(name, status);
            button.append(createUserAvatar(details), identity);
            button.addEventListener("click", () => setDirectRecipient(details.username));
            return button;
        }),
    );

    if (!toggle) {
        return;
    }
    toggle.classList.add("hidden");
}

function setDirectRecipient(username) {
    stopTyping();
    clearBartenderMode(false);
    directRecipient = username;

    const banner = document.getElementById("direct-recipient");
    const name = document.getElementById("direct-recipient-name");
    const input = document.getElementById("chat-message-input");
    if (!banner || !name || !input) {
        return;
    }

    name.textContent = `@${username}`;
    banner.classList.remove("hidden");
    input.placeholder = `Личное сообщение для @${username}`;
    input.focus();
}

function clearDirectRecipient(focus = true) {
    stopTyping();
    directRecipient = null;

    const banner = document.getElementById("direct-recipient");
    const input = document.getElementById("chat-message-input");
    banner?.classList.add("hidden");
    if (input) {
        setComposerPlaceholder(input);
        if (focus) {
            input.focus();
        }
    }
}

function activateNoteMode() {
    stopTyping();
    clearReply();
    clearDirectRecipient(false);
    clearBartenderMode(false);
    noteMode = true;
    document.getElementById("note-recipient")?.classList.remove("hidden");

    const input = document.getElementById("chat-message-input");
    if (input) {
        input.placeholder = "Запишите мысль для себя…";
        input.focus();
    }
}

function clearNoteMode(focus = true) {
    noteMode = false;
    document.getElementById("note-recipient")?.classList.add("hidden");

    const input = document.getElementById("chat-message-input");
    if (input && !directRecipient && !bartenderMode) {
        setComposerPlaceholder(input);
    }
    if (focus) {
        input?.focus();
    }
}

function activateBartender() {
    stopTyping();
    clearReply();
    clearDirectRecipient(false);
    bartenderMode = true;
    bartenderPrivate = false;
    updateBartenderMode();

    const input = document.getElementById("chat-message-input");
    if (!input) {
        return;
    }
    if (!input.value.trim().startsWith(`@${BARTENDER_USERNAME}`)) {
        input.value = `@${BARTENDER_USERNAME} ${input.value.trim()}`.trimEnd() + " ";
    }
    input.focus();
}

function setBartenderVisibility(isPrivate) {
    stopTyping();
    bartenderPrivate = isPrivate;
    updateBartenderMode();
}

function clearBartenderMode(focus = true) {
    bartenderMode = false;
    bartenderPrivate = false;
    document.getElementById("bartender-recipient")?.classList.add("hidden");

    const input = document.getElementById("chat-message-input");
    if (input?.value.startsWith(`@${BARTENDER_USERNAME}`)) {
        input.value = input.value.slice(BARTENDER_USERNAME.length + 1).trimStart();
    }
    if (input && !directRecipient) {
        setComposerPlaceholder(input);
    }
    updateInputSize();
    if (focus) {
        input?.focus();
    }
}

function updateBartenderMode() {
    const banner = document.getElementById("bartender-recipient");
    banner?.classList.toggle("hidden", !bartenderMode);
    document.getElementById("bartender-public")?.classList.toggle("selected", !bartenderPrivate);
    document.getElementById("bartender-private")?.classList.toggle("selected", bartenderPrivate);
}

function addMessage(data) {
    const chatLog = document.getElementById("chat-log");
    if (!chatLog) {
        return;
    }

    const wasNearBottom = isNearBottom(chatLog);
    clearHistorySkeleton();
    chatLog.querySelector(".chat-empty-state")?.remove();
    const timestamp = data.timestamp || data.created_at;
    appendDayDivider(chatLog, timestamp);

    const message = document.createElement("div");
    message.className = "message";
    if (data.id) {
        message.dataset.messageId = String(data.id);
    }
    if (data.private) {
        message.classList.add("private");
    }
    if (normalizeUsername(data.username) === normalizeUsername(currentUsername)) {
        message.classList.add("own");
    }
    if (["amber", "blue", "sage", "plum"].includes(data.color)) {
        message.dataset.color = data.color;
    }

    const content = document.createElement("div");
    content.className = "message-content";

    if (data.reply_to) {
        const quote = document.createElement("button");
        quote.type = "button";
        quote.className = "message-reply-quote";
        quote.textContent = data.reply_to.available
            ? `↩ ${data.reply_to.username}: ${data.reply_to.message}`
            : "↩ Исходная реплика недоступна";
        quote.title = quote.textContent;
        quote.addEventListener("click", () => scrollToMessage(data.reply_to.id));
        content.append(quote);
    }

    const author = document.createElement("div");
    author.className = "message-username";
    const avatar = createUserAvatar({
        username: data.username,
        avatar_url: data.avatar_url,
    }, "message-author-avatar");
    const authorName = document.createElement(
        normalizeUsername(data.username) === normalizeUsername(currentUsername)
            ? "button"
            : "span",
    );
    if (authorName.tagName === "BUTTON") {
        authorName.type = "button";
        authorName.className = "message-own-name";
        authorName.title = "Написать личную заметку";
        authorName.addEventListener("click", activateNoteMode);
    }
    authorName.textContent = data.private && data.username === currentUsername
        ? `Вы → ${data.recipient}`
        : data.username;
    authorName.classList.add("message-author-name");
    authorName.title = authorName.textContent;
    author.append(authorName);

    const canDelete = normalizeUsername(data.username) === normalizeUsername(currentUsername)
        || canModerateMessages;
    if (data.id && canDelete) {
        const removeTitle = canModerateMessages
            && normalizeUsername(data.username) !== normalizeUsername(currentUsername)
            ? "Убрать сообщение как модератор"
            : "Удалить сообщение";
        const remove = createMessageAction("message-delete", removeTitle, "trash");
        remove.addEventListener("click", () => openDeleteMessageDialog(data.id));
        author.append(remove);
    }
    if (data.id) {
        const reply = createMessageAction("message-reply", "Ответить", "reply");
        reply.addEventListener("click", () => activateReply(data));
        author.append(reply);
        const save = createMessageAction(
            "message-save-note",
            "Приколоть к личным заметкам",
            "pin",
        );
        save.addEventListener("click", () => saveNote(data.id));
        author.append(save);
    }
    if (
        data.id
        && normalizeUsername(data.username) !== normalizeUsername(currentUsername)
    ) {
        const report = createMessageAction(
            "message-report",
            "Пожаловаться модератору",
            "flag",
        );
        report.addEventListener("click", () => openReportMessageDialog(data.id));
        author.append(report);
    }

    const text = document.createElement("div");
    text.className = "message-text";
    if (data.message) {
        renderMessageText(text, data.message);
        if (/^(?=.*\p{Extended_Pictographic})[\p{Extended_Pictographic}\p{Emoji_Component}\s]+$/u.test(data.message)) {
            text.classList.add("is-emoji-only");
        }
    }

    const time = document.createElement("div");
    time.className = "message-time";
    if (timestamp) {
        time.textContent = new Date(timestamp).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    content.append(author, text, time);
    // Время должно быть в DOM до вложений: они встают непосредственно перед ним.
    renderMessageAttachments(content, data.attachments || []);
    renderMessageReactions(content, data.reactions || [], data.id);
    message.append(avatar, content);
    message.addEventListener("click", (event) => {
        if (event.target.closest("button, a")) {
            return;
        }
        toggleMessageSelection(message);
    });
    chatLog.append(message);

    if (
        pendingAttachmentUpload
        && message.classList.contains("own")
        && data.id
    ) {
        const files = pendingAttachmentUpload;
        pendingAttachmentUpload = null;
        uploadMessageAttachments(data.id, files);
    }

    if (data.username === BARTENDER_USERNAME) {
        setBartenderTyping(false);
    }

    if (loadingHistory) {
        scrollToLatest(chatLog, false);
    } else if (wasNearBottom) {
        scrollToLatestAfterLayout(chatLog, true);
        keepLatestAfterImages(content, chatLog);
    } else if (!message.classList.contains("own")) {
        document.getElementById("scroll-to-latest")?.classList.remove("hidden");
    }
}

function renderMessageText(container, source) {
    const lines = String(source).split("\n");
    let proseLines = [];
    let fencedLines = null;
    let fencedLanguage = "";

    const appendProse = () => {
        if (!proseLines.length) return;
        const prose = document.createElement("span");
        prose.className = "message-prose";
        prose.textContent = proseLines.join("\n");
        container.append(prose);
        proseLines = [];
    };

    const appendCode = (codeLines, language = "") => {
        const block = document.createElement("pre");
        block.className = "message-code-block";
        block.tabIndex = 0;
        block.setAttribute("aria-label", "Блок кода");
        const code = document.createElement("code");
        code.textContent = codeLines.join("\n");
        if (language) code.dataset.language = language;
        block.append(code);
        container.append(block);
    };

    for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index];
        const openingFence = line.match(/^```([a-z0-9_+-]*)\s*$/i);
        if (fencedLines !== null) {
            if (/^```\s*$/.test(line)) {
                appendCode(fencedLines, fencedLanguage);
                fencedLines = null;
                fencedLanguage = "";
            } else {
                fencedLines.push(line);
            }
            continue;
        }
        if (openingFence) {
            appendProse();
            fencedLines = [];
            fencedLanguage = openingFence[1];
            continue;
        }
        if (line.startsWith(">>>")) {
            appendProse();
            const quotedLines = [];
            while (index < lines.length && lines[index].startsWith(">>>")) {
                quotedLines.push(lines[index]);
                index += 1;
            }
            appendCode(quotedLines);
            index -= 1;
            continue;
        }
        proseLines.push(line);
    }

    if (fencedLines !== null) appendCode(fencedLines, fencedLanguage);
    appendProse();
}

function updateMessageAttachments(messageId, attachments) {
    const message = document.querySelector(
        `.message[data-message-id="${CSS.escape(String(messageId))}"]`,
    );
    const content = message?.querySelector(".message-content");
    if (content) {
        const chatLog = document.getElementById("chat-log");
        const shouldFollow = chatLog && isNearBottom(chatLog);
        renderMessageAttachments(content, attachments);
        if (shouldFollow) {
            scrollToLatestAfterLayout(chatLog, true);
            keepLatestAfterImages(content, chatLog);
        }
    }
}

function removeMessage(messageId) {
    const message = document.querySelector(
        `.message[data-message-id="${CSS.escape(String(messageId))}"]`,
    );
    if (!message) {
        return;
    }
    if (selectedMessageElement === message) {
        selectedMessageElement = null;
    }
    message.remove();

    const chatLog = document.getElementById("chat-log");
    if (chatLog && !chatLog.querySelector(".message")) {
        renderEmptyState(chatLog);
    }
}

function toggleMessageSelection(message) {
    if (selectedMessageElement === message) {
        message.classList.remove("is-selected");
        selectedMessageElement = null;
        return;
    }

    selectedMessageElement?.classList.remove("is-selected");
    selectedMessageElement = message;
    message.classList.add("is-selected");
}

function activateReply(data) {
    clearBartenderMode(false);
    clearNoteMode();
    if (data.private) {
        const other = normalizeUsername(data.username) === normalizeUsername(currentUsername)
            ? data.recipient : data.username;
        setDirectRecipient(other);
    } else {
        clearDirectRecipient();
    }
    replyTarget = {id: data.id, username: data.username, message: data.message};
    document.getElementById("reply-author").textContent = data.username;
    document.getElementById("reply-preview").textContent = data.message.slice(0, 100);
    document.getElementById("reply-recipient")?.classList.remove("hidden");
    document.getElementById("chat-message-input")?.focus();
}

function clearReply() {
    replyTarget = null;
    document.getElementById("reply-recipient")?.classList.add("hidden");
}

function scrollToMessage(messageId) {
    const source = document.querySelector(`.message[data-message-id="${CSS.escape(String(messageId))}"]`);
    if (!source) return;
    source.scrollIntoView({behavior: "smooth", block: "center"});
    source.classList.add("reply-highlight");
    window.setTimeout(() => source.classList.remove("reply-highlight"), 1400);
}

function openDeleteMessageDialog(messageId) {
    const dialog = document.getElementById("delete-message-modal");
    if (!dialog) {
        return;
    }
    pendingDeletionMessageId = messageId;
    dialog.classList.remove("hidden");
    document.getElementById("cancel-message-delete")?.focus();
}

function closeDeleteMessageDialog() {
    pendingDeletionMessageId = null;
    document.getElementById("delete-message-modal")?.classList.add("hidden");
}

function openReportMessageDialog(messageId) {
    pendingReportMessageId = messageId;
    document.getElementById("message-report-details").value = "";
    document.getElementById("report-message-modal")?.classList.remove("hidden");
    document.getElementById("message-report-reason")?.focus();
}

function closeReportMessageDialog() {
    pendingReportMessageId = null;
    document.getElementById("report-message-modal")?.classList.add("hidden");
}

async function submitMessageReport() {
    if (!pendingReportMessageId) return;
    const messageId = pendingReportMessageId;
    const reason = document.getElementById("message-report-reason")?.value;
    const details = document.getElementById("message-report-details")?.value || "";
    try {
        const response = await fetch(
            messageReportTemplate.replace("/0/", `/${messageId}/`),
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrfToken(),
                },
                credentials: "same-origin",
                body: JSON.stringify({reason, details}),
            },
        );
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Не удалось отправить жалобу.");
        closeReportMessageDialog();
        showSuccess(payload.created ? "Жалоба отправлена модератору." : "Жалоба уже отправлена.");
    } catch (error) {
        showError(error.message || "Не удалось отправить жалобу.");
    }
}

async function deleteMessage(messageId) {
    try {
        const response = await fetch(
            messageDeleteTemplate.replace("/0/", `/${messageId}/`),
            {
                method: "POST",
                headers: { "X-CSRFToken": getCsrfToken() },
                credentials: "same-origin",
            },
        );
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.error || "Не удалось удалить сообщение.");
        }
        removeMessage(messageId);
        closeDeleteMessageDialog();
        showSuccess("Реплика убрана: рабочее дерево снова чистое.");
    } catch (error) {
        showError(error.message || "Не удалось удалить сообщение.");
    }
}

async function saveNote(sourceMessageId = null) {
    const input = document.getElementById("chat-message-input");
    const text = input?.value.trim() || "";
    if (!sourceMessageId && !text) {
        showError("Заметка не может быть пустой.");
        return false;
    }

    try {
        const response = await fetch(noteCreateUrl, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCsrfToken(),
            },
            credentials: "same-origin",
            body: JSON.stringify(sourceMessageId
                ? { source_message_id: sourceMessageId }
                : { text }),
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.error || "Не удалось сохранить заметку.");
        }
        if (!sourceMessageId && input) {
            input.value = "";
            updateInputSize();
            clearNoteMode();
        }
        showSuccess(
            payload.created
                ? "Заметка сохранена."
                : "Эта реплика уже есть в заметках.",
        );
        return true;
    } catch (error) {
        showError(error.message || "Не удалось сохранить заметку.");
        return false;
    }
}

function renderMessageAttachments(content, attachments) {
    content.querySelector(".message-attachments")?.remove();
    if (!attachments.length) {
        return;
    }

    const container = document.createElement("div");
    container.className = "message-attachments";
    attachments.forEach((attachment) => {
        if (attachment.kind === "audio") {
            const audioContainer = document.createElement("div");
            audioContainer.className = "message-attachment message-attachment-audio";
            const audio = document.createElement("audio");
            audio.controls = true;
            audio.preload = "metadata";
            audio.src = attachment.preview_url;
            audio.setAttribute("aria-label", "Аудиосообщение");
            audioContainer.append(audio);
            container.append(audioContainer);
            return;
        }
        const link = document.createElement("a");
        link.className = `message-attachment message-attachment-${attachment.kind}`;
        link.href = attachment.preview_url;
        link.target = "_blank";
        link.rel = "noopener";

        if (attachment.kind === "image") {
            const image = document.createElement("img");
            image.src = attachment.preview_url;
            image.alt = attachment.name;
            image.loading = "lazy";
            link.append(image);
        } else {
            const name = document.createElement("span");
            name.className = "message-attachment-name";
            name.textContent = attachment.name;
            const size = document.createElement("span");
            size.className = "message-attachment-size";
            size.textContent = formatFileSize(attachment.size);
            link.href = attachment.download_url;
            link.download = attachment.name;
            link.append(name, size);
        }
        container.append(link);
    });
    content.querySelector(".message-time")?.before(container);
}

function selectAudioFormat() {
    const variants = [
        {mimeType: "audio/webm;codecs=opus", extension: "webm"},
        {mimeType: "audio/mp4;codecs=mp4a.40.2", extension: "m4a"},
        {mimeType: "audio/mp4", extension: "m4a"},
        {mimeType: "audio/webm", extension: "webm"},
        {mimeType: "audio/ogg;codecs=opus", extension: "ogg"},
    ];
    return variants.find(
        ({mimeType}) => MediaRecorder.isTypeSupported?.(mimeType),
    ) || null;
}

function formatAudioTime(milliseconds) {
    const seconds = Math.floor(milliseconds / 1000);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function updateAudioTimer() {
    const elapsed = Math.min(Date.now() - audioStartedAt, AUDIO_MAX_DURATION_MS);
    const time = document.getElementById("audio-recording-time");
    if (time) {
        time.textContent = formatAudioTime(elapsed);
    }
}

function releaseAudioStream() {
    audioStream?.getTracks().forEach((track) => track.stop());
    audioStream = null;
}

function clearAudioRecording() {
    window.clearInterval(audioTimer);
    window.clearTimeout(audioStopTimer);
    audioTimer = null;
    audioStopTimer = null;
    releaseAudioStream();
    audioRecorder = null;
    audioChunks = [];
    recordedAudio = null;
    recordedAudioDurationMs = 0;
    cancelAudioOnStop = false;
    sendAudioOnStop = false;
    audioPointerId = null;
    audioGestureCanceled = false;
    document.getElementById("audio-recorder")?.classList.add("hidden");
    document.getElementById("chat-message-submit")?.classList.remove("is-recording");
    updateComposerSubmitMode();
}

function finishAudioRecording(shouldSend = true) {
    if (audioRecorder?.state === "recording") {
        sendAudioOnStop = shouldSend;
        cancelAudioOnStop = !shouldSend;
        audioRecorder.stop();
    }
}

async function startAudioRecording() {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
        showError("Этот браузер не умеет записывать аудиосообщения.");
        return;
    }
    if (directRecipient || bartenderMode || noteMode || replyTarget) {
        showError("Сначала завершите текущий режим ответа или заметки.");
        return;
    }
    if (selectedAttachments.length || pendingAttachmentUpload) {
        showError("Сначала отправьте или уберите выбранные файлы.");
        return;
    }

    try {
        const format = selectAudioFormat();
        audioStream = await navigator.mediaDevices.getUserMedia({audio: true});
        if (audioPointerId === null) {
            releaseAudioStream();
            return;
        }
        audioRecorder = format
            ? new MediaRecorder(audioStream, {mimeType: format.mimeType})
            : new MediaRecorder(audioStream);
        audioRecorder.datasetExtension = format?.extension || (
            audioRecorder.mimeType.includes("mp4") ? "m4a" : "webm"
        );
        audioChunks = [];
        cancelAudioOnStop = false;
        sendAudioOnStop = false;
        audioRecorder.addEventListener("dataavailable", (event) => {
            if (event.data.size) {
                audioChunks.push(event.data);
            }
        });
        audioRecorder.addEventListener("stop", () => {
            window.clearInterval(audioTimer);
            window.clearTimeout(audioStopTimer);
            releaseAudioStream();
            if (cancelAudioOnStop || !audioChunks.length) {
                clearAudioRecording();
                return;
            }
            recordedAudioDurationMs = Math.max(1, Math.min(
                Date.now() - audioStartedAt,
                AUDIO_MAX_DURATION_MS,
            ));
            recordedAudio = new Blob(audioChunks, {type: audioRecorder.mimeType});
            if (sendAudioOnStop) {
                sendAudioRecording();
            } else {
                clearAudioRecording();
            }
        }, {once: true});
        audioStartedAt = Date.now();
        audioRecorder.start(250);
        document.getElementById("audio-recorder")?.classList.remove("hidden");
        document.getElementById("chat-message-submit")?.classList.add("is-recording");
        updateAudioTimer();
        audioTimer = window.setInterval(updateAudioTimer, 250);
        audioStopTimer = window.setTimeout(() => finishAudioRecording(true), AUDIO_MAX_DURATION_MS);
    } catch (error) {
        clearAudioRecording();
        showError(error?.name === "NotAllowedError"
            ? "Разрешите доступ к микрофону в настройках браузера."
            : "Не удалось начать запись. Проверьте микрофон.");
    }
}

async function sendAudioRecording() {
    if (!recordedAudio || !audioRecorder) {
        return;
    }
    const hint = document.getElementById("audio-recording-hint");
    if (hint) {
        hint.textContent = "Отправляем…";
    }
    const formData = new FormData();
    const extension = audioRecorder.datasetExtension || "webm";
    formData.append("audio", recordedAudio, `voice-${Date.now()}.${extension}`);
    formData.append("duration_ms", String(recordedAudioDurationMs));
    try {
        const response = await fetch(audioMessageUrl, {
            method: "POST",
            body: formData,
            headers: {"X-CSRFToken": getCsrfToken()},
            credentials: "same-origin",
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.error || "Не удалось отправить аудиосообщение.");
        }
        clearAudioRecording();
    } catch (error) {
        showError(error.message || "Не удалось отправить аудиосообщение.");
        clearAudioRecording();
    } finally {
        if (hint) {
            hint.textContent = "Отпустите, чтобы отправить · влево — отмена";
        }
    }
}

function composerHasText() {
    return Boolean(document.getElementById("chat-message-input")?.value.trim());
}

function updateComposerSubmitMode() {
    const button = document.getElementById("chat-message-submit");
    if (!button || audioRecorder?.state === "recording") {
        return;
    }
    const voiceMode = !composerHasText();
    button.classList.toggle("is-voice-mode", voiceMode);
    button.querySelector(".submit-icon")?.classList.toggle("hidden", voiceMode);
    button.querySelector(".microphone-icon")?.classList.toggle("hidden", !voiceMode);
    button.ariaLabel = voiceMode
        ? "Удерживайте для записи аудиосообщения"
        : "Отправить сообщение";
    button.title = voiceMode ? "Удерживайте для записи" : "Отправить";
}

function startAudioGesture(event) {
    if (composerHasText() || audioRecorder) {
        return;
    }
    event.preventDefault();
    audioPointerId = event.pointerId;
    audioPointerStartX = event.clientX;
    audioGestureCanceled = false;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    startAudioRecording();
}

function moveAudioGesture(event) {
    if (event.pointerId !== audioPointerId) {
        return;
    }
    audioGestureCanceled = event.clientX - audioPointerStartX < -70;
    const hint = document.getElementById("audio-recording-hint");
    if (hint) {
        hint.textContent = audioGestureCanceled
            ? "Отпустите, чтобы отменить"
            : "Отпустите, чтобы отправить · влево — отмена";
    }
}

function endAudioGesture(event) {
    if (event.pointerId !== audioPointerId) {
        return;
    }
    const shouldSend = !audioGestureCanceled && event.type === "pointerup";
    audioPointerId = null;
    if (audioRecorder?.state === "recording") {
        finishAudioRecording(shouldSend);
    }
}

function renderMessageReactions(content, reactions, messageId) {
    content.querySelector(".message-reactions")?.remove();
    if (!messageId) {
        return;
    }

    const container = document.createElement("div");
    container.className = "message-reactions";
    reactions.forEach((reaction) => {
        container.append(createReactionButton(messageId, reaction));
    });
    container.append(createReactionPicker(messageId));
    refreshReactionContainer(container);
    content.querySelector(".message-time")?.before(container);
}

function refreshReactionContainer(container) {
    if (!container) {
        return;
    }
    container.classList.toggle(
        "is-empty",
        !container.querySelector(".message-reaction"),
    );
}

function createReactionButton(messageId, reaction) {
    const reactionElement = document.createElement("span");
    reactionElement.className = "message-reaction";
    reactionElement.dataset.emoji = reaction.emoji;
    reactionElement.classList.toggle("is-active", Boolean(reaction.reacted));

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "message-reaction-toggle";
    toggle.textContent = reaction.emoji;
    toggle.ariaLabel = reaction.reacted
        ? `Снять реакцию ${reaction.emoji}`
        : `Поставить реакцию ${reaction.emoji}`;
    toggle.addEventListener("click", () => toggleReaction(messageId, reaction.emoji));

    const count = document.createElement("button");
    count.type = "button";
    count.className = "message-reaction-users";
    count.textContent = String(reaction.count);
    const users = reaction.users || [];
    count.ariaLabel = `Кто поставил ${reaction.emoji}: ${reaction.count}`;
    count.setAttribute("aria-expanded", "false");

    const popover = document.createElement("span");
    popover.className = "message-reaction-users-popover hidden";
    popover.setAttribute("role", "tooltip");
    popover.textContent = users.length ? users.join("\n") : "Пока никто";
    count.addEventListener("click", (event) => {
        event.stopPropagation();
        const willOpen = popover.classList.contains("hidden");
        closeReactionUserPopovers();
        popover.classList.toggle("hidden", !willOpen);
        count.setAttribute("aria-expanded", String(willOpen));
    });

    reactionElement.append(toggle, count, popover);
    return reactionElement;
}

function closeReactionUserPopovers() {
    document.querySelectorAll(".message-reaction-users-popover").forEach((popover) => {
        popover.classList.add("hidden");
        popover.parentElement
            ?.querySelector(".message-reaction-users")
            ?.setAttribute("aria-expanded", "false");
    });
}

function createReactionPicker(messageId) {
    const wrapper = document.createElement("div");
    wrapper.className = "reaction-picker-wrap";
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "message-reaction-add";
    trigger.textContent = "+";
    trigger.title = "Добавить реакцию";
    trigger.ariaLabel = trigger.title;
    trigger.ariaExpanded = "false";

    const picker = document.createElement("div");
    picker.className = "message-reaction-picker hidden";
    picker.setAttribute("role", "group");
    picker.setAttribute("aria-label", "Выберите реакцию");
    REACTION_EMOJI.forEach((emoji) => {
        const emojiButton = document.createElement("button");
        emojiButton.type = "button";
        emojiButton.textContent = emoji;
        emojiButton.ariaLabel = emoji;
        emojiButton.addEventListener("click", () => {
            toggleReaction(messageId, emoji);
            picker.classList.add("hidden");
            trigger.ariaExpanded = "false";
        });
        picker.append(emojiButton);
    });
    trigger.addEventListener("click", () => {
        const isOpen = !picker.classList.contains("hidden");
        document.querySelectorAll(".message-reaction-picker").forEach((item) => {
            item.classList.add("hidden");
        });
        picker.classList.toggle("hidden", isOpen);
        trigger.ariaExpanded = String(!isOpen);
    });
    wrapper.append(trigger, picker);
    return wrapper;
}

function updateMessageReaction(data) {
    const message = document.querySelector(
        `.message[data-message-id="${CSS.escape(String(data.message_id))}"]`,
    );
    const content = message?.querySelector(".message-content");
    if (!content) {
        return;
    }

    const isCurrentUser = normalizeUsername(data.actor_username)
        === normalizeUsername(currentUsername);
    const existing = content.querySelector(
        `.message-reaction[data-emoji="${CSS.escape(data.emoji)}"]`,
    );
    if (data.count === 0) {
        existing?.remove();
        refreshReactionContainer(content.querySelector(".message-reactions"));
        return;
    }
    if (existing) {
        const users = data.users || [];
        const count = existing.querySelector(".message-reaction-users");
        count.textContent = String(data.count);
        count.ariaLabel = `Кто поставил ${data.emoji}: ${data.count}`;
        existing.querySelector(".message-reaction-users-popover").textContent = users.length
            ? users.join("\n")
            : "Пока никто";
        if (isCurrentUser) {
            existing.classList.toggle("is-active", data.active);
            const toggle = existing.querySelector(".message-reaction-toggle");
            toggle.ariaLabel = data.active
                ? `Снять реакцию ${data.emoji}`
                : `Поставить реакцию ${data.emoji}`;
        }
        return;
    }

    const picker = content.querySelector(".reaction-picker-wrap");
    const button = createReactionButton(data.message_id, {
        emoji: data.emoji,
        count: data.count,
        reacted: isCurrentUser && data.active,
        users: data.users || [],
    });
    picker?.before(button);
    refreshReactionContainer(content.querySelector(".message-reactions"));
}

document.addEventListener("click", (event) => {
    if (!event.target.closest(".message-reaction")) {
        closeReactionUserPopovers();
    }
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
        closeReactionUserPopovers();
    }
});

function toggleReaction(messageId, emoji) {
    if (!chatSocket || chatSocket.readyState !== WebSocket.OPEN) {
        showError("Связь со стойкой прервалась. Попробуйте ещё раз.");
        return;
    }
    chatSocket.send(JSON.stringify({
        type: "reaction",
        message_id: messageId,
        emoji,
    }));
}

function formatFileSize(size) {
    if (size < 1024) {
        return `${size} Б`;
    }
    if (size < 1024 * 1024) {
        return `${Math.ceil(size / 1024)} КБ`;
    }
    return `${(size / (1024 * 1024)).toFixed(1)} МБ`;
}

function appendDayDivider(chatLog, timestamp) {
    if (!timestamp) {
        return;
    }

    const date = new Date(timestamp);
    const dayKey = date.toLocaleDateString("ru-RU");
    if (dayKey === lastMessageDay) {
        return;
    }
    lastMessageDay = dayKey;

    const divider = document.createElement("div");
    divider.className = "day-divider";
    const today = new Date().toLocaleDateString("ru-RU");
    divider.textContent = dayKey === today ? "Сегодня" : dayKey;
    chatLog.append(divider);
}

function renderEmptyState(chatLog) {
    const state = document.createElement("div");
    state.className = "chat-empty-state";
    const title = document.createElement("strong");
    title.textContent = "У стойки пока тихо.";
    const description = document.createElement("span");
    description.textContent = "Первое слово — за вами.";
    state.append(title, description);
    chatLog.append(state);
}

function isNearBottom(chatLog) {
    return chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 48;
}

function scrollToLatest(chatLog, smooth = true) {
    chatLog.scrollTo({
        top: chatLog.scrollHeight,
        behavior: smooth && !window.matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "smooth"
            : "auto",
    });
}

function scrollToLatestAfterLayout(chatLog, smooth = true) {
    window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => scrollToLatest(chatLog, smooth));
    });
}

function keepLatestAfterImages(content, chatLog) {
    content.querySelectorAll("img").forEach((image) => {
        if (!image.complete) {
            image.addEventListener(
                "load",
                () => scrollToLatestAfterLayout(chatLog, false),
                {once: true},
            );
        }
    });
}

function showError(message) {
    const errorElement = document.getElementById("error-message");
    if (!errorElement) {
        return;
    }

    errorElement.classList.remove("is-success");
    errorElement.textContent = message;
    window.setTimeout(() => {
        errorElement.textContent = "";
    }, 3000);
}

function showSuccess(message) {
    const errorElement = document.getElementById("error-message");
    if (!errorElement) {
        return;
    }

    errorElement.classList.add("is-success");
    errorElement.textContent = message;
    window.setTimeout(() => {
        errorElement.textContent = "";
        errorElement.classList.remove("is-success");
    }, 3000);
}

function sendMessage() {
    const input = document.getElementById("chat-message-input");
    const message = replaceTextEmoticons(input?.value.trim() || "");
    if (!message) {
        if (selectedAttachments.length) {
            showError("Добавьте короткую подпись к файлам перед отправкой.");
        }
        return;
    }

    if (input && input.value !== message) {
        input.value = message;
        updateInputSize();
    }

    if (pendingAttachmentUpload) {
        showError("Подождите, пока предыдущие файлы попадут в сообщение.");
        return;
    }

    if (noteMode) {
        if (selectedAttachments.length) {
            showError("К личной заметке нельзя прикрепить файлы.");
            return;
        }
        saveNote();
        return;
    }

    if (message.length > MESSAGE_MAX_LENGTH) {
        showError(`Сообщение не может быть длиннее ${MESSAGE_MAX_LENGTH} символов`);
        input.focus();
        return;
    }

    if (!chatSocket || chatSocket.readyState !== WebSocket.OPEN) {
        showError("Связь со стойкой прервалась. Попробуйте ещё раз.");
        return;
    }

    if (selectedAttachments.length) {
        pendingAttachmentUpload = [...selectedAttachments];
    }
    stopTyping();
    chatSocket.send(JSON.stringify({
        message,
        recipient: directRecipient,
        bartender_private: bartenderMode && bartenderPrivate,
        reply_to: replyTarget?.id || null,
    }));
    if (bartenderMode || isBartenderRequest(message)) {
        setBartenderTyping(true);
    }
    input.value = bartenderMode ? `@${BARTENDER_USERNAME} ` : "";
    clearReply();
    updateInputSize();
    input.focus();
}

function selectAttachments() {
    document.getElementById("chat-attachment-input")?.click();
}

function handleAttachmentSelection(event) {
    const files = Array.from(event.target.files || []);
    if (!files.length) {
        return;
    }
    if (selectedAttachments.length + files.length > 3) {
        showError("К одному сообщению можно прикрепить не больше трёх файлов.");
        event.target.value = "";
        return;
    }

    selectedAttachments = [...selectedAttachments, ...files];
    event.target.value = "";
    renderSelectedAttachments();
}

function removeSelectedAttachment(index) {
    selectedAttachments = selectedAttachments.filter((_, itemIndex) => itemIndex !== index);
    renderSelectedAttachments();
}

function renderSelectedAttachments() {
    const container = document.getElementById("attachment-preview");
    if (!container) {
        return;
    }
    container.replaceChildren();
    container.classList.toggle("hidden", !selectedAttachments.length);

    selectedAttachments.forEach((file, index) => {
        const item = document.createElement("div");
        item.className = "attachment-preview-item";
        if (file.type.startsWith("image/")) {
            const image = document.createElement("img");
            image.src = URL.createObjectURL(file);
            image.alt = "Предпросмотр изображения";
            item.append(image);
        }
        const details = document.createElement("span");
        details.className = "attachment-preview-details";
        details.textContent = `${file.name} · ${formatFileSize(file.size)}`;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "attachment-remove";
        remove.textContent = "×";
        remove.ariaLabel = `Убрать ${file.name}`;
        remove.addEventListener("click", () => removeSelectedAttachment(index));
        item.append(details, remove);
        container.append(item);
    });
}

async function uploadMessageAttachments(messageId, files) {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));

    try {
        const response = await fetch(
            attachmentUploadTemplate.replace("/0/", `/${messageId}/`),
            {
                method: "POST",
                body: formData,
                headers: { "X-CSRFToken": getCsrfToken() },
                credentials: "same-origin",
            },
        );
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.error || "Не удалось загрузить файлы.");
        }
        selectedAttachments = [];
        renderSelectedAttachments();
    } catch (error) {
        selectedAttachments = files;
        renderSelectedAttachments();
        showError(error.message || "Не удалось загрузить файлы.");
    }
}

function getCsrfToken() {
    const cookie = document.cookie
        .split("; ")
        .find((item) => item.startsWith("csrftoken="));
    return cookie ? decodeURIComponent(cookie.split("=")[1]) : "";
}

function isBartenderRequest(message) {
    return message.toLocaleLowerCase().startsWith("@семён")
        || message.toLocaleLowerCase().startsWith("@семен");
}

function setBartenderTyping(isTyping) {
    bartenderTyping = isTyping;
    renderTypingIndicator();
}

function renderTypingIndicator() {
    const indicator = document.getElementById("typing-indicator");
    if (!indicator) {
        return;
    }
    if (bartenderTyping) {
        indicator.textContent = "Семён протирает стакан и подбирает слова…";
        indicator.classList.remove("hidden");
        return;
    }

    const usernames = [...typingUsers.keys()];
    if (!usernames.length) {
        indicator.classList.add("hidden");
        return;
    }
    indicator.textContent = usernames.length === 1
        ? `${usernames[0]} печатает…`
        : `${usernames.slice(0, 2).join(" и ")} печатают…`;
    indicator.classList.remove("hidden");
}

function updateTypingUser(data) {
    if (normalizeUsername(data.username) === normalizeUsername(currentUsername)) {
        return;
    }
    window.clearTimeout(typingUsers.get(data.username));
    if (!data.active) {
        typingUsers.delete(data.username);
        renderTypingIndicator();
        return;
    }
    typingUsers.set(data.username, window.setTimeout(() => {
        typingUsers.delete(data.username);
        renderTypingIndicator();
    }, TYPING_TTL_MS));
    renderTypingIndicator();
}

function sendTypingState(active, recipient = typingRecipient) {
    if (!chatSocket || chatSocket.readyState !== WebSocket.OPEN) {
        return;
    }
    chatSocket.send(JSON.stringify({type: "typing", active, recipient}));
}

function stopTyping() {
    window.clearTimeout(typingDebounceTimer);
    window.clearTimeout(typingIdleTimer);
    typingDebounceTimer = null;
    typingIdleTimer = null;
    if (typingActive) {
        sendTypingState(false);
    }
    typingActive = false;
    typingRecipient = null;
}

function scheduleTyping() {
    const input = document.getElementById("chat-message-input");
    if (!input?.value.trim() || noteMode || (bartenderMode && bartenderPrivate)) {
        stopTyping();
        return;
    }

    const recipient = directRecipient;
    if (typingActive && recipient !== typingRecipient) {
        stopTyping();
    }
    window.clearTimeout(typingDebounceTimer);
    if (!typingActive) {
        typingDebounceTimer = window.setTimeout(() => {
            typingRecipient = recipient;
            typingActive = true;
            sendTypingState(true, recipient);
        }, TYPING_DEBOUNCE_MS);
    }
    window.clearTimeout(typingIdleTimer);
    typingIdleTimer = window.setTimeout(stopTyping, TYPING_IDLE_MS);
}

function updateInputSize() {
    const input = document.getElementById("chat-message-input");
    const counter = document.getElementById("message-char-count");
    if (!input || !counter) {
        return;
    }

    input.style.height = "46px";
    if (input.value) {
        input.style.height = `${Math.min(Math.max(input.scrollHeight, 46), 96)}px`;
    }
    counter.textContent = `${input.value.length} / ${MESSAGE_MAX_LENGTH}`;
    updateComposerSubmitMode();
}

function replaceTextEmoticons(text) {
    const protectedParts = /(https?:\/\/\S+|```[\s\S]*?```|`[^`\n]*`)/g;
    return text.split(protectedParts).map((part) => {
        if (/^(https?:\/\/|```|`)/.test(part)) {
            return part;
        }
        return part
            .replace(/(^|[\s(])(:\))(?=$|[\s),.!?])/g, "$1😊")
            .replace(/(^|[\s(])(;\))(?=$|[\s),.!?])/g, "$1😉")
            .replace(/(^|[\s(])(:D)(?=$|[\s),.!?])/g, "$1😄")
            .replace(/(^|[\s(])(:\()(?=$|[\s),.!?])/g, "$1😔")
            .replace(/(^|[\s(])(<3)(?=$|[\s),.!?])/g, "$1❤️");
    }).join("");
}

function toggleEmojiPicker() {
    const picker = document.getElementById("emoji-picker");
    const trigger = document.getElementById("emoji-trigger");
    if (!picker || !trigger) {
        return;
    }
    const isOpen = !picker.classList.contains("hidden");
    picker.classList.toggle("hidden", isOpen);
    trigger.ariaExpanded = String(!isOpen);
}

function insertEmoji(emoji) {
    const input = document.getElementById("chat-message-input");
    if (!input) {
        return;
    }
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    input.value = `${input.value.slice(0, start)}${emoji}${input.value.slice(end)}`;
    const caretPosition = start + emoji.length;
    input.setSelectionRange(caretPosition, caretPosition);
    updateInputSize();
    input.focus();
    document.getElementById("emoji-picker")?.classList.add("hidden");
    document.getElementById("emoji-trigger")?.setAttribute("aria-expanded", "false");
}

function initialiseAtmosphere() {
    const tagline = document.getElementById("brand-tagline");
    if (tagline) {
        taglineIndex = Math.floor(Math.random() * TAGLINES.length);
        tagline.textContent = TAGLINES[taglineIndex];
    }

    const input = document.getElementById("chat-message-input");
    setComposerPlaceholder(input);

    window.setInterval(() => {
        if (!document.hidden) {
            rotateTagline(tagline);
            rotateComposerHint(input);
        }
    }, 30000);
}

function rotateTagline(tagline) {
    if (!tagline) {
        return;
    }

    tagline.classList.add("is-changing");
    window.setTimeout(() => {
        taglineIndex = (taglineIndex + 1) % TAGLINES.length;
        tagline.textContent = TAGLINES[taglineIndex];
        tagline.classList.remove("is-changing");
    }, 180);
}

function rotateComposerHint(input) {
    if (!input || input.value || directRecipient || bartenderMode) {
        return;
    }

    composerHintIndex = (composerHintIndex + 1) % COMPOSER_HINTS.length;
    setComposerPlaceholder(input);
}

function setComposerPlaceholder(input) {
    if (input && !input.value && !directRecipient && !bartenderMode) {
        input.placeholder = COMPOSER_HINTS[composerHintIndex];
    }
}

function createMessageAction(className, title, icon) {
    const action = document.createElement("button");
    action.type = "button";
    action.className = `message-action ${className}`;
    action.title = title;
    action.ariaLabel = title;
    action.innerHTML = icon === "trash"
        ? '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 7h16M10 11v6m4-6v6M9 7l1-3h4l1 3m-9 0 1 13h10l1-13" /></svg>'
        : icon === "reply"
            ? '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M9 8 4 12l5 4v-3h4c3 0 5 1 7 4-1-6-4-8-7-8H9V8Z" /></svg>'
            : icon === "flag"
                ? '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3v18m1-16h10l-2 4 2 4H7" /></svg>'
            : '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m14 4 6 6-4 2-3 6-2-2-4 4-1-1 4-4-2-2 6-3 2-4Z" /></svg>';
    return action;
}

const composerSubmit = document.getElementById("chat-message-submit");
composerSubmit?.addEventListener("click", () => {
    if (composerHasText()) {
        sendMessage();
    }
});
composerSubmit?.addEventListener("pointerdown", startAudioGesture);
composerSubmit?.addEventListener("pointermove", moveAudioGesture);
composerSubmit?.addEventListener("pointerup", endAudioGesture);
composerSubmit?.addEventListener("pointercancel", endAudioGesture);
document.getElementById("chat-attachment-trigger")?.addEventListener("click", selectAttachments);
document.getElementById("chat-attachment-input")?.addEventListener("change", handleAttachmentSelection);
document.getElementById("cancel-direct-message")?.addEventListener("click", clearDirectRecipient);
document.getElementById("cancel-reply")?.addEventListener("click", clearReply);
document.getElementById("note-trigger")?.addEventListener("click", activateNoteMode);
document.querySelectorAll('[data-chat-action="bartender"]').forEach((button) => {
    button.addEventListener("click", activateBartender);
});
document.getElementById("emoji-trigger")?.addEventListener("click", toggleEmojiPicker);
document.querySelectorAll("#emoji-picker [data-emoji]").forEach((button) => {
    button.addEventListener("click", () => insertEmoji(button.dataset.emoji));
});
document.getElementById("cancel-note-message")?.addEventListener("click", clearNoteMode);
document.getElementById("cancel-message-delete")?.addEventListener("click", closeDeleteMessageDialog);
document.getElementById("confirm-message-delete")?.addEventListener("click", () => {
    if (pendingDeletionMessageId) {
        deleteMessage(pendingDeletionMessageId);
    }
});
document.getElementById("delete-message-modal")?.addEventListener("click", (event) => {
    if (event.target.id === "delete-message-modal") {
        closeDeleteMessageDialog();
    }
});
document.getElementById("cancel-message-report")?.addEventListener("click", closeReportMessageDialog);
document.getElementById("confirm-message-report")?.addEventListener("click", submitMessageReport);
document.getElementById("report-message-modal")?.addEventListener("click", (event) => {
    if (event.target.id === "report-message-modal") closeReportMessageDialog();
});
document.getElementById("bartender-public")?.addEventListener("click", () => setBartenderVisibility(false));
document.getElementById("bartender-private")?.addEventListener("click", () => setBartenderVisibility(true));
document.getElementById("cancel-bartender-message")?.addEventListener("click", clearBartenderMode);
const chatInput = document.getElementById("chat-message-input");
chatInput?.addEventListener("input", () => {
    updateInputSize();
    scheduleTyping();
});
chatInput?.addEventListener("blur", stopTyping);
chatInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
    if (event.key === "Escape") {
        closeDeleteMessageDialog();
        closeReportMessageDialog();
        clearDirectRecipient(false);
        clearBartenderMode(false);
        clearNoteMode(false);
        chatInput.focus();
    }
});
document.getElementById("scroll-to-latest")?.addEventListener("click", () => {
    const chatLog = document.getElementById("chat-log");
    if (chatLog) {
        scrollToLatestAfterLayout(chatLog, true);
    }
    document.getElementById("scroll-to-latest")?.classList.add("hidden");
});
window.addEventListener("pagehide", releaseAudioStream);
document.getElementById("chat-log")?.addEventListener("scroll", (event) => {
    document
        .getElementById("scroll-to-latest")
        ?.classList.toggle("hidden", isNearBottom(event.currentTarget));
});
document.getElementById("message-sound-toggle")?.addEventListener("click", async () => {
    const enabled = !isMessageSoundEnabled();
    setMessageSoundEnabled(enabled);
    if (enabled) {
        await playMessageSound().catch(() => false);
    }
});
updateMessageSoundControl();
document.addEventListener("click", (event) => {
    if (!event.target.closest("#emoji-picker, #emoji-trigger")) {
        document.getElementById("emoji-picker")?.classList.add("hidden");
        document.getElementById("emoji-trigger")?.setAttribute("aria-expanded", "false");
    }
});

initialiseAtmosphere();
updateInputSize();
