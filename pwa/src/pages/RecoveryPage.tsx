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
      setError(err instanceof ApiError ? err.message : 'Não foi possível enviar o e-mail.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <BackHeader title="Recuperar senha" onBack={() => navigate('/login')} />
      <div className="edit-form">
        {sent ? (
          <div className="success-page" style={{ minHeight: 'auto', padding: 16 }}>
            <img src="/assets/images/emblem.svg" alt="" style={{ width: 72 }} />
            <h1>E-mail enviado!</h1>
            <p>
              Se o e-mail estiver cadastrado, você receberá instruções para redefinir sua senha.
            </p>
            <Button onClick={() => navigate('/login')}>Voltar ao login</Button>
          </div>
        ) : (
          <form onSubmit={handleSubmit}>
            <p style={{ marginBottom: 16, fontSize: 14, lineHeight: 1.45 }}>
              Informe o e-mail da sua conta para receber o link de recuperação.
            </p>
            <div className="form-field form-field--dark">
              <label htmlFor="recovery-email">E-mail</label>
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
              Enviar
            </Button>
          </form>
        )}
      </div>
    </div>
  )
}
