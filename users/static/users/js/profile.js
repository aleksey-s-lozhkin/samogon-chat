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