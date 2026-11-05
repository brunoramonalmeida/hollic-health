import { createContext, useContext, useEffect, useState } from 'react'

/**
 * Toast store. Lives outside React so `api.js` can report request results;
 * `ToastProvider` renders whatever is in the store.
 */
const listeners = new Set()
let queue = []
let counter = 0

export function pushToast(kind, message, { duration = kind === 'error' ? 6000 : 3000 } = {}) {
  const toast = { id: (counter += 1), kind, message, duration }
  queue = [...queue.slice(-4), toast]
  listeners.forEach((listener) => listener(queue))
  return toast.id
}

export function dismissToast(id) {
  queue = queue.filter((item) => item.id !== id)
  listeners.forEach((listener) => listener(queue))
}

export const toast = {
  success: (message, options) => pushToast('success', message, options),
  error: (message, options) => pushToast('error', message, options),
  info: (message, options) => pushToast('info', message, options),
}

function Toast({ item }) {
  useEffect(() => {
    const timer = setTimeout(() => dismissToast(item.id), item.duration)
    return () => clearTimeout(timer)
  }, [item.id, item.duration])
  // `show` satisfies CoreUI's Bootstrap rule `.toast:not(.show) { display: none }`.
  return (
    <div className={`toast show ${item.kind}`} role="status">
      <span>{item.message}</span>
      <button type="button" aria-label="Dismiss" onClick={() => dismissToast(item.id)}>×</button>
    </div>
  )
}

const ToastContext = createContext(toast)

export function ToastProvider({ children }) {
  const [items, setItems] = useState(queue)
  useEffect(() => {
    listeners.add(setItems)
    return () => listeners.delete(setItems)
  }, [])
  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((item) => <Toast key={item.id} item={item} />)}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  return useContext(ToastContext)
}
