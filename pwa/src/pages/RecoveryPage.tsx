import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { forgotPasswordRequest } from '../api/services'
import { BackHeader } from '../components/BackHeader'
import { Button } from '../components/Button'

export function RecoveryPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [loading, setLoading] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await forgotPasswordRequest(email)
      setSent(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send the email.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <BackHeader title="Reset password" onBack={() => navigate('/login')} />
      <div className="edit-form">
        {sent ? (
          <div className="success-page" style={{ minHeight: 'auto', padding: 16 }}>
            <p className="brand-word">Hollic Health</p>
            <h1>Email sent</h1>
            <p>
              If that email is on file, you will get instructions to reset your password.
            </p>
            <Button onClick={() => navigate('/login')}>Back to sign in</Button>
          </div>
        ) : (
          <form onSubmit={handleSubmit}>
            <p style={{ marginBottom: 16, fontSize: 14, lineHeight: 1.45 }}>
              Enter your account email to receive the reset link.
            </p>
            <div className="form-field form-field--dark">
              <label htmlFor="recovery-email">Email</label>
              <input
                id="recovery-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            {error && <p className="form-error">{error}</p>}
            <Button type="submit" loading={loading}>
              Send
            </Button>
          </form>
        )}
      </div>
    </div>
  )
}
