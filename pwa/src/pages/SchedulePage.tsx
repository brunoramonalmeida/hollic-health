import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { mapApiAppointment } from '../api/mappers'
import {
  createAppointmentRequest,
  fetchAppointmentNeededData,
  fetchCategories,
  fetchCheckout,
} from '../api/services'
import { api } from '../api/client'
import { endpoints } from '../api/endpoints'
import type { ApiCategory } from '../api/types'
import { BackHeader } from '../components/BackHeader'
import { Button } from '../components/Button'
import { useAuth } from '../context/AuthContext'

type Step = 'checkout' | 'type' | 'form' | 'resume' | 'success'

interface NeededData {
  clinics?: Array<{ id: number; name: string }>
  professionals?: Array<{ id: number; name: string }>
  specialties?: Array<{ id: number; name: string }>
  services?: Array<{ id: number; name: string; price?: number | string }>
  payment_methods?: Array<{ id: number; name: string }>
  [key: string]: unknown
}

function normalizeCategories(payload: unknown): ApiCategory[] {
  if (Array.isArray(payload)) return payload as ApiCategory[]
  if (payload && typeof payload === 'object' && Array.isArray((payload as { data?: unknown }).data)) {
    return (payload as { data: ApiCategory[] }).data
  }
  return []
}

export function SchedulePage() {
  const navigate = useNavigate()
  const { user, session, addAppointment, refreshAppointments } = useAuth()
  const [step, setStep] = useState<Step>('checkout')
  const [checkoutErrors, setCheckoutErrors] = useState<string[]>([])
  const [categories, setCategories] = useState<ApiCategory[]>([])
  const [typeId, setTypeId] = useState<number | null>(null)
  const [needed, setNeeded] = useState<NeededData | null>(null)
  const [clinicId, setClinicId] = useState<number | ''>('')
  const [professionalId, setProfessionalId] = useState<number | ''>('')
  const [specialtyId, setSpecialtyId] = useState<number | ''>('')
  const [serviceId, setServiceId] = useState<number | ''>('')
  const [paymentMethodId, setPaymentMethodId] = useState<number | ''>('')
  const [date, setDate] = useState('')
  const [time, setTime] = useState('09:00')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const selectedType = useMemo(
    () => categories.find((t) => t.id === typeId),
    [categories, typeId],
  )

  const selectedService = useMemo(
    () => (needed?.services || []).find((s) => s.id === serviceId),
    [needed, serviceId],
  )

  const amount = useMemo(() => {
    const price = selectedService?.price
    const n = typeof price === 'string' ? Number(price) : price
    return Number.isFinite(n) ? Number(n) : 0
  }, [selectedService])

  useEffect(() => {
    let active = true
    setLoading(true)
    Promise.all([fetchCheckout(), fetchCategories()])
      .then(([checkout, cats]) => {
        if (!active) return
        const errs = Array.isArray(checkout.errors) ? checkout.errors : []
        setCheckoutErrors(errs)
        setCategories(normalizeCategories(cats))
        setStep(errs.length ? 'checkout' : 'type')
      })
      .catch((err) => {
        if (!active) return
        setError(err instanceof ApiError ? err.message : 'Could not start booking')
        setStep('checkout')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  async function goForm() {
    if (!typeId) return
    setError('')
    setLoading(true)
    try {
      const data = await fetchAppointmentNeededData(typeId)
      setNeeded(data as NeededData)
      const clinics = (data.clinics as NeededData['clinics']) || []
      const professionals = (data.professionals as NeededData['professionals']) || []
      const specialties = (data.specialties as NeededData['specialties']) || []
      const services = (data.services as NeededData['services']) || []
      setClinicId(clinics[0]?.id ?? '')
      setProfessionalId(professionals[0]?.id ?? '')
      setSpecialtyId(specialties[0]?.id ?? '')
      setServiceId(services[0]?.id ?? '')

      // payment methods may come from needed-data or dedicated endpoint
      let methods = (data.payment_methods as NeededData['payment_methods']) || []
      if (!methods.length) {
        try {
          const pm = await api.get<Array<{ id: number; name: string }>>(endpoints.paymentMethods)
          methods = Array.isArray(pm) ? pm : []
          setNeeded((prev) => ({ ...(prev || {}), payment_methods: methods }))
        } catch {
          // ignore
        }
      }
      setPaymentMethodId(methods[0]?.id ?? '')
      setStep('form')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load booking details')
    } finally {
      setLoading(false)
    }
  }

  function goResume(e: FormEvent) {
    e.preventDefault()
    if (!clinicId || !date) {
      setError('Fill in the clinic and the date.')
      return
    }
    setError('')
    setStep('resume')
  }

  async function confirm() {
    if (!typeId || !clinicId || !paymentMethodId) {
      setError('Some details are still missing.')
      return
    }
    setLoading(true)
    setError('')
    const payload: Record<string, unknown> = {
      customer_id: session?.customer?.id,
      clinic_id: clinicId,
      category_id: typeId,
      amount_to_pay: amount.toFixed(2),
      marked_to: `${date} ${time}:00`,
      payment: {
        amount: amount.toFixed(2),
        partial_amount: amount.toFixed(2),
        installments: '1',
        payment_method_id: paymentMethodId,
        fee: 0,
      },
    }
    if (professionalId) payload.professional_id = professionalId
    if (specialtyId) payload.specialty_id = specialtyId
    if (serviceId) payload.services = [{ id: serviceId }]

    try {
      const result = await createAppointmentRequest(payload)
      if (result.data) addAppointment(mapApiAppointment(result.data))
      await refreshAppointments()
      setStep('success')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Booking failed')
    } finally {
      setLoading(false)
    }
  }

  if (loading && step === 'checkout') {
    return (
      <div className="page">
        <BackHeader title="Book" />
        <div className="empty-state">
          <div className="spinner" />
        </div>
      </div>
    )
  }

  if (step === 'success') {
    return (
      <div className="page success-page">
        <p className="brand-word">Hollic Health</p>
        <h1>Appointment booked</h1>
        <p>Your visit is booked. Follow it on the Appointments tab.</p>
        <Button onClick={() => navigate('/app/appointments', { replace: true })}>
          View appointments
        </Button>
      </div>
    )
  }

  return (
    <div className="page">
      <BackHeader
        title={
          step === 'checkout'
            ? 'Book'
            : step === 'type'
              ? 'Visit type'
              : step === 'form'
                ? 'Book an appointment'
                : 'Summary'
        }
        onBack={() => {
          if (step === 'form') setStep('type')
          else if (step === 'resume') setStep('form')
          else navigate(-1)
        }}
      />
      <div className="schedule">
        {error && <p className="form-error" style={{ marginBottom: 12 }}>{error}</p>}

        {step === 'checkout' && (
          <div>
            <div className="resume-box">
              <h3 style={{ color: 'var(--secondary)', marginBottom: 8 }}>Booking is not available</h3>
              {(checkoutErrors.length ? checkoutErrors : [error || 'Check your account details.']).map(
                (msg) => (
                  <p key={msg}>• {msg}</p>
                ),
              )}
            </div>
            <Button variant="secondary" onClick={() => navigate('/app/profile')}>
              Go to profile
            </Button>
          </div>
        )}

        {step === 'type' && (
          <>
            <div className="type-grid">
              {categories.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={`type-card ${typeId === t.id ? 'is-selected' : ''}`}
                  onClick={() => setTypeId(t.id)}
                >
                  <span>🩺</span>
                  {t.name}
                </button>
              ))}
            </div>
            {!categories.length && (
              <div className="empty-state">No visit types available</div>
            )}
            <div style={{ marginTop: 24 }}>
              <Button disabled={!typeId} loading={loading} onClick={() => void goForm()}>
                Continue
              </Button>
            </div>
          </>
        )}

        {step === 'form' && (
          <form className="form-stack" onSubmit={goResume}>
            {(needed?.clinics || []).length > 0 && (
              <div className="form-field form-field--dark">
                <label>Clinic</label>
                <select
                  value={clinicId}
                  onChange={(e) => setClinicId(Number(e.target.value))}
                  required
                >
                  {needed?.clinics?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {(needed?.specialties || []).length > 0 && (
              <div className="form-field form-field--dark">
                <label>Specialty</label>
                <select
                  value={specialtyId}
                  onChange={(e) => setSpecialtyId(Number(e.target.value))}
                >
                  {needed?.specialties?.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {(needed?.professionals || []).length > 0 && (
              <div className="form-field form-field--dark">
                <label>Professional</label>
                <select
                  value={professionalId}
                  onChange={(e) => setProfessionalId(Number(e.target.value))}
                >
                  {needed?.professionals?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {(needed?.services || []).length > 0 && (
              <div className="form-field form-field--dark">
                <label>Service</label>
                <select value={serviceId} onChange={(e) => setServiceId(Number(e.target.value))}>
                  {needed?.services?.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                      {s.price != null ? ` — R$ ${s.price}` : ''}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {(needed?.payment_methods || []).length > 0 && (
              <div className="form-field form-field--dark">
                <label>Payment method</label>
                <select
                  value={paymentMethodId}
                  onChange={(e) => setPaymentMethodId(Number(e.target.value))}
                  required
                >
                  {needed?.payment_methods?.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="form-field form-field--dark">
              <label>Date</label>
              <input
                type="date"
                required
                value={date}
                onChange={(e) => setDate(e.target.value)}
                min={new Date().toISOString().slice(0, 10)}
              />
            </div>
            <div className="form-field form-field--dark">
              <label>Time</label>
              <input type="time" value={time} onChange={(e) => setTime(e.target.value)} required />
            </div>
            <Button type="submit">Review appointment</Button>
          </form>
        )}

        {step === 'resume' && (
          <>
            <div className="resume-box">
              <p>
                <strong>Type:</strong> {selectedType?.name}
              </p>
              <p>
                <strong>Patient:</strong> {user?.name}
              </p>
              <p>
                <strong>Clinic:</strong>{' '}
                {needed?.clinics?.find((c) => c.id === clinicId)?.name || '—'}
              </p>
              <p>
                <strong>Professional:</strong>{' '}
                {needed?.professionals?.find((p) => p.id === professionalId)?.name || '—'}
              </p>
              <p>
                <strong>Date and time:</strong>{' '}
                {new Date(`${date}T${time}:00`).toLocaleString('en-US', {
                  day: '2-digit',
                  month: '2-digit',
                  year: 'numeric',
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </p>
              <p>
                <strong>Amount:</strong> R$ {amount.toFixed(2)}
              </p>
            </div>
            <Button loading={loading} onClick={() => void confirm()}>
              Confirm appointment
            </Button>
          </>
        )}
      </div>
    </div>
  )
}
