(() => {
    // Просмотр изображения внутри приложения: вложение не открывается в новой
    // вкладке, где его размер не подогнан под экран.
    const root = document.getElementById("attachment-lightbox");
    if (!root) {
        return;
    }

    const image = root.querySelector("[data-lightbox-target]");
    const caption = root.querySelector("[data-lightbox-caption]");
    const save = root.querySelector("[data-lightbox-save]");
    const dismiss = root.querySelector("button[data-lightbox-close]");
    let lastFocused = null;

    function close() {
        root.classList.add("hidden");
        document.body.classList.remove("has-lightbox");
        image.removeAttribute("src");
        image.removeAttribute("alt");
        if (lastFocused && lastFocused.isConnected) {
            lastFocused.focus();
        }
        lastFocused = null;
    }

    function open(link) {
        lastFocused = document.activeElement;
        const source = link.dataset.lightboxImage;
        image.src = source;
        image.alt = link.dataset.lightboxName || "";
        caption.textContent = link.dataset.lightboxName || "";
        if (save) {
            save.href = link.dataset.lightboxDownload || source;
            save.setAttribute("download", link.dataset.lightboxName || "");
        }
        root.classList.remove("hidden");
        document.body.classList.add("has-lightbox");
        dismiss?.focus();
    }

    document.addEventListener("click", (event) => {
        const link = event.target.closest("[data-lightbox-image]");
        if (link) {
            // При выключенном JavaScript ссылка по-прежнему открывает вложение.
            event.preventDefault();
            open(link);
            return;
        }

        const closer = event.target.closest("[data-lightbox-close]");
        if (closer && root.contains(closer) && !root.classList.contains("hidden")) {
            event.preventDefault();
            close();
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !root.classList.contains("hidden")) {
            event.preventDefault();
            close();
        }
    });
})();
