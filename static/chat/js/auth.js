const authConfig = window.authConfig;

function csrfToken() {
    return document.querySelector("[name=csrfmiddlewaretoken]")?.value;
}

document.getElementById("show-register")?.addEventListener("click", () => {
    document.getElementById("login-container").classList.add("hidden");
    document.getElementById("register-container").classList.remove("hidden");
});

document.getElementById("show-login")?.addEventListener("click", () => {
    document.getElementById("register-container").classList.add("hidden");
    document.getElementById("login-container").classList.remove("hidden");
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
