async function refreshStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();

    const statTasks = document.getElementById('stat-tasks');
    const statEmails = document.getElementById('stat-emails');
    const statEvents = document.getElementById('stat-events');

    if (statTasks)  statTasks.textContent  = data.task_count;
    if (statEmails) statEmails.textContent = data.email_count;
    if (statEvents) statEvents.textContent = data.event_count;

    const dots = document.querySelectorAll('.integration-dots .dot');
    dots.forEach(dot => {
      const key = dot.dataset.key;
      if (key && data.integrations) {
        const ok = data.integrations[key];
        dot.className = 'dot ' + (ok ? 'dot-ok' : 'dot-error');
      }
    });
  } catch (err) {
    console.warn('Status poll failed:', err);
  }
}

refreshStatus();
setInterval(refreshStatus, 10000);
