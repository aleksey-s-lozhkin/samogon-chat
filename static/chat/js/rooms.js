const membersCount = document.getElementById("private-room-members-count");

function updatePrivateRoomMembersCount() {
    if (!membersCount) {
        return;
    }

    const selected = document.querySelectorAll(
        'input[name="members"]:checked',
    ).length;
    membersCount.textContent = `Выбрано: ${selected}`;
}

document.querySelectorAll('input[name="members"]').forEach((checkbox) => {
    checkbox.addEventListener("change", updatePrivateRoomMembersCount);
});

updatePrivateRoomMembersCount();

document.querySelectorAll("[data-confirm]").forEach((button) => {
    button.addEventListener("click", (event) => {
        if (!window.confirm(button.dataset.confirm)) {
            event.preventDefault();
        }
    });
});
