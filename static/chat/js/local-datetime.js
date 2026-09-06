document.querySelectorAll("time.local-datetime[datetime]").forEach((element) => {
    const date = new Date(element.dateTime);
    if (Number.isNaN(date.getTime())) {
        return;
    }
    element.textContent = new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    }).format(date);
});
