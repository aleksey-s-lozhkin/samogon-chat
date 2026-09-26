/* One bounded overlay replaces independently sticky day dividers. */
(() => {
    function stackDays(days, activeIndex) {
        return days.slice(Math.max(0, activeIndex - 2), activeIndex + 1).reverse();
    }
    if (typeof module !== "undefined") module.exports = {stackDays};
    if (typeof document === "undefined") return;
    const log = document.getElementById("chat-log");
    if (!log) return;
    const root = document.createElement("nav");
    root.className = "date-navigation";
    root.ariaLabel = "Переход к дате загруженной истории";
    root.hidden = true;
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "date-stack";
    trigger.ariaExpanded = "false";
    trigger.ariaControls = "date-options";
    const menu = document.createElement("div");
    menu.className = "date-options";
    menu.id = "date-options";
    menu.hidden = true;
    root.append(trigger, menu);
    log.parentElement.append(root);
    let days = [];
    let activeKey = "";
    let frame = null;
    function expand(open, focus = false) {
        menu.hidden = !open;
        trigger.ariaExpanded = String(open);
        if (open && focus) menu.querySelector('[aria-current="date"]')?.focus();
    }
    function refreshDays() {
        days = [...log.querySelectorAll(".day-divider")].filter((day, i, all) =>
            all.findIndex(other => other.dataset.dayKey === day.dataset.dayKey) === i);
        const previousFocus = menu.contains(document.activeElement) ? document.activeElement.dataset.dayKey : null;
        menu.replaceChildren(...days.map(day => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = day.textContent;
            button.dataset.dayKey = day.dataset.dayKey;
            button.addEventListener("click", () => {
                expand(false);
                log.scrollTop += day.getBoundingClientRect().top - log.getBoundingClientRect().top - 56;
                trigger.focus({preventScroll: true});
                schedule();
            });
            return button;
        }));
        if (previousFocus) [...menu.children].find(el => el.dataset.dayKey === previousFocus)?.focus({preventScroll: true});
        activeKey = "";
        schedule();
    }
    function render() {
        frame = null;
        const edge = log.getBoundingClientRect().top + 8;
        let index = -1;
        for (let i = 0; i < days.length; i++) {
            if (days[i].getBoundingClientRect().bottom <= edge) index = i;
            else break;
        }
        root.hidden = index < 0;
        if (index < 0) { expand(false); activeKey = ""; return; }
        const key = days[index].dataset.dayKey;
        if (key === activeKey) return;
        activeKey = key;
        const stack = stackDays(days, index);
        const layers = stack.slice(1).reverse().map((day, i) => {
            const layer = document.createElement("span");
            layer.className = "date-stack-layer";
            layer.style.setProperty("--depth", String(stack.length - i - 1));
            layer.ariaHidden = "true";
            return layer;
        });
        const label = document.createElement("span");
        label.className = "date-stack-label";
        label.textContent = days[index].textContent;
        trigger.ariaLabel = `${label.textContent}. Выбрать дату`;
        trigger.replaceChildren(...layers, label);
        for (const button of menu.children) {
            if (button.dataset.dayKey === key) button.setAttribute("aria-current", "date");
            else button.removeAttribute("aria-current");
        }
    }
    function schedule() { if (frame === null) frame = requestAnimationFrame(render); }
    let suppressClickUntil = 0;
    trigger.addEventListener("click", () => {
        if (performance.now() >= suppressClickUntil) expand(menu.hidden);
    });
    trigger.addEventListener("keydown", event => {
        if (event.key === "ArrowDown") { event.preventDefault(); expand(true, true); }
    });
    root.addEventListener("keydown", event => {
        if (event.key === "Escape") { expand(false); trigger.focus(); }
    });
    document.addEventListener("pointerdown", event => { if (!root.contains(event.target)) expand(false); });
    let start = null;
    trigger.addEventListener("pointerdown", event => {
        start = {x: event.clientX, y: event.clientY};
        trigger.setPointerCapture(event.pointerId);
    });
    trigger.addEventListener("pointerup", event => {
        if (!start) return;
        const dy = event.clientY - start.y;
        const dx = event.clientX - start.x;
        start = null;
        if (Math.abs(dy) >= 30 && Math.abs(dy) > Math.abs(dx)) {
            expand(dy > 0);
            // Suppress the synthetic click after a completed swipe.
            suppressClickUntil = performance.now() + 400;
        }
    });
    trigger.addEventListener("pointercancel", () => { start = null; });
    log.addEventListener("scroll", schedule, {passive: true});
    window.addEventListener("resize", schedule);
    new MutationObserver(refreshDays).observe(log, {childList: true});
    refreshDays();
})();
