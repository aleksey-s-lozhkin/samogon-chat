const decisionForm = document.getElementById('decision-form');
const networkStatus = document.getElementById('network-status');
function updateNetworkStatus() {
    networkStatus.hidden = navigator.onLine;
    if (decisionForm) decisionForm.querySelector('button[type=submit]').disabled = !navigator.onLine;
}
window.addEventListener('online', updateNetworkStatus);
window.addEventListener('offline', updateNetworkStatus);
window.addEventListener('pageshow', updateNetworkStatus);
decisionForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!navigator.onLine) { updateNetworkStatus(); return; }
    const button = decisionForm.querySelector('button[type=submit]');
    button.disabled = true;
    button.textContent = 'Сохраняем…';
    try {
        const response = await fetch(location.href, {method: 'POST', body: new FormData(decisionForm), credentials: 'same-origin', headers: {'Accept': 'application/json'}});
        if (response.ok && response.headers.get('content-type')?.includes('application/json')) {
            const result = await response.json();
            location.replace(result.url);
            return;
        }
        if (response.redirected) { location.replace(response.url); return; }
        if ([400, 409].includes(response.status)) {
            const html = new DOMParser().parseFromString(await response.text(), 'text/html');
            const errors = html.querySelector('#decision-form .errorlist');
            networkStatus.textContent = errors?.textContent || 'Решение не сохранено. Обновите страницу.';
        } else {
            networkStatus.textContent = 'Не удалось сохранить решение. Обновите страницу и проверьте права доступа.';
        }
    } catch (_) {
        networkStatus.textContent = 'Связь прервалась. Результат не подтверждён — обновите страницу перед повтором.';
    }
    networkStatus.hidden = false;
    button.disabled = false;
    button.textContent = 'Применить решение';
});
updateNetworkStatus();

const actionInput = document.getElementById('id_action');
const banDaysInput = document.getElementById('id_ban_days');
function updateBanDays() {
    if (banDaysInput && actionInput) {
        banDaysInput.closest('p').hidden = actionInput.value !== 'ban';
        document.getElementById('id_hide_with_ban').closest('p').hidden = actionInput.value !== 'ban';
    }
}
actionInput?.addEventListener('change', updateBanDays);
updateBanDays();
