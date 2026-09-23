const notesSearchControls = document.getElementById("notes-search-controls");
const notesSearchInput = document.getElementById("notes-search-input");
const notesSearchStatus = document.getElementById("notes-search-status");
const notesSearchEmpty = document.getElementById("notes-search-empty");

if (notesSearchControls && notesSearchInput && notesSearchStatus && notesSearchEmpty) {
    const notes = [...document.querySelectorAll(".notes-list .note-card")].map((card) => {
        const searchable = [
            card.querySelector(".note-source")?.textContent,
            card.querySelector("p")?.textContent,
            ...[...card.querySelectorAll(".note-attachment-file span")].map((item) => item.textContent),
            ...[...card.querySelectorAll(".note-attachment-image img")].map((item) => item.alt),
            ...[...card.querySelectorAll(".note-attachment-audio")].map((item) => item.getAttribute("aria-label")),
        ].filter(Boolean).join(" ").toLocaleLowerCase("ru");
        return { card, searchable };
    });

    const updateSearch = () => {
        const terms = notesSearchInput.value.trim().toLocaleLowerCase("ru").split(/\s+/).filter(Boolean);
        let visible = 0;

        for (const note of notes) {
            const matches = terms.every((term) => note.searchable.includes(term));
            note.card.hidden = !matches;
            if (matches) visible += 1;
        }

        notesSearchEmpty.hidden = terms.length === 0 || visible > 0;
        notesSearchStatus.textContent = terms.length
            ? `Найдено: ${visible} из ${notes.length}`
            : `Всего заметок: ${notes.length}`;
    };

    notesSearchControls.hidden = false;
    notesSearchInput.addEventListener("input", updateSearch);
    updateSearch();
}
