import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { BackHeader } from '../components/BackHeader'
import { Button } from '../components/Button'
import { useAuth } from '../context/AuthContext'

export function PasswordUpdatePage() {
  const navigate = useNavigate()
  const { changePassword } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    if (next.length < 6) {
      setError('The new password must be at least 6 characters.')
      return
    }
    if (next !== confirm) {
      setError('The passwords do not match.')
      return
    }
    setLoading(true)
    const result = await changePassword(current, next, confirm)
    setLoading(false)
    if (!result.ok) {
      setError(result.message || 'Could not update the password')
      return
    }
    navigate('/app/profile')
  }

  return (
    <div className="page">
      <BackHeader title="Change password" />
      <form className="edit-form form-stack" onSubmit={handleSubmit}>
        <div className="form-field form-field--dark">
          <label htmlFor="current">Current password</label>
          <input
            id="current"
            type="password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
          />
        </div>
        <div className="form-field form-field--dark">
          <label htmlFor="next">New password</label>
          <input
            id="next"
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            required
          />
        </div>
        <div className="form-field form-field--dark">
          <label htmlFor="confirm">Confirm new password</label>
          <input
            id="confirm"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            required
          />
        </div>
        {error && <p className="form-error">{error}</p>}
        <Button type="submit" loading={loading}>
          Update password
        </Button>
      </form>
    </div>
  )
}
