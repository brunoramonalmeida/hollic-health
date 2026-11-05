import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { mapApiAppointment } from '../api/mappers'
import { cancelAppointmentRequest, fetchAppointment } from '../api/services'
import { BackHeader } from '../components/BackHeader'
import { Button } from '../components/Button'
import { useAuth } from '../context/AuthContext'
import type { Appointment } from '../data/mock'

const statusClass: Record<string, string> = {
  marked: 'status-pill--marked',
  under_analysis: 'status-pill--analysis',
  completed: 'status-pill--completed',
  canceled: 'status-pill--canceled',
}

export function AppointmentViewPage() {
  const { id } = useParams()
  const { appointments, refreshAppointments } = useAuth()
  const navigate = useNavigate()
  const cached = appointments.find((a) => String(a.id) === id)
  const [appointment, setAppointment] = useState<Appointment | null>(cached || null)
  const [loading, setLoading] = useState(!cached)
  const [error, setError] = useState('')
  const [canceling, setCanceling] = useState(false)

  useEffect(() => {
    if (!id || cached) return
    let active = true
    fetchAppointment(id)
      .then((data) => {
        if (active) setAppointment(mapApiAppointment(data))
      })
      .catch((err) => {
        if (active) setError(err instanceof ApiError ? err.message : 'Agendamento não encontrado')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [id, cached])

  async function handleCancel() {
    if (!id) return
    setCanceling(true)
    try {
      await cancelAppointmentRequest(id)
      await refreshAppointments()
      navigate('/app/agendamentos')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Não foi possível cancelar')
    } finally {
      setCanceling(false)
    }
  }

  if (loading) {
    return (
      <div className="page">
        <BackHeader title="Detalhes" />
        <div className="empty-state">
          <div className="spinner" />
        </div>
      </div>
    )
  }

  if (!appointment) {
    return (
      <div className="page">
        <BackHeader title="Detalhes" />
        <div className="empty-state">{error || 'Agendamento não encontrado'}</div>
      </div>
    )
  }

  return (
    <div className="page">
      <BackHeader title="Detalhes do agendamento" />
      <div className="schedule">
        {error && <p className="form-error">{error}</p>}
        <div className="resume-box">
          <p>
            <strong>Tipo:</strong> {appointment.category}
          </p>
          <p>
            <strong>Paciente:</strong> {appointment.holder}
          </p>
          <p>
            <strong>Especialidade:</strong> {appointment.specialty}
          </p>
          <p>
            <strong>Clínica:</strong> {appointment.clinic}
          </p>
          <p>
            <strong>Profissional:</strong> {appointment.professional}
          </p>
          <p>
            <strong>Data:</strong>{' '}
            {new Date(appointment.date).toLocaleString('pt-BR', {
              day: '2-digit',
              month: '2-digit',
              year: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
            })}
          </p>
          <p>
            <strong>Serviços:</strong> {appointment.services.join(', ') || '—'}
          </p>
          <p>
            <strong>Valor:</strong> R$ {appointment.value.toFixed(2).replace('.', ',')}
          </p>
          <p style={{ marginTop: 12 }}>
            <span className={`status-pill ${statusClass[appointment.status]}`}>
              {appointment.statusLabel}
            </span>
          </p>
        </div>
        {appointment.status === 'marked' && (
          <Button variant="secondary" loading={canceling} onClick={() => void handleCancel()}>
            Cancelar agendamento
          </Button>
        )}
        <div style={{ marginTop: 12 }}>
          <Button variant="outline" onClick={() => navigate(-1)}>
            Voltar
          </Button>
        </div>
      </div>
    </div>
  )
}
