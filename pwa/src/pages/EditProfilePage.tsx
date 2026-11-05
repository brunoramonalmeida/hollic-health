import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { BackHeader } from '../components/BackHeader'
import { Button } from '../components/Button'
import { useAuth } from '../context/AuthContext'

export function EditProfilePage() {
  const { user, updateUser } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({
    name: user?.name ?? '',
    email: user?.email ?? '',
    phone: user?.phone ?? '',
    cpf: user?.cpf ?? '',
    birthDate: user?.birthDate ?? '',
    maritalStatus: user?.maritalStatus ?? '',
    rg: user?.rg ?? '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    const result = await updateUser(form)
    setLoading(false)
    if (!result.ok) {
      setError(result.message || 'Falha ao salvar')
      return
    }
    setSaved(true)
    setTimeout(() => navigate('/app/perfil'), 800)
  }

  return (
    <div className="page">
      <BackHeader title="Meus dados" />
      <form className="edit-form form-stack" onSubmit={handleSubmit}>
        {(
          [
            ['name', 'Nome completo'],
            ['email', 'E-mail'],
            ['phone', 'Telefone'],
            ['cpf', 'CPF'],
            ['rg', 'RG'],
            ['birthDate', 'Data de nascimento'],
          ] as const
        ).map(([key, label]) => (
          <div key={key} className="form-field form-field--dark">
            <label htmlFor={key}>{label}</label>
            <input
              id={key}
              value={form[key]}
              onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
            />
          </div>
        ))}
        <div className="form-field form-field--dark">
          <label htmlFor="maritalStatus">Estado civil</label>
          <select
            id="maritalStatus"
            value={form.maritalStatus}
            onChange={(e) => setForm((f) => ({ ...f, maritalStatus: e.target.value }))}
          >
            <option value="">Selecione</option>
            <option>Solteiro(a)</option>
            <option>Casado(a)</option>
            <option>Divorciado(a)</option>
            <option>Viúvo(a)</option>
          </select>
        </div>
        {error && <p className="form-error">{error}</p>}
        {saved && <p style={{ color: 'var(--success)', fontWeight: 600 }}>Dados salvos!</p>}
        <Button type="submit" loading={loading}>
          Salvar
        </Button>
      </form>
    </div>
  )
}
