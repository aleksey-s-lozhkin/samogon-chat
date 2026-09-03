(() => {
    const storageKey = "samogon-theme";
    const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");

    function applyTheme(mode) {
        const safeMode = ["auto", "dark", "light"].includes(mode) ? mode : "auto";
        const resolved = safeMode === "auto"
            ? (systemTheme.matches ? "dark" : "light")
            : safeMode;

        document.documentElement.dataset.themeMode = safeMode;
        document.documentElement.dataset.theme = resolved;
        document.querySelector('meta[name="theme-color"]')?.setAttribute(
            "content",
            resolved === "dark" ? "#171311" : "#eee4d5",
        );
        document.querySelectorAll(".theme-select").forEach((select) => {
            select.value = safeMode;
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        applyTheme(localStorage.getItem(storageKey) || "auto");
        document.querySelectorAll(".theme-select").forEach((select) => {
            select.addEventListener("change", () => {
                localStorage.setItem(storageKey, select.value);
                applyTheme(select.value);
            });
        });
    });

    systemTheme.addEventListener("change", () => {
        if ((localStorage.getItem(storageKey) || "auto") === "auto") {
            applyTheme("auto");
        }
    });
})();

