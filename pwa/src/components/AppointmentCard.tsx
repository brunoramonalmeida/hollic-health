import type { Appointment } from '../data/mock'
import { Button } from './Button'
import { Link } from 'react-router-dom'

function formatDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString('en-US', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const statusClass: Record<string, string> = {
  marked: 'status-pill--marked',
  under_analysis: 'status-pill--analysis',
  completed: 'status-pill--completed',
  canceled: 'status-pill--canceled',
}

interface Props {
  appointment: Appointment
}

export function AppointmentCard({ appointment }: Props) {
  return (
    <article className="card appointment-card">
      <div className="appointment-card__row">
        <div>
          <span className="appointment-card__label secondary">Type: </span>
          <strong className="secondary">{appointment.category}</strong>
        </div>
        <Link to={`/app/appointments/${appointment.id}`} className="appointment-card__detail" aria-label="Details">
          📄
        </Link>
      </div>
      <p>
        <span className="appointment-card__label primary">Patient: </span>
        <strong className="primary">{appointment.holder}</strong>
      </p>
      <p>
        <span className="appointment-card__label">Specialty: </span>
        {appointment.specialty}
      </p>
      <p>
        <span className="appointment-card__label">Professional: </span>
        {appointment.professional}
      </p>
      <p>
        <span className="appointment-card__label">Date: </span>
        {formatDate(appointment.date)}
      </p>
      <div className="appointment-card__footer">
        <span className={`status-pill ${statusClass[appointment.status]}`}>
          {appointment.statusLabel}
        </span>
        <span className="appointment-card__value">
          R$ {appointment.value.toFixed(2)}
        </span>
      </div>
      {appointment.status === 'marked' && (
        <div className="appointment-card__actions">
          <Button variant="secondary" style={{ padding: '10px', fontSize: 13 }}>
            Cancel
          </Button>
        </div>
      )}
    </article>
  )
}
