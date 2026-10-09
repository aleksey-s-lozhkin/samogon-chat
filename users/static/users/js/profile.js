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
    // Выбор из трёх, а не два флажка: с двумя получалось состояние
    // «разрешить, но ни о чём не сообщать».
    const modes = Array.from(pushSettings.querySelectorAll("[data-push-mode]"));
    const status = pushSettings.querySelector("[data-push-status]");
    const reportButton = pushSettings.querySelector("[data-push-report-copy]");
    const reportStatus = pushSettings.querySelector("[data-push-report-status]");
    const reportOutput = pushSettings.querySelector("[data-push-report-output]");
    const diagnosticValues = {};
    const runtimeValues = {
        workerScope: "unknown",
        workerState: "unknown",
        workerControlsPage: Boolean(navigator.serviceWorker?.controller),
        browserSubscription: "unknown",
        serverSubscription: "unknown",
    };
    const diagnostic = (name, message, isError = false) => {
        const element = pushSettings.querySelector(`[data-push-check="${name}"]`);
        element.textContent = message;
        element.classList.toggle("error", isError);
        diagnosticValues[name] = message;
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
    const modeValue = () =>
        (modes.find((item) => item.checked)?.value ?? "off");
    const setMode = (value) => {
        for (const item of modes) {
            item.checked = item.value === value;
        }
    };
    const disableControls = () => {
        for (const item of modes) {
            item.disabled = true;
        }
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
    const buildDiagnosticReport = () => JSON.stringify({
        report: "Samogon Web Push diagnostics",
        collectedAt: new Date().toISOString(),
        appOrigin: window.location.origin,
        serverConfigured: pushSettings.dataset.enabled === "true",
        secureContext: window.isSecureContext,
        online: navigator.onLine,
        standalone: isStandalone,
        appleMobile: isAppleMobile,
        userAgent: navigator.userAgent,
        platform: navigator.platform || "unknown",
        language: navigator.language || "unknown",
        viewport: `${window.innerWidth}x${window.innerHeight}`,
        screen: `${window.screen.width}x${window.screen.height}`,
        diagnostics: diagnosticValues,
        serviceWorker: {
            supported: hasServiceWorker,
            scope: runtimeValues.workerScope,
            state: runtimeValues.workerState,
            controlsPage: runtimeValues.workerControlsPage,
        },
        push: {
            apiAvailable: hasPushApi,
            permission: "Notification" in window ? Notification.permission : "unavailable",
            browserSubscription: runtimeValues.browserSubscription,
            serverSubscription: runtimeValues.serverSubscription,
        },
    }, null, 2);
    const copyDiagnosticReport = async () => {
        const report = buildDiagnosticReport();
        reportOutput.value = report;
        try {
            await navigator.clipboard.writeText(report);
            reportOutput.hidden = true;
            reportStatus.textContent = "Диагностика скопирована. Пришлите её в чат.";
        } catch (_error) {
            reportOutput.hidden = false;
            reportOutput.focus();
            reportOutput.select();
            reportStatus.textContent = "Автокопирование недоступно. Скопируйте текст из поля ниже.";
        }
    };

    reportButton.addEventListener("click", copyDiagnosticReport);

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
            runtimeValues.workerScope = registration.scope;
            runtimeValues.workerState = registration.active?.state || "no-active-worker";
            runtimeValues.workerControlsPage = Boolean(navigator.serviceWorker.controller);
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
            runtimeValues.browserSubscription = subscription ? "present" : "absent";
            diagnostic("subscription", subscription ? "Подписка найдена" : "Не подписано");
            setMode("off");
            if (subscription) {
                try {
                    const query = new URLSearchParams({endpoint: subscription.endpoint});
                    const response = await fetch(`${pushSettings.dataset.statusUrl}?${query}`);
                    if (response.ok) {
                        const saved = await response.json();
                        setMode(saved.known ? saved.mode : "off");
                        runtimeValues.serverSubscription = saved.known
                            ? (saved.mode === "off" ? "known-disabled" : "known-enabled")
                            : "unknown";
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
        } else if (modeValue() !== "off") {
            setStatus("Уведомления включены для этого устройства.");
        } else if (subscription) {
            setStatus("Уведомления этого браузера не привязаны к текущему аккаунту.");
        }

        const applyMode = async (mode) => {
            disableControls();
            try {
                if (mode !== "off") {
                    subscription = await registration.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: applicationServerKey(pushSettings.dataset.vapidKey),
                    });
                    diagnostic("permission", "Разрешено");
                    await post(pushSettings.dataset.subscribeUrl, {
                        ...subscription.toJSON(), mode,
                    });
                    runtimeValues.browserSubscription = "present";
                    runtimeValues.serverSubscription = "known-enabled";
                    diagnostic("subscription", "Привязано к аккаунту");
                    setStatus(
                        mode === "all"
                            ? "Присылаем всё новое на это устройство."
                            : "Присылаем только личные сообщения."
                    );
                } else if (subscription) {
                    await post(pushSettings.dataset.unsubscribeUrl, {endpoint: subscription.endpoint});
                    await subscription.unsubscribe();
                    subscription = null;
                    runtimeValues.browserSubscription = "absent";
                    runtimeValues.serverSubscription = "unknown";
                    diagnostic("subscription", "Не подписано");
                    setStatus("Уведомления отключены для этого устройства.");
                }
            } catch (_error) {
                diagnostic("permission", Notification.permission === "denied" ? "Запрещено" : "Не разрешено", Notification.permission === "denied");
                setStatus("Не удалось изменить настройку уведомлений.", true);
            } finally {
                if (Notification.permission === "denied") {
                    disableControls();
                } else {
                    for (const item of modes) {
                        item.disabled = false;
                    }
                }
            }
        };

        for (const item of modes) {
            item.addEventListener("change", () => applyMode(item.value));
        }
    };

    // Тихие часы — отдельно от режима: они общие для всех устройств.
    const quietFrom = pushSettings.querySelector("[data-push-quiet-from]");
    const quietTo = pushSettings.querySelector("[data-push-quiet-to]");
    const quietSave = pushSettings.querySelector("[data-push-quiet-save]");
    const quietStatus = pushSettings.querySelector("[data-push-quiet-status]");

    const setQuietStatus = (message, isError = false) => {
        quietStatus.textContent = message;
        quietStatus.classList.toggle("error", isError);
    };

    if (quietSave) {
        quietSave.addEventListener("click", async () => {
            quietSave.disabled = true;
            try {
                const response = await fetch(pushSettings.dataset.quietUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": csrfToken(),
                    },
                    body: JSON.stringify({
                        quietFrom: quietFrom.value,
                        quietTo: quietTo.value,
                        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
                    }),
                });
                const data = await response.json();
                if (!response.ok) {
                    setQuietStatus(data.error || "Не удалось сохранить.", true);
                    return;
                }
                // Сервер отвечает нормализованными значениями: «07» и «7» —
                // одно и то же, и показывать надо то, что сохранилось.
                quietFrom.value = data.quietFrom;
                quietTo.value = data.quietTo;
                setQuietStatus(
                    data.quiet
                        ? "Сохранено. Сейчас тихие часы — уведомления не придут."
                        : "Сохранено."
                );
            } catch (_error) {
                setQuietStatus("Не удалось сохранить тихие часы.", true);
            } finally {
                quietSave.disabled = false;
            }
        });
    }

    initializePush();
}
