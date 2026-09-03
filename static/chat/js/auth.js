const authConfig = window.authConfig;

if (new URLSearchParams(window.location.search).get("auth") === "login") {
    document.getElementById("login-modal")?.classList.remove("hidden");
    document.getElementById("login-username")?.focus();
}

const oauthError = new URLSearchParams(window.location.search).get("oauth_error");
if (oauthError === "email_exists") {
    const loginError = document.getElementById("login-error");
    if (loginError) {
        loginError.textContent = "Этот email уже связан с аккаунтом. Войдите обычным способом, а затем подключите сервис в профиле.";
    }
}
if (oauthError === "email_required") {
    const loginError = document.getElementById("login-error");
    if (loginError) {
        loginError.textContent = "Сервис не передал подтверждённый email. Разрешите доступ к email у провайдера или войдите обычным способом.";
    }
}

function csrfToken() {
    return document.querySelector("[name=csrfmiddlewaretoken]")?.value;
}

document.getElementById("show-register")?.addEventListener("click", () => {
    document.getElementById("login-container").classList.add("hidden");
    document.getElementById("register-container").classList.remove("hidden");
    document.getElementById("register-username")?.focus();
});

document.getElementById("show-login")?.addEventListener("click", () => {
    document.getElementById("register-container").classList.add("hidden");
    document.getElementById("login-container").classList.remove("hidden");
    document.getElementById("login-username")?.focus();
});

document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
        const input = document.getElementById(button.dataset.passwordToggle);
        if (!input) return;
        const reveal = input.type === "password";
        input.type = reveal ? "text" : "password";
        button.textContent = reveal ? "Скрыть" : "Показать";
        button.setAttribute("aria-label", reveal ? "Скрыть пароль" : "Показать пароль");
        button.setAttribute("aria-pressed", String(reveal));
        input.focus({ preventScroll: true });
    });
});

document.querySelectorAll('input[type="password"]').forEach((input) => {
    const warning = input.closest(".form-group")?.querySelector("[data-caps-lock]");
    const updateCapsLock = (event) => {
        warning?.classList.toggle("hidden", !event.getModifierState("CapsLock"));
    };
    input.addEventListener("keydown", updateCapsLock);
    input.addEventListener("keyup", updateCapsLock);
    input.addEventListener("blur", () => warning?.classList.add("hidden"));
});

const registrationPassword = document.getElementById("register-password");
registrationPassword?.addEventListener("input", () => {
    const value = registrationPassword.value;
    document.querySelector('[data-password-rule="length"]')?.classList.toggle("is-met", value.length >= 8);
    document.querySelector('[data-password-rule="numeric"]')?.classList.toggle("is-met", Boolean(value) && !/^\d+$/.test(value));
});

document.querySelectorAll("#login-form, #register-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
        if (form.dataset.submitting === "true") {
            event.preventDefault();
            return;
        }
        const button = form.querySelector('button[type="submit"]');
        if (!button) return;
        form.dataset.submitting = "true";
        button.dataset.defaultText = button.textContent;
        button.textContent = button.dataset.pendingText;
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
    });
});

document.body.addEventListener("htmx:afterRequest", (event) => {
    const form = event.detail.elt;
    if (!form?.matches?.("#login-form, #register-form")) return;
    const button = form.querySelector('button[type="submit"]');
    if (button) {
        button.textContent = button.dataset.defaultText || button.textContent;
        button.disabled = false;
        button.removeAttribute("aria-busy");
    }
    delete form.dataset.submitting;
});

document.body.addEventListener("htmx:afterSwap", (event) => {
    if (!event.detail.target?.matches?.("#login-error, #register-error")) return;
    if (!event.detail.target.textContent.trim()) return;
    const form = event.detail.target.closest("form");
    const fieldError = Array.from(form?.querySelectorAll(".field-error") || [])
        .find((element) => element.textContent.trim());
    if (fieldError) {
        const fieldName = fieldError.id.replace("register-error-", "");
        form.elements.namedItem(fieldName)?.focus({ preventScroll: true });
        return;
    }
    event.detail.target.setAttribute("tabindex", "-1");
    event.detail.target.focus({ preventScroll: true });
});

document.querySelectorAll("[data-open-auth]").forEach((button) => {
    button.addEventListener("click", () => {
        document.getElementById("login-modal")?.classList.remove("hidden");
        document.getElementById("login-username")?.focus();
    });
});

document.getElementById("logout-button")?.addEventListener("click", async () => {
    const response = await fetch(authConfig.logoutUrl, {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken() },
    });

    if (response.ok) {
        window.location.assign("/");
    }
});
