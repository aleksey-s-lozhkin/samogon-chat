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
        form.querySelectorAll(".field-error, .form-error").forEach((error) => {
            error.textContent = "";
        });
        form.querySelectorAll("[aria-invalid]").forEach((field) => {
            field.removeAttribute("aria-invalid");
        });
        form.dataset.submitting = "true";
        button.dataset.defaultText = button.textContent;
        button.textContent = button.dataset.pendingText;
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
    });
});

function finishAuthRequest(form) {
    const button = form.querySelector('button[type="submit"]');
    if (button) {
        button.textContent = button.dataset.defaultText || button.textContent;
        button.disabled = false;
        button.removeAttribute("aria-busy");
    }
    delete form.dataset.submitting;
}

function showAuthRequestError(form, status) {
    const error = form.querySelector(".form-error");
    if (!error) return;
    const messages = {
        400: form.id === "register-form"
            ? "Не удалось подтвердить проверку безопасности. Дождитесь новой проверки и повторите отправку."
            : "Не удалось войти. Проверьте логин и пароль.",
        403: "Сессия страницы устарела. Обновите страницу и попробуйте снова.",
        429: "Слишком много попыток. Подождите минуту и попробуйте снова.",
    };
    error.textContent = messages[status] || (status >= 500
        ? "Сервер временно недоступен. Попробуйте позже."
        : "Не удалось связаться с сервером. Проверьте соединение и попробуйте снова.");
    error.setAttribute("tabindex", "-1");
    error.focus({ preventScroll: true });
}

function resetRegistrationChallenge(form) {
    const widget = form.querySelector(".cf-turnstile");
    if (widget && window.turnstile) {
        try {
            window.turnstile.reset(widget);
        } catch (_error) {
            window.handleTurnstileError?.();
        }
    }
}

document.body.addEventListener("htmx:afterRequest", (event) => {
    const form = event.detail.elt;
    if (!form?.matches?.("#login-form, #register-form")) return;
    finishAuthRequest(form);
    const xhr = event.detail.xhr;
    if (xhr?.getResponseHeader("HX-Redirect")) return;
    if (!xhr || xhr.status === 0 || xhr.status >= 400) {
        showAuthRequestError(form, xhr?.status || 0);
    }
    // Even a 200 validation error has consumed the one-use Turnstile token.
    if (form.id === "register-form") resetRegistrationChallenge(form);
});

for (const eventName of ["htmx:sendError", "htmx:timeout"]) {
    document.body.addEventListener(eventName, (event) => {
        const form = event.detail.elt;
        if (!form?.matches?.("#login-form, #register-form")) return;
        finishAuthRequest(form);
        showAuthRequestError(form, 0);
    });
}

document.body.addEventListener("htmx:afterSwap", (event) => {
    if (!event.detail.target?.matches?.("#login-error, #register-error")) return;
    if (!event.detail.target.textContent.trim()) return;
    const form = event.detail.target.closest("form");
    if (event.detail.target.id === "login-error") {
        form?.querySelectorAll('input[name="identifier"], input[name="password"]')
            .forEach((field) => field.setAttribute("aria-invalid", "true"));
        form?.elements.namedItem("identifier")?.focus({ preventScroll: true });
        return;
    }
    const fieldError = Array.from(form?.querySelectorAll(".field-error") || [])
        .find((element) => element.textContent.trim());
    if (fieldError) {
        const fieldName = fieldError.id.replace("register-error-", "");
        const field = form.elements.namedItem(fieldName);
        field?.setAttribute("aria-invalid", "true");
        field?.focus({ preventScroll: true });
        window.requestAnimationFrame(() => syncRegistrationFieldErrors(form));
        return;
    }
    event.detail.target.setAttribute("tabindex", "-1");
    event.detail.target.focus({ preventScroll: true });
});

function syncRegistrationFieldErrors(form) {
    form?.querySelectorAll(".field-error[id]").forEach((error) => {
        const fieldName = error.id.replace("register-error-", "");
        const field = form.elements.namedItem(fieldName);
        if (!field) return;
        if (error.textContent.trim()) {
            field.setAttribute("aria-invalid", "true");
        } else {
            field.removeAttribute("aria-invalid");
        }
    });
}

document.querySelectorAll("#login-form input, #register-form input").forEach((field) => {
    field.addEventListener("input", () => {
        field.removeAttribute("aria-invalid");
        const describedIds = (field.getAttribute("aria-describedby") || "").split(/\s+/);
        describedIds.forEach((id) => {
            const error = document.getElementById(id);
            if (error?.matches(".field-error, .form-error")) {
                error.textContent = "";
                if (error.matches(".form-error")) {
                    error.closest("form")
                        ?.querySelectorAll(`[aria-describedby~="${id}"]`)
                        .forEach((relatedField) => {
                            relatedField.removeAttribute("aria-invalid");
                        });
                }
            }
        });
    });
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
