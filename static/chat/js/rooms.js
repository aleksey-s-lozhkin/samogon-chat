const membersCount = document.getElementById("private-room-members-count");

function updatePrivateRoomMembersCount() {
    if (!membersCount) {
        return;
    }

    const selected = document.querySelectorAll(
        'input[name="members"]:checked',
    ).length;
    membersCount.textContent = `${selected} из 2`;
}

document.querySelectorAll('input[name="members"]').forEach((checkbox) => {
    checkbox.addEventListener("change", updatePrivateRoomMembersCount);
});

updatePrivateRoomMembersCount();
