
const roomName = chatConfig.roomName;

let chatSocket = null;


// =========================
// Authentication state
// =========================

const isAuthenticated = chatConfig.isAuthenticated;


// =========================
// DOM elements
// =========================

const loginContainer =
    document.getElementById("login-container");

const registerContainer =
    document.getElementById("register-container");

const loginForm =
    document.getElementById("login-form");

const registerForm =
    document.getElementById("register-form");

const showRegisterButton =
    document.getElementById("show-register");

const showLoginButton =
    document.getElementById("show-login");


// =========================
// WebSocket
// =========================

if (isAuthenticated) {
    connectWebSocket();
}


function connectWebSocket() {

    chatSocket = new WebSocket(
        "ws://" +
        window.location.host +
        "/ws/chat/" +
        roomName +
        "/"
    );


    chatSocket.onmessage = function (event) {

        const data = JSON.parse(event.data);


        if (data.type === "history") {
            data.messages.forEach(addMessage);
        }


        if (data.type === "message") {
            addMessage(data);
        }


        if (data.type === "error") {
            showError(data.message);
        }

        if (data.type === "online_users") {
            updateOnlineUsers(data.users);
        }

        if (data.type === "user_status") {
            showSystemMessage(
                data.username,
                data.action
            );
        }

    };


    chatSocket.onclose = function () {
        console.log("WebSocket connection closed");
    };
}

function updateOnlineUsers(users) {

    const container =
        document.getElementById(
            "online-users-list"
        );

    container.innerHTML = "";


    users.forEach(function (username) {

        const element =
            document.createElement("div");

        element.classList.add(
            "online-user"
        );

        element.textContent =
            username;

        container.appendChild(element);
    });
}


// =========================
// Messages
// =========================

function addMessage(data) {

    const chatLog =
        document.getElementById("chat-log");

    const messageElement =
        document.createElement("div");


    const currentUser = chatConfig.username;


    if (data.username === currentUser) {

        messageElement.classList.add(
            "message",
            "own"
        );

    } else {

        messageElement.classList.add(
            "message"
        );
    }


    const content =
        document.createElement("div");

    content.classList.add(
        "message-content"
    );


    const username =
        document.createElement("div");

    username.classList.add("username");

    username.textContent =
        data.username;


    const text =
        document.createElement("div");

    text.classList.add("text");

    text.textContent =
        data.message;


    const time =
        document.createElement("div");

    time.classList.add("time");


    const date =
        new Date(data.created_at);


    time.textContent =
        date.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit"
        });


    content.appendChild(username);
    content.appendChild(text);
    content.appendChild(time);

    messageElement.appendChild(content);

    chatLog.appendChild(messageElement);


    chatLog.scrollTop =
        chatLog.scrollHeight;
}

function showSystemMessage(username, action) {

    const chatLog =
        document.getElementById("chat-log");

    const element =
        document.createElement("div");

    element.classList.add("system-message");

    if (action === "join") {
        element.textContent =
            `🟢 ${username} вошёл в чат`;
    }

    if (action === "leave") {
        element.textContent =
            `🔴 ${username} вышел из чата`;
    }

    chatLog.appendChild(element);

    chatLog.scrollTop =
        chatLog.scrollHeight;
}


function showError(message) {

    const errorElement =
        document.getElementById(
            "error-message"
        );


    errorElement.textContent =
        message;


    setTimeout(() => {

        errorElement.textContent = "";

    }, 3000);
}


// =========================
// Send message
// =========================

function sendMessage() {

    const input =
        document.getElementById(
            "chat-message-input"
        );


    const message =
        input.value.trim();


    if (!message) {
        return;
    }


    if (
        !chatSocket ||
        chatSocket.readyState !== WebSocket.OPEN
    ) {

        showError(
            "WebSocket не подключен"
        );

        return;
    }


    chatSocket.send(
        JSON.stringify({
            message: message
        })
    );


    input.value = "";

    input.focus();
}


document
    .getElementById("chat-message-submit")
    .addEventListener(
        "click",
        sendMessage
    );


document
    .getElementById("chat-message-input")
    .addEventListener(
        "keydown",
        function (event) {

            if (event.key === "Enter") {
                sendMessage();
            }

        }
    );


// =========================
// Login
// =========================

loginForm.addEventListener(
    "submit",
    async function (event) {

        event.preventDefault();


        const username =
            document.getElementById(
                "login-username"
            ).value;


        const password =
            document.getElementById(
                "login-password"
            ).value;


        const errorElement =
            document.getElementById(
                "login-error"
            );


        errorElement.textContent = "";


        const csrfToken =
            document.querySelector(
                "[name=csrfmiddlewaretoken]"
            ).value;


        const response =
            await fetch(
                chatConfig.loginUrl,
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/x-www-form-urlencoded",

                        "X-CSRFToken":
                            csrfToken,
                    },

                    body:
                        new URLSearchParams({
                            username: username,
                            password: password,
                        }),
                }
            );


        const data =
            await response.json();


        if (!response.ok) {

            errorElement.textContent =
                data.error;

            return;
        }


        window.location.reload();
    }
);


// =========================
// Logout
// =========================

const logoutButton =
    document.getElementById(
        "logout-button"
    );


if (logoutButton) {

    logoutButton.addEventListener(
        "click",
        async function () {

            const csrfToken =
                document.querySelector(
                    "[name=csrfmiddlewaretoken]"
                ).value;


            if (chatSocket) {
                chatSocket.close();
            }


            const response =
                await fetch(
                       chatConfig.logoutUrl,
                    {
                        method: "POST",

                        headers: {
                            "X-CSRFToken":
                                csrfToken,
                        },
                    }
                );


            if (response.ok) {
                window.location.reload();
            }
        }
    );
}


// =========================
// Switch to registration
// =========================

showRegisterButton.addEventListener(
    "click",
    function () {

        loginContainer.classList.add(
            "hidden"
        );

        registerContainer.classList.remove(
            "hidden"
        );
    }
);


// =========================
// Switch to login
// =========================

showLoginButton.addEventListener(
    "click",
    function () {

        registerContainer.classList.add(
            "hidden"
        );

        loginContainer.classList.remove(
            "hidden"
        );
    }
);


// =========================
// Registration
// =========================

registerForm.addEventListener(
    "submit",
    async function (event) {

        event.preventDefault();


        const username =
            document.getElementById(
                "register-username"
            ).value;


        const email =
            document.getElementById(
                "register-email"
            ).value;


        const password =
            document.getElementById(
                "register-password"
            ).value;


        const passwordConfirm =
            document.getElementById(
                "register-password-confirm"
            ).value;


        const errorElement =
            document.getElementById(
                "register-error"
            );


        errorElement.textContent = "";


        const csrfToken =
            document.querySelector(
                "[name=csrfmiddlewaretoken]"
            ).value;


        const response =
            await fetch(
                   chatConfig.registerUrl,
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/x-www-form-urlencoded",

                        "X-CSRFToken":
                            csrfToken,
                    },

                    body:
                        new URLSearchParams({
                            username: username,
                            email: email,
                            password: password,
                            password_confirm:
                                passwordConfirm,
                        }),
                }
            );


        const data =
            await response.json();


        if (!response.ok) {

            showRegistrationErrors(
                data.errors
            );

            return;
        }


        window.location.reload();
    }
);


function showRegistrationErrors(errors) {

    const errorElement =
        document.getElementById(
            "register-error"
        );


    const messages = [];


    for (const field in errors) {

        for (const error of errors[field]) {

            messages.push(
                error.message
            );
        }
    }


    errorElement.textContent =
        messages.join(" ");
}
