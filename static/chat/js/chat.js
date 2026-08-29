const { isAuthenticated, roomSlug, username: currentUsername } = chatConfig;
const MESSAGE_MAX_LENGTH = 1000;
const BARTENDER_USERNAME = "Семён";

let chatSocket = null;
let directRecipient = null;
let bartenderMode = false;
let bartenderPrivate = false;
let loadingHistory = false;
let lastMessageDay = null;
let presenceUsers = [];
let presenceOnlineUsers = [];
const expandedPresenceLists = new Set();
const PRESENCE_PREVIEW_LIMIT = 6;

const TAGLINES = [
    "Семён протирает стакан и слушает логи.",
    "Здесь баги разбирают по душам.",
    "Заходите с вопросом, выходите с планом.",
    "Связь есть. Наливаю первую тему.",
    "У стойки спорят о табах и мирятся на пробелах.",
];
const COMPOSER_HINTS = [
    "Скажите что-нибудь у стойки…",
    "Опишите баг — Семён нальёт контекст…",
    "Есть идея? Ставьте её на стойку…",
    "Код, вопрос или тост за удачный деплой…",
];
let taglineIndex = 0;
let composerHintIndex = 0;

if (isAuthenticated) {
    connectWebSocket();
}

function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws/chat/${encodeURIComponent(roomSlug)}/`;

    chatSocket = new WebSocket(url);
    chatSocket.onmessage = ({ data }) => handleServerEvent(JSON.parse(data));
    chatSocket.onclose = ({ code }) => console.log("WebSocket closed:", code);
    chatSocket.onerror = (error) => console.error("WebSocket error:", error);
}

function handleServerEvent(data) {
    if (data.type === "history") {
        loadingHistory = true;
        lastMessageDay = null;
        data.messages.forEach(addMessage);
        loadingHistory = false;
        finishHistoryLoading();
    }

    if (data.type === "message") {
        if (!data.room_slug || data.room_slug === roomSlug) {
            addMessage(data);
        } else {
            increaseUnreadCount(data);
        }
    }

    if (data.type === "user_presence") {
        updateUserPresence(data.users, data.online);
    }

    if (data.type === "error") {
        setBartenderTyping(false);
        showError(data.message);
    }
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
    chatLog.scrollTop = chatLog.scrollHeight;
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
    const onlineUsers = new Set(online.map(normalizeUsername));
    const contacts = users.filter(
        (username) => normalizeUsername(username) !== normalizeUsername(currentUsername),
    );

    renderUserList(
        "online-users-list",
        contacts.filter((username) => onlineUsers.has(normalizeUsername(username))),
        "online-user",
        "Сейчас вы один у стойки",
        "online-users-count",
        "toggle-online-users",
    );
    renderUserList(
        "offline-users-list",
        contacts.filter((username) => !onlineUsers.has(normalizeUsername(username))),
        "offline-user",
        "Все сейчас в баре",
        "offline-users-count",
        "toggle-offline-users",
    );
}

function normalizeUsername(username) {
    return String(username || "").trim().toLocaleLowerCase();
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

    const isExpanded = expandedPresenceLists.has(containerId);
    const visibleUsers = isExpanded
        ? usernames
        : usernames.slice(0, PRESENCE_PREVIEW_LIMIT);
    container.replaceChildren(
        ...visibleUsers.map((username) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `online-user user-contact ${className}`;
            const avatar = document.createElement("span");
            avatar.className = "user-avatar";
            avatar.textContent = username.slice(0, 1).toUpperCase();
            const name = document.createElement("span");
            name.textContent = `@${username}`;
            button.append(avatar, name);
            button.addEventListener("click", () => setDirectRecipient(username));
            return button;
        }),
    );

    if (!toggle) {
        return;
    }
    toggle.classList.toggle("hidden", usernames.length <= PRESENCE_PREVIEW_LIMIT);
    toggle.textContent = isExpanded
        ? "Свернуть список"
        : `Показать всех (${usernames.length})`;
}

function togglePresenceList(containerId) {
    if (expandedPresenceLists.has(containerId)) {
        expandedPresenceLists.delete(containerId);
    } else {
        expandedPresenceLists.add(containerId);
    }
    updateUserPresence(presenceUsers, presenceOnlineUsers);
}

function setDirectRecipient(username) {
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

function activateBartender() {
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
    chatLog.querySelector(".chat-empty-state")?.remove();
    const timestamp = data.timestamp || data.created_at;
    appendDayDivider(chatLog, timestamp);

    const message = document.createElement("div");
    message.className = "message";
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

    const author = document.createElement("div");
    author.className = "message-username";
    author.textContent = data.username === BARTENDER_USERNAME
        ? BARTENDER_USERNAME
        : data.private && data.username === currentUsername
        ? `Вы → @${data.recipient}`
        : `@${data.username}`;

    const text = document.createElement("div");
    text.className = "message-text";
    text.textContent = data.message;

    const time = document.createElement("div");
    time.className = "message-time";
    if (timestamp) {
        time.textContent = new Date(timestamp).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    content.append(author, text, time);
    message.append(content);
    chatLog.append(message);

    if (data.username === BARTENDER_USERNAME) {
        setBartenderTyping(false);
    }

    if (loadingHistory || wasNearBottom) {
        chatLog.scrollTop = chatLog.scrollHeight;
    } else if (!message.classList.contains("own")) {
        document.getElementById("scroll-to-latest")?.classList.remove("hidden");
    }
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

function showError(message) {
    const errorElement = document.getElementById("error-message");
    if (!errorElement) {
        return;
    }

    errorElement.textContent = message;
    window.setTimeout(() => {
        errorElement.textContent = "";
    }, 3000);
}

function sendMessage() {
    const input = document.getElementById("chat-message-input");
    const message = input?.value.trim();
    if (!message) {
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

    chatSocket.send(JSON.stringify({
        message,
        recipient: directRecipient,
        bartender_private: bartenderMode && bartenderPrivate,
    }));
    if (bartenderMode || isBartenderRequest(message)) {
        setBartenderTyping(true);
    }
    input.value = bartenderMode ? `@${BARTENDER_USERNAME} ` : "";
    updateInputSize();
    input.focus();
}

function isBartenderRequest(message) {
    return message.toLocaleLowerCase().startsWith("@семён")
        || message.toLocaleLowerCase().startsWith("@семен");
}

function setBartenderTyping(isTyping) {
    document.getElementById("typing-indicator")?.classList.toggle("hidden", !isTyping);
}

function updateInputSize() {
    const input = document.getElementById("chat-message-input");
    const counter = document.getElementById("message-char-count");
    if (!input || !counter) {
        return;
    }

    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
    counter.textContent = `${input.value.length} / ${MESSAGE_MAX_LENGTH}`;
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

document.getElementById("chat-message-submit")?.addEventListener("click", sendMessage);
document.getElementById("cancel-direct-message")?.addEventListener("click", clearDirectRecipient);
document.getElementById("bartender-card")?.addEventListener("click", activateBartender);
document.getElementById("bartender-public")?.addEventListener("click", () => setBartenderVisibility(false));
document.getElementById("bartender-private")?.addEventListener("click", () => setBartenderVisibility(true));
document.getElementById("cancel-bartender-message")?.addEventListener("click", clearBartenderMode);
const chatInput = document.getElementById("chat-message-input");
chatInput?.addEventListener("input", updateInputSize);
chatInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
    if (event.key === "Escape") {
        clearDirectRecipient(false);
        clearBartenderMode(false);
        chatInput.focus();
    }
});
document.getElementById("scroll-to-latest")?.addEventListener("click", () => {
    const chatLog = document.getElementById("chat-log");
    if (chatLog) {
        chatLog.scrollTop = chatLog.scrollHeight;
    }
    document.getElementById("scroll-to-latest")?.classList.add("hidden");
});
document.getElementById("toggle-online-users")?.addEventListener("click", () => {
    togglePresenceList("online-users-list");
});
document.getElementById("toggle-offline-users")?.addEventListener("click", () => {
    togglePresenceList("offline-users-list");
});

initialiseAtmosphere();
updateInputSize();
