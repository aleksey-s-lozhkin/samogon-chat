const { isAuthenticated, roomSlug, username: currentUsername } = chatConfig;
const MESSAGE_MAX_LENGTH = 1000;

let chatSocket = null;
let directRecipient = null;

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
        data.messages.forEach(addMessage);
    }

    if (data.type === "message") {
        addMessage(data);
    }

    if (data.type === "user_presence") {
        updateUserPresence(data.users, data.online);
    }

    if (data.type === "error") {
        showError(data.message);
    }
}

function updateUserPresence(users, online) {
    const onlineUsers = new Set(online);
    const contacts = users.filter((username) => username !== currentUsername);

    renderUserList(
        "online-users-list",
        contacts.filter((username) => onlineUsers.has(username)),
        "online-user",
        "Сейчас вы один у стойки",
    );
    renderUserList(
        "offline-users-list",
        contacts.filter((username) => !onlineUsers.has(username)),
        "offline-user",
        "Все сейчас в баре",
    );
}

function renderUserList(containerId, usernames, className, emptyText) {
    const container = document.getElementById(containerId);
    if (!container) {
        return;
    }

    if (!usernames.length) {
        const empty = document.createElement("span");
        empty.className = "users-empty";
        empty.textContent = emptyText;
        container.replaceChildren(empty);
        return;
    }

    container.replaceChildren(
        ...usernames.map((username) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `online-user user-contact ${className}`;
            button.textContent = `@${username}`;
            button.addEventListener("click", () => setDirectRecipient(username));
            return button;
        }),
    );
}

function setDirectRecipient(username) {
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

function clearDirectRecipient() {
    directRecipient = null;

    const banner = document.getElementById("direct-recipient");
    const input = document.getElementById("chat-message-input");
    banner?.classList.add("hidden");
    if (input) {
        input.placeholder = "Написать сообщение...";
        input.focus();
    }
}

function addMessage(data) {
    const chatLog = document.getElementById("chat-log");
    if (!chatLog) {
        return;
    }

    const message = document.createElement("div");
    message.className = "message";
    if (data.private) {
        message.classList.add("private");
    }
    if (data.username === currentUsername) {
        message.classList.add("own");
    }
    if (["amber", "blue", "sage", "plum"].includes(data.color)) {
        message.dataset.color = data.color;
    }

    const content = document.createElement("div");
    content.className = "message-content";

    const author = document.createElement("div");
    author.className = "message-username";
    author.textContent = data.private && data.username === currentUsername
        ? `Вы → @${data.recipient}`
        : `@${data.username}`;

    const text = document.createElement("div");
    text.className = "message-text";
    text.textContent = data.message;

    const time = document.createElement("div");
    time.className = "message-time";
    const timestamp = data.timestamp || data.created_at;
    if (timestamp) {
        time.textContent = new Date(timestamp).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    content.append(author, text, time);
    message.append(content);
    chatLog.append(message);
    chatLog.scrollTop = chatLog.scrollHeight;
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

    chatSocket.send(JSON.stringify({ message, recipient: directRecipient }));
    input.value = "";
    input.focus();
}

document.getElementById("chat-message-submit")?.addEventListener("click", sendMessage);
document.getElementById("cancel-direct-message")?.addEventListener("click", clearDirectRecipient);
document.getElementById("chat-message-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        sendMessage();
    }
});
