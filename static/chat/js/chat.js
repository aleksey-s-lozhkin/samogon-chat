const {
    isAuthenticated,
    roomSlug,
    username: currentUsername,
    canModerateMessages,
    attachmentUploadTemplate,
    messageDeleteTemplate,
    noteCreateUrl,
} = chatConfig;
const MESSAGE_MAX_LENGTH = 1000;
const BARTENDER_USERNAME = "Семён";

let chatSocket = null;
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
let pendingDeletionMessageId = null;
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

    if (data.type === "attachments" && data.room_slug === roomSlug) {
        updateMessageAttachments(data.message_id, data.attachments);
    }

    if (data.type === "message_deleted" && data.room_slug === roomSlug) {
        removeMessage(data.message_id);
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
        return { username: user, avatarUrl: null };
    }

    return {
        username: String(user?.username || ""),
        avatarUrl: user?.avatar_url || null,
    };
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

    const isExpanded = expandedPresenceLists.has(containerId);
    const visibleUsers = isExpanded
        ? usernames
        : usernames.slice(0, PRESENCE_PREVIEW_LIMIT);
    container.replaceChildren(
        ...visibleUsers.map((user) => {
            const details = userDetails(user);
            const button = document.createElement("button");
            button.type = "button";
            button.className = `online-user user-contact ${className}`;
            const name = document.createElement("span");
            name.textContent = details.username;
            button.append(createUserAvatar(details), name);
            button.addEventListener("click", () => setDirectRecipient(details.username));
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

function activateNoteMode() {
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

    const author = document.createElement("div");
    author.className = "message-username";
    author.append(createUserAvatar({
        username: data.username,
        avatar_url: data.avatar_url,
    }, "message-author-avatar"));
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
        const save = createMessageAction(
            "message-save-note",
            "Приколоть к личным заметкам",
            "pin",
        );
        save.addEventListener("click", () => saveNote(data.id));
        author.append(save);
    }

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
    // Время должно быть в DOM до вложений: они встают непосредственно перед ним.
    renderMessageAttachments(content, data.attachments || []);
    message.append(content);
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

    if (loadingHistory || wasNearBottom) {
        chatLog.scrollTop = chatLog.scrollHeight;
    } else if (!message.classList.contains("own")) {
        document.getElementById("scroll-to-latest")?.classList.remove("hidden");
    }
}

function updateMessageAttachments(messageId, attachments) {
    const message = document.querySelector(
        `.message[data-message-id="${CSS.escape(String(messageId))}"]`,
    );
    const content = message?.querySelector(".message-content");
    if (content) {
        renderMessageAttachments(content, attachments);
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
    const message = input?.value.trim();
    if (!message) {
        if (selectedAttachments.length) {
            showError("Добавьте короткую подпись к файлам перед отправкой.");
        }
        return;
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

function createMessageAction(className, title, icon) {
    const action = document.createElement("button");
    action.type = "button";
    action.className = `message-action ${className}`;
    action.title = title;
    action.ariaLabel = title;
    action.innerHTML = icon === "trash"
        ? '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 7h16M10 11v6m4-6v6M9 7l1-3h4l1 3m-9 0 1 13h10l1-13" /></svg>'
        : '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m14 4 6 6-4 2-3 6-2-2-4 4-1-1 4-4-2-2 6-3 2-4Z" /></svg>';
    return action;
}

document.getElementById("chat-message-submit")?.addEventListener("click", sendMessage);
document.getElementById("chat-attachment-trigger")?.addEventListener("click", selectAttachments);
document.getElementById("chat-attachment-input")?.addEventListener("change", handleAttachmentSelection);
document.getElementById("cancel-direct-message")?.addEventListener("click", clearDirectRecipient);
document.getElementById("bartender-trigger")?.addEventListener("click", activateBartender);
document.getElementById("note-trigger")?.addEventListener("click", activateNoteMode);
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
        closeDeleteMessageDialog();
        clearDirectRecipient(false);
        clearBartenderMode(false);
        clearNoteMode(false);
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
