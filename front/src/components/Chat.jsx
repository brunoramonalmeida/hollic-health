import { useEffect, useRef, useState } from 'react'
import { api, apiBytes } from '../api'
import {
  decryptBlob,
  decryptMessage,
  encryptBlob,
  encryptMessage,
  generateConversationKey,
  indexedDbStore,
  loadOrCreateIdentity,
  shortFingerprint,
  unwrapKey,
  wrapKeyFor,
} from '../chatCrypto'
import { formatDateTime } from '../dates'
import { useSession } from '../session'

const POLL_MESSAGES_MS = 5000
const POLL_CONVERSATION_MS = 30000
const ACCEPT = 'image/*,application/pdf,text/plain,.doc,.docx,.odt,audio/*,video/mp4'
const INLINE_IMAGE_LIMIT = 3 * 1024 * 1024

function size(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** One attachment: decrypted on demand (images of a reasonable size at once). */
function Attachment({ meta, appointmentId, conversationId, conversationKey }) {
  const [url, setUrl] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const urlRef = useRef(null)
  const isImage = (meta.type || '').startsWith('image/')

  async function open() {
    if (urlRef.current || busy) return urlRef.current
    setBusy(true)
    setError('')
    try {
      const bytes = await apiBytes(`/appointments/${appointmentId}/chat/attachments/${meta.id}`)
      const plain = await decryptBlob(conversationKey, conversationId, bytes, meta.iv)
      const objectUrl = URL.createObjectURL(new Blob([plain], { type: meta.type || 'application/octet-stream' }))
      urlRef.current = objectUrl
      setUrl(objectUrl)
      return objectUrl
    } catch (err) {
      setError(err.message || 'Could not open the attachment')
      return null
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (isImage && meta.size <= INLINE_IMAGE_LIMIT) open()
    return () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta.id])

  async function download() {
    const objectUrl = url || (await open())
    if (!objectUrl) return
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = meta.name || 'attachment'
    link.click()
  }

  return (
    <div className="chat-attachment">
      {isImage && url ? (
        <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={meta.name} /></a>
      ) : null}
      <button type="button" className="ghost small" disabled={busy} onClick={download}>
        {busy ? 'Decrypting…' : `⬇ ${meta.name || 'attachment'}`} <small className="muted">({size(meta.size || 0)})</small>
      </button>
      {error && <small className="error">{error}</small>}
    </div>
  )
}

/**
 * The end-to-end encrypted chat of a remote appointment, for its two
 * parties (the patient's login and the professional's). Everything is
 * encrypted in this browser with keys the server never sees; see chatCrypto.js.
 */
export default function ChatPanel({ appointment }) {
  const session = useSession()
  const [identity, setIdentity] = useState(null)
  const [conversation, setConversation] = useState(null)
  const [conversationKey, setConversationKey] = useState(null)
  const [messages, setMessages] = useState([]) // decrypted: {id, mine, sender_name, created_at, text, attachments, failed}
  const [text, setText] = useState('')
  const [files, setFiles] = useState([])
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('Setting up encryption…')
  const lastId = useRef(0)
  const keyRef = useRef(null)
  const listRef = useRef(null)
  const fileInput = useRef(null)
  const store = useRef(null)

  if (!store.current) store.current = typeof indexedDB !== 'undefined' ? indexedDbStore() : null

  const base = `/appointments/${appointment.id}/chat`

  /** Make sure every participant with a public key has an envelope, when I hold the key. */
  async function shareKeys(view, key) {
    const pending = view.participants.filter((person) => person.public_key && !person.has_key)
    if (!pending.length) return view
    const keys = await Promise.all(pending.map((person) => wrapKeyFor(key, person.public_key)))
    return api(`${base}/keys`, { method: 'POST', body: { keys }, silent: true, message: false })
  }

  async function pullMessages(key, conversationId) {
    const rows = await api(`${base}/messages?after=${lastId.current}`, { silent: true })
    if (!rows.length) return
    const decrypted = await Promise.all(
      rows.map(async (row) => {
        try {
          const payload = await decryptMessage(key, conversationId, row)
          return { ...row, text: payload.text || '', attachments: payload.attachments || [] }
        } catch {
          return { ...row, text: '', attachments: [], failed: true }
        }
      }),
    )
    lastId.current = rows[rows.length - 1].id
    setMessages((current) => [...current, ...decrypted])
    if (rows.some((row) => !row.mine)) api(`${base}/read`, { method: 'POST', body: { last_read_message_id: lastId.current }, silent: true, message: false }).catch(() => {})
  }

  // Setup: identity -> publish -> conversation -> key (open mine, create, or wait) -> history.
  useEffect(() => {
    let active = true
    async function setup() {
      try {
        if (!store.current) throw new Error('This browser cannot keep the encryption key (no IndexedDB).')
        const me = await loadOrCreateIdentity(store.current, session.user.id)
        if (!active) return
        setIdentity(me)
        const published = await api('/chat/key', { silent: true })
        if (published.fingerprint !== me.fingerprint) await api('/chat/key', { method: 'PUT', body: { public_key: me.publicJwk }, silent: true, message: false })
        let view = await api(base, { silent: true })
        let key = null
        if (view.my_key) {
          key = await unwrapKey(view.my_key, me.privateKey)
        } else if (!view.has_conversation_key) {
          key = await generateConversationKey()
          const recipients = view.participants.filter((person) => person.public_key)
          const keys = await Promise.all(recipients.map((person) => wrapKeyFor(key, person.public_key)))
          view = await api(`${base}/keys`, { method: 'POST', body: { keys }, silent: true, message: false })
        }
        if (key) view = await shareKeys(view, key)
        if (!active) return
        keyRef.current = key ? { key, conversationId: view.id } : null
        setConversationKey(key)
        setConversation(view)
        setStatus(key ? '' : 'Waiting for the other party to open the chat and share the encryption key…')
        if (key) await pullMessages(key, view.id)
      } catch (err) {
        if (active) setError(err.message)
      }
    }
    setup()
    return () => {
      active = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appointment.id, session.user.id])

  // Polling: new messages every few seconds; the conversation (keys pending, state) less often.
  useEffect(() => {
    if (!conversation) return undefined
    const messagesTimer = setInterval(() => {
      if (keyRef.current) pullMessages(keyRef.current.key, keyRef.current.conversationId).catch(() => {})
    }, POLL_MESSAGES_MS)
    const conversationTimer = setInterval(async () => {
      try {
        let view = await api(base, { silent: true })
        if (!keyRef.current && view.my_key && identity) {
          const key = await unwrapKey(view.my_key, identity.privateKey)
          keyRef.current = { key, conversationId: view.id }
          setConversationKey(key)
          setStatus('')
          await pullMessages(key, view.id)
        } else if (keyRef.current) {
          view = await shareKeys(view, keyRef.current.key)
        }
        setConversation(view)
      } catch {
        /* next tick */
      }
    }, POLL_CONVERSATION_MS)
    return () => {
      clearInterval(messagesTimer)
      clearInterval(conversationTimer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversation && conversation.id, identity])

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [messages.length])

  function pickFiles(event) {
    const picked = [...(event.target.files || [])]
    const limit = conversation ? conversation.max_attachments_per_message : 5
    const maxBytes = conversation ? conversation.max_attachment_bytes : 10 * 1024 * 1024
    const tooBig = picked.find((file) => file.size > maxBytes)
    if (tooBig) setError(`"${tooBig.name}" exceeds the limit of ${size(maxBytes)}.`)
    setFiles((current) => [...current, ...picked.filter((file) => file.size <= maxBytes)].slice(0, limit))
    event.target.value = ''
  }

  async function send(event) {
    event.preventDefault()
    if (!keyRef.current || sending || (!text.trim() && files.length === 0)) return
    setSending(true)
    setError('')
    try {
      const { key, conversationId } = keyRef.current
      const attachments = []
      for (const file of files) {
        const { blob, iv } = await encryptBlob(key, conversationId, await file.arrayBuffer())
        const form = new FormData()
        form.append('file', new Blob([blob]), 'blob.bin')
        const stored = await api(`${base}/attachments`, { method: 'POST', body: form, silent: true, message: false })
        attachments.push({ id: stored.id, name: file.name, type: file.type, size: file.size, iv })
      }
      const body = await encryptMessage(key, conversationId, { text: text.trim(), attachments })
      await api(`${base}/messages`, { method: 'POST', body: { ...body, attachment_ids: attachments.map((item) => item.id) }, silent: true, message: false })
      setText('')
      setFiles([])
      await pullMessages(key, conversationId)
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  const other = conversation ? conversation.participants.find((person) => person.side !== (session.isProfessional ? 'professional' : 'customer')) : null
  const open = conversation ? conversation.open : false

  return (
    <div className="panel chat" data-testid="chat-panel">
      <div className="card-head" style={{ padding: 0, marginBottom: 8 }}>
        <h2>Chat</h2>
        <span className="badge remote" title="Messages and attachments are encrypted in your browser; the server and the staff cannot read them">🔒 end-to-end encrypted</span>
      </div>
      {error && <p className="error">{error}</p>}
      {status && !error && <p className="notice info" data-testid="chat-status">{status}</p>}
      {conversation && !open && <p className="notice warn">{conversation.closed_reason}. The history stays readable.</p>}
      <div className="chat-messages" ref={listRef} data-testid="chat-messages">
        {messages.length === 0 && conversationKey && <p className="muted">No messages yet. Say hello{other ? ` to ${other.name}` : ''}.</p>}
        {messages.map((message) => (
          <div key={message.id} className={`chat-bubble ${message.mine ? 'mine' : 'theirs'} ${message.failed ? 'failed' : ''}`}>
            <small className="chat-meta">{message.mine ? 'You' : message.sender_name} · {formatDateTime(message.created_at)}</small>
            {message.failed ? <em className="muted">Could not decrypt this message.</em> : message.text ? <p>{message.text}</p> : null}
            {message.attachments.map((meta) => (
              <Attachment key={meta.id} meta={meta} appointmentId={appointment.id} conversationId={conversation.id} conversationKey={conversationKey} />
            ))}
          </div>
        ))}
      </div>
      {open && conversationKey && (
        <form className="chat-composer" onSubmit={send}>
          <textarea
            value={text}
            placeholder="Write a message… (Enter to send, Shift+Enter for a new line)"
            rows={2}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                send(event)
              }
            }}
          />
          {files.length > 0 && (
            <ul className="chat-pending">
              {files.map((file, index) => (
                <li key={`${file.name}-${index}`}>
                  {file.name} <small className="muted">({size(file.size)})</small>
                  <button type="button" className="ghost small" onClick={() => setFiles(files.filter((_, i) => i !== index))}>×</button>
                </li>
              ))}
            </ul>
          )}
          <input ref={fileInput} type="file" multiple accept={ACCEPT} style={{ display: 'none' }} onChange={pickFiles} />
          <div className="actions">
            <button type="submit" disabled={sending || (!text.trim() && files.length === 0)}>{sending ? 'Encrypting…' : 'Send'}</button>
            <button type="button" className="ghost" disabled={sending} onClick={() => fileInput.current && fileInput.current.click()}>📎 Attach</button>
            <small className="muted">Photos, PDFs, documents, audio — up to {conversation ? conversation.max_attachments_per_message : 5} per message.</small>
          </div>
        </form>
      )}
      {conversation && (
        <details className="chat-security">
          <summary>Security details</summary>
          <p className="muted">
            Compare the <strong>safety code</strong> with {other ? other.name : 'the other party'} over the video call: if it matches on both screens, nobody
            tampered with the keys.
          </p>
          {conversation.safety_code ? (
            <code className="safety-code" data-testid="safety-code">{conversation.safety_code}</code>
          ) : (
            <p className="muted">The safety code appears once both parties have set up their keys.</p>
          )}
          <ul className="muted">
            {conversation.participants.map((person) => (
              <li key={person.user_id}>
                {person.name} ({person.side === 'professional' ? 'professional' : 'patient'}): {person.fingerprint ? shortFingerprint(person.fingerprint) : 'no key yet'}
                {person.fingerprint && !person.has_key ? ' · waiting for the conversation key' : ''}
              </li>
            ))}
          </ul>
          <small className="muted">
            Your key is generated and kept by this browser only{identity ? ` (${shortFingerprint(identity.fingerprint)})` : ''}. On another device a new key is
            created and the other party shares the conversation key again; the server never holds any of them.
          </small>
        </details>
      )}
    </div>
  )
}
