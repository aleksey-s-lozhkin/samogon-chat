let deferredInstallPrompt = null;

const installTrigger = document.getElementById("pwa-install-trigger");
const installDialog = document.getElementById("pwa-install-dialog");
const installClose = document.getElementById("pwa-install-close");
const isIos = /iPad|iPhone|iPod/.test(navigator.userAgent);
const isAndroid = /Android/.test(navigator.userAgent);
const isStandalone = window.matchMedia("(display-mode: standalone)").matches
    || window.navigator.standalone === true;

document.body.classList.toggle("is-ios", isIos);
document.body.classList.toggle("is-android", isAndroid);

if (isStandalone) {
    installTrigger?.remove();
}

window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstallPrompt = event;
});

window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    installDialog?.close();
    installTrigger?.remove();
});

function showInstallInstructions() {
    if (typeof installDialog?.showModal === "function") {
        installDialog.showModal();
    } else {
        installDialog?.setAttribute("open", "");
    }
}

installTrigger?.addEventListener("click", async () => {
    if (!deferredInstallPrompt) {
        showInstallInstructions();
        return;
    }
    deferredInstallPrompt.prompt();
    await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
});

installClose?.addEventListener("click", () => installDialog?.close());
installDialog?.addEventListener("click", (event) => {
    if (event.target === installDialog) {
        installDialog.close();
    }
});
