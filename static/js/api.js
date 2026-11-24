function getClientId() {
  let cid = localStorage.getItem('xb_client_id');
  if (!cid) {
    cid = crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem('xb_client_id', cid);
  }
  return cid;
}

export async function apiGet(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function apiPost(path, body) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Client-Id': getClientId(),
    },
    body: JSON.stringify(body),
  });
  return resp.json();
}