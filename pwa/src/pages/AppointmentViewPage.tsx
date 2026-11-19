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
        if (active) setError(err instanceof ApiError ? err.message : 'Appointment not found')
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
      navigate('/app/appointments')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not cancel')
    } finally {
      setCanceling(false)
    }
  }

  if (loading) {
    return (
      <div className="page">
        <BackHeader title="Details" />
        <div className="empty-state">
          <div className="spinner" />
        </div>
      </div>
    )
  }

  if (!appointment) {
    return (
      <div className="page">
        <BackHeader title="Details" />
        <div className="empty-state">{error || 'Appointment not found'}</div>
      </div>
    )
  }

  return (
    <div className="page">
      <BackHeader title="Appointment details" />
      <div className="schedule">
        {error && <p className="form-error">{error}</p>}
        <div className="resume-box">
          <p>
            <strong>Type:</strong> {appointment.category}
          </p>
          <p>
            <strong>Patient:</strong> {appointment.holder}
          </p>
          <p>
            <strong>Specialty:</strong> {appointment.specialty}
          </p>
          <p>
            <strong>Clinic:</strong> {appointment.clinic}
          </p>
          <p>
            <strong>Professional:</strong> {appointment.professional}
          </p>
          <p>
            <strong>Date:</strong>{' '}
            {new Date(appointment.date).toLocaleString('en-US', {
              day: '2-digit',
              month: '2-digit',
              year: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
            })}
          </p>
          <p>
            <strong>Services:</strong> {appointment.services.join(', ') || '—'}
          </p>
          <p>
            <strong>Amount:</strong> R$ {appointment.value.toFixed(2)}
          </p>
          <p style={{ marginTop: 12 }}>
            <span className={`status-pill ${statusClass[appointment.status]}`}>
              {appointment.statusLabel}
            </span>
          </p>
        </div>
        {appointment.status === 'marked' && (
          <Button variant="secondary" loading={canceling} onClick={() => void handleCancel()}>
            Cancel appointment
          </Button>
        )}
        <div style={{ marginTop: 12 }}>
          <Button variant="outline" onClick={() => navigate(-1)}>
            Back
          </Button>
        </div>
      </div>
    </div>
  )
}
