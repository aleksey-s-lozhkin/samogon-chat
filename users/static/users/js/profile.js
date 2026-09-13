const avatarInput = document.getElementById("id_avatar");
const avatarFileName = document.getElementById("avatar-file-name");

if (avatarInput && avatarFileName) {
    avatarInput.addEventListener("change", () => {
        const file = avatarInput.files[0];
        avatarFileName.textContent = file ? file.name : "";
    });
}

document.querySelectorAll("[data-oauth-disconnect]").forEach((form) => {
    form.addEventListener("submit", (event) => {
        const provider = form.dataset.oauthDisconnect;
        if (!window.confirm(`Отключить вход через ${provider}?`)) event.preventDefault();
    });
});

const pushSettings = document.querySelector("[data-push-settings]");

if (pushSettings) {
    const master = pushSettings.querySelector("[data-push-master]");
    const direct = pushSettings.querySelector("[data-push-direct]");
    const status = pushSettings.querySelector("[data-push-status]");
    const diagnostic = (name, message, isError = false) => {
        const element = pushSettings.querySelector(`[data-push-check="${name}"]`);
        element.textContent = message;
        element.classList.toggle("error", isError);
    };
    const isStandalone = window.matchMedia("(display-mode: standalone)").matches
        || window.navigator.standalone === true;
    const isAppleMobile = /iPhone|iPad|iPod/.test(navigator.userAgent)
        || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    const hasServiceWorker = "serviceWorker" in navigator;
    const hasPushApi = "PushManager" in window && "Notification" in window;

    diagnostic("standalone", isStandalone ? "Да" : "Нет");
    diagnostic("api", hasPushApi ? "Доступен" : "Недоступен", !hasPushApi);
    diagnostic("worker", hasServiceWorker ? "Проверяем…" : "Недоступен", !hasServiceWorker);
    diagnostic("permission", "Notification" in window
        ? {granted: "Разрешено", denied: "Запрещено", default: "Не запрашивалось"}[Notification.permission]
        : "Недоступно", "Notification" in window && Notification.permission === "denied");
    diagnostic("subscription", "Не проверено");

    const setStatus = (message, isError = false) => {
        status.textContent = message;
        status.classList.toggle("error", isError);
    };
    const disableControls = () => {
        master.disabled = true;
        direct.disabled = true;
    };
    const csrfToken = () => document.cookie.split("; ")
        .find((item) => item.startsWith("csrftoken="))?.split("=")[1] || "";
    const applicationServerKey = (value) => {
        const padding = "=".repeat((4 - value.length % 4) % 4);
        const raw = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
        return Uint8Array.from([...raw].map((char) => char.charCodeAt(0)));
    };
    const post = async (url, payload) => {
        const response = await fetch(url, {
            method: "POST",
            headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken()},
            body: JSON.stringify(payload),
        });
        if (!response.ok) throw new Error("Сервер не сохранил настройку.");
    };

    const initializePush = async () => {
        if (pushSettings.dataset.enabled !== "true") {
            disableControls();
            setStatus("Уведомления пока не настроены на сервере.");
            return;
        }
        if (isAppleMobile && !isStandalone) {
            disableControls();
            setStatus("На iPhone и iPad откройте Самогон с экрана «Домой», затем включите уведомления здесь.");
            return;
        }
        if (!hasServiceWorker || !hasPushApi) {
            disableControls();
            setStatus("Web Push недоступен в этом режиме браузера.", true);
            return;
        }

        let registration;
        try {
            registration = await navigator.serviceWorker.getRegistration("/")
                || await navigator.serviceWorker.register("/service-worker.js");
            registration = await navigator.serviceWorker.ready;
            diagnostic("worker", "Зарегистрирован");
        } catch (_error) {
            disableControls();
            diagnostic("worker", "Ошибка регистрации", true);
            setStatus("Не удалось подготовить уведомления. Обновите страницу и попробуйте снова.", true);
            return;
        }

        let subscription;
        try {
            subscription = await registration.pushManager.getSubscription();
            diagnostic("subscription", subscription ? "Подписка найдена" : "Не подписано");
            master.checked = false;
            direct.disabled = true;
            if (subscription) {
                try {
                    const query = new URLSearchParams({endpoint: subscription.endpoint});
                    const response = await fetch(`${pushSettings.dataset.statusUrl}?${query}`);
                    if (response.ok) {
                        const saved = await response.json();
                        master.checked = saved.known && saved.enabled;
                        direct.checked = saved.directMessages;
                        direct.disabled = !master.checked;
                        diagnostic("subscription", saved.known ? "Привязано к аккаунту" : "Не привязано");
                    }
                } catch (_error) {
                    // Временный сбой сервера не должен ломать браузерную подписку.
                }
            }
        } catch (_error) {
            disableControls();
            diagnostic("subscription", "Ошибка проверки", true);
            setStatus("Не удалось проверить подписку этого устройства.", true);
            return;
        }

        if (Notification.permission === "denied") {
            disableControls();
            setStatus("Уведомления запрещены в настройках устройства.", true);
        } else if (master.checked) {
            setStatus("Уведомления включены для этого устройства.");
        } else if (subscription) {
            setStatus("Уведомления этого браузера не привязаны к текущему аккаунту.");
        }

        master.addEventListener("change", async () => {
            master.disabled = true;
            try {
                if (master.checked) {
                    subscription = await registration.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: applicationServerKey(pushSettings.dataset.vapidKey),
                    });
                    diagnostic("permission", "Разрешено");
                    await post(pushSettings.dataset.subscribeUrl, {
                        ...subscription.toJSON(), enabled: true, directMessages: direct.checked,
                    });
                    direct.disabled = false;
                    diagnostic("subscription", "Привязано к аккаунту");
                    setStatus("Уведомления включены для этого устройства.");
                } else if (subscription) {
                    await post(pushSettings.dataset.unsubscribeUrl, {endpoint: subscription.endpoint});
                    await subscription.unsubscribe();
                    subscription = null;
                    direct.disabled = true;
                    diagnostic("subscription", "Не подписано");
                    setStatus("Уведомления отключены для этого устройства.");
                }
            } catch (_error) {
                diagnostic("permission", Notification.permission === "denied" ? "Запрещено" : "Не разрешено", Notification.permission === "denied");
                master.checked = Boolean(subscription) && !direct.disabled;
                setStatus("Не удалось изменить настройку уведомлений.", true);
            } finally {
                master.disabled = Notification.permission === "denied";
            }
        });

        direct.addEventListener("change", async () => {
            if (!subscription) return;
            direct.disabled = true;
            try {
                await post(pushSettings.dataset.subscribeUrl, {
                    ...subscription.toJSON(), enabled: true, directMessages: direct.checked,
                });
                setStatus(direct.checked ? "Личные уведомления включены." : "Личные уведомления отключены.");
            } catch (_error) {
                direct.checked = !direct.checked;
                setStatus("Не удалось сохранить настройку.", true);
            } finally {
                direct.disabled = false;
            }
        });
    };

    initializePush();
}
