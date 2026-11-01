/* Keep one live set of controls so closing a dialog preserves current inputs. */
const teachingSummary = document.getElementById('teaching-summary');
function updateTeachingSummary() {
  teachingSummary.textContent = `${document.getElementById('lesson-duration').value || '—'} 分钟 · ${document.getElementById('lesson-type').selectedOptions[0].textContent} · ${document.getElementById('lesson-detail').selectedOptions[0].textContent}`;
}
for (const control of document.querySelectorAll('#lesson-teaching-dialog input, #lesson-teaching-dialog select')) {
  control.addEventListener('input', updateTeachingSummary);
  control.addEventListener('change', updateTeachingSummary);
  control.addEventListener('invalid', () => {
    const dialog = control.closest('dialog');
    if (!dialog.open) dialog.showModal();
  });
}
window.addEventListener('lecture-version-changed', updateTeachingSummary);
updateTeachingSummary();
for (const button of document.querySelectorAll('[data-setting-dialog]')) {
  button.addEventListener('click', () => {
    const dialog = document.getElementById(button.dataset.settingDialog);
    dialog.showModal();
    dialog.querySelector('.dialog-body').scrollTop = 0;
  });
}
for (const dialog of document.querySelectorAll('.lesson-settings-dialog')) {
  for (const button of dialog.querySelectorAll('[data-close-setting]')) {
    button.addEventListener('click', () => dialog.close());
  }
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right ||
        event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  });
}
