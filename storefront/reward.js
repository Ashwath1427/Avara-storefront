(() => {
  const style = document.createElement('style');
  style.textContent = `
    .secret-lock{border:1px solid var(--line);border-radius:50%;background:transparent;color:var(--muted);width:34px;height:34px;cursor:pointer;margin:12px;vertical-align:middle}.secret-lock:hover,.secret-lock:focus-visible{color:var(--cyan);border-color:var(--cyan)}
    #reward-dialog{width:min(540px,calc(100% - 28px));max-height:90vh;overflow:auto;background:var(--panel);color:var(--text);border:1px solid var(--cyan);border-radius:22px;padding:28px;box-shadow:0 20px 100px #0009}#reward-dialog::backdrop{background:#020610d9;backdrop-filter:blur(8px)}.reward-top{display:flex;justify-content:space-between;align-items:center;gap:12px}.reward-close{background:transparent;border:1px solid var(--line);color:var(--text);border-radius:8px;padding:8px 12px;cursor:pointer}.lock-seal{font-size:44px;margin:12px 0;text-align:center;color:var(--cyan)}.reward-clues{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:0;list-style-position:inside}.reward-clues li{border:1px solid var(--line);border-radius:10px;padding:10px;font:13px/1.7 monospace}.reward-code{display:block;font:700 clamp(13px,3vw,18px)/1.6 monospace;overflow-wrap:anywhere;color:var(--cyan);border:1px dashed var(--cyan);border-radius:12px;padding:16px;margin:20px 0}.reward-receipt{border:1px solid var(--line);border-radius:14px;padding:18px;margin:18px 0}.reward-actions{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:18px}#reward-pin{font:24px monospace;letter-spacing:.3em;text-align:center}#reward-feedback{min-height:24px;color:#ffe1a6}#reward-dialog [hidden]{display:none!important}@media(max-width:420px){.reward-clues{grid-template-columns:repeat(2,1fr)}}`;
  document.head.append(style);
  const entry = document.createElement('button');
  entry.type = 'button'; entry.className = 'secret-lock'; entry.id = 'discover-reward';
  entry.textContent = '\uD83D\uDD12'; entry.setAttribute('aria-label', 'Open the sealed number lock'); entry.title = 'A sealed lock';
  document.querySelector('footer').append(entry);
  const dialog = document.createElement('dialog'); dialog.id = 'reward-dialog'; dialog.setAttribute('aria-labelledby', 'reward-title');
  dialog.innerHTML = `
    <div class="reward-top"><span class="eyebrow">YOU FOUND SOMETHING</span><button type="button" class="reward-close" id="reward-close">Close</button></div>
    <div class="lock-seal" aria-hidden="true">&#128274;</div><h2 id="reward-title">The sealed number lock</h2>
    <p class="fine">Solve the six clues, then enter the digits in order. Each combination opens once, for the first correct answer. One reward per account.</p>
    <form id="reward-form" hidden><ol class="reward-clues" id="reward-clues"></ol><label for="reward-pin">Six-digit combination</label><input id="reward-pin" inputmode="numeric" autocomplete="off" pattern="[0-9]{6}" maxlength="6" minlength="6" required placeholder="000000"><div class="reward-actions"><button class="btn" id="reward-unlock" type="submit">Break the seal</button></div></form>
    <section id="reward-receipt" class="reward-receipt" hidden aria-label="Reward claim receipt"><div class="eyebrow">AVARA BASIC &middot; 1 MONTH FREE</div><h3>You opened the lock!</h3><code class="reward-code" id="reward-code"></code><p id="reward-account" class="fine"></p><p id="reward-date" class="fine"></p><p><strong>To claim:</strong> take a screenshot of this entire receipt and email it with your code from the account email shown above.</p><p id="reward-contact"></p><p class="fine">Pending owner verification and approval. Your free month starts when the owner activates Basic. This code is tied to your account and cannot grant another reward.</p><div class="reward-actions"><a class="btn" id="reward-email">Email screenshot to claim</a><button class="reward-close" id="reward-copy" type="button">Copy code</button></div><p class="fine">Attach your screenshot in your email app before sending. No email is sent automatically. Reopen this lock any time to recover your receipt.</p></section>
    <p id="reward-feedback" role="status" aria-live="polite"></p><button type="button" class="reward-close" id="reward-refresh">Reload lock / saved receipt</button>`;
  document.body.append(dialog);
  const el = id => document.getElementById(id);
  let lockId = null;
  const feedback = message => { el('reward-feedback').textContent = message; };
  function show(data) {
    const claim = data.claim;
    el('reward-form').hidden = !!claim; el('reward-receipt').hidden = !claim; el('reward-pin').value = '';
    if (claim) {
      lockId = null;
      el('reward-code').textContent = claim.code;
      el('reward-account').textContent = 'Account: ' + claim.account_email;
      el('reward-date').textContent = 'Issued: ' + new Date(claim.created * 1000).toLocaleString();
      el('reward-contact').textContent = 'Send to: ' + claim.contact_email;
      el('reward-email').href = 'mailto:' + encodeURIComponent(claim.contact_email) + '?subject=' + encodeURIComponent('AVARA Basic free month claim: ' + claim.code) + '&body=' + encodeURIComponent('Hello, I opened the AVARA number lock.\n\nCode: ' + claim.code + '\nAccount: ' + claim.account_email + '\nReward: 1 month of AVARA Basic free\n\nI am attaching my screenshot for verification.');
      feedback('Your claim is saved. Email the screenshot to request approval.');
    } else {
      lockId = data.lock_id; const list = el('reward-clues'); list.replaceChildren();
      (data.clues || []).forEach(clue => { const item = document.createElement('li'); item.textContent = clue; list.append(item); });
      feedback('The seal is intact. Solve each clue to find its digit.'); el('reward-pin').focus();
    }
  }
  async function load() {
    el('reward-form').hidden = true; el('reward-receipt').hidden = true; lockId = null;
    feedback('Checking the seal...'); el('reward-refresh').disabled = true;
    try {
      const response = await fetch('/api/reward', {cache: 'no-store'}); const data = await response.json();
      if (!response.ok) throw new Error(response.status === 401 ? 'Sign in using the account form, then reopen this lock.' : data.detail || 'Could not load the lock.');
      show(data);
    } catch (error) { feedback(error.message || 'Could not reach AVARA. Open this page through its server.'); }
    finally { el('reward-refresh').disabled = false; }
  }
  entry.onclick = () => { dialog.showModal(); load(); };
  el('reward-close').onclick = () => dialog.close(); el('reward-refresh').onclick = load;
  el('reward-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!lockId) return;
    const button = el('reward-unlock'); button.disabled = true; feedback('Checking your combination...');
    try {
      const response = await fetch('/api/reward/unlock', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({lock_id: lockId, pin: el('reward-pin').value})});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Enter exactly six digits.');
      show(data);
    } catch (error) { feedback(error.message || 'Connection interrupted. Reload to check for a saved receipt before retrying.'); }
    finally { button.disabled = false; }
  });
  el('reward-copy').onclick = async () => {
    try { await navigator.clipboard.writeText(el('reward-code').textContent); feedback('Code copied. Include it with your screenshot.'); }
    catch { feedback('Select and copy the code shown on your receipt.'); }
  };
})();
