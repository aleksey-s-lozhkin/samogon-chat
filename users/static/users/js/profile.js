const avatarInput = document.getElementById("id_avatar");
const avatarFileName = document.getElementById("avatar-file-name");

if (avatarInput && avatarFileName) {
    avatarInput.addEventListener("change", () => {
        const file = avatarInput.files[0];

        avatarFileName.textContent = file
            ? file.name
            : "";
    });
}

const pushSettings = document.querySelector("[data-push-settings]");

if (pushSettings) {
    const master = pushSettings.querySelector("[data-push-master]");
    const direct = pushSettings.querySelector("[data-push-direct]");
    const status = pushSettings.querySelector("[data-push-status]");
    const supported = pushSettings.dataset.enabled === "true"
        && "serviceWorker" in navigator
        && "PushManager" in window
        && "Notification" in window;

    const setStatus = (message, isError = false) => {
        status.textContent = message;
        status.classList.toggle("error", isError);
    };
    const csrfToken = () => document.cookie
        .split("; ")
        .find((item) => item.startsWith("csrftoken="))
        ?.split("=")[1] || "";
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

    if (!supported) {
        master.disabled = true;
        direct.disabled = true;
        setStatus(pushSettings.dataset.enabled === "true"
            ? "Этот браузер не поддерживает Web Push."
            : "Уведомления пока не настроены на сервере.");
    } else {
        navigator.serviceWorker.ready.then(async (registration) => {
            let subscription = await registration.pushManager.getSubscription();
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
                    }
                } catch (_error) {
                    // Сбой чтения настройки не должен отключать уже работающую подписку.
                }
            }
            if (Notification.permission === "denied") {
                master.disabled = true;
                direct.disabled = true;
                setStatus("Уведомления запрещены в настройках браузера.", true);
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
                        await post(pushSettings.dataset.subscribeUrl, {
                            ...subscription.toJSON(),
                            enabled: true,
                            directMessages: direct.checked,
                        });
                        direct.disabled = false;
                        setStatus("Уведомления включены для этого устройства.");
                    } else if (subscription) {
                        await post(pushSettings.dataset.unsubscribeUrl, {endpoint: subscription.endpoint});
                        await subscription.unsubscribe();
                        subscription = null;
                        direct.disabled = true;
                        setStatus("Уведомления отключены для этого устройства.");
                    }
                } catch (_error) {
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
                        ...subscription.toJSON(),
                        enabled: true,
                        directMessages: direct.checked,
                    });
                    setStatus(direct.checked
                        ? "Личные уведомления включены."
                        : "Личные уведомления отключены.");
                } catch (_error) {
                    direct.checked = !direct.checked;
                    setStatus("Не удалось сохранить настройку.", true);
                } finally {
                    direct.disabled = false;
                }
            });
        });
    }
}
