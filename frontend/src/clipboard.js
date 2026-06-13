/**
 * Clipboard helpers that survive async fetches and insecure contexts.
 *
 * Two pitfalls this module works around:
 *   1. iOS Safari (and some others) reject navigator.clipboard.writeText() if
 *      it is called after an `await` — the click's transient user activation is
 *      gone by then.  copyDeferred() avoids this by handing the clipboard a
 *      promise-backed ClipboardItem, which registers the write synchronously
 *      inside the gesture and lets the text arrive later.
 *   2. navigator.clipboard is undefined in insecure contexts (e.g. viewing the
 *      digest over http://<lan-ip>:8000 from a phone).  Both helpers fall back
 *      to a hidden-textarea + execCommand('copy').
 */

function _execCommandCopy(text) {
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.top = '0'
  ta.style.left = '0'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.focus()
  ta.select()
  try {
    document.execCommand('copy')
  } finally {
    document.body.removeChild(ta)
  }
}

/**
 * Copy already-available text. Use when the string is in memory (e.g. a deep
 * dive that has already been generated).
 */
export async function copyText(text) {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return
  }
  _execCommandCopy(text)
}

/**
 * Copy text that must first be fetched. `getText` is a function returning a
 * Promise<string>. The clipboard write is initiated synchronously within the
 * click handler so it is not blocked by loss of user activation after the
 * fetch resolves.
 */
export async function copyDeferred(getText) {
  if (
    typeof ClipboardItem !== 'undefined' &&
    navigator.clipboard?.write &&
    window.isSecureContext
  ) {
    const item = new ClipboardItem({
      'text/plain': Promise.resolve()
        .then(getText)
        .then(text => new Blob([text], { type: 'text/plain' })),
    })
    await navigator.clipboard.write([item])
    return
  }
  // Fallback: fetch first, then copy. Works on desktop Chromium and over
  // insecure LAN origins via execCommand.
  const text = await getText()
  await copyText(text)
}
