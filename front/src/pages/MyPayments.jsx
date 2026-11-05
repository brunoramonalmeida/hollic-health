import { useState } from 'react'
import EmployeeStatement from '../components/EmployeeStatement'
import PageHeader from '../components/PageHeader'

/** The client's own payments: its part of every fee and what its company paid. */
export default function MyPayments() {
  const [error, setError] = useState('')
  return (
    <section>
      <PageHeader
        crumb="My area / Payments"
        title="My payments"
        hint="Your part of each monthly fee of your plan — all of it for an individual plan; your share when your company co-pays it; nothing when your company pays it all — and what is still open."
      />
      {error && <p className="error">{error}</p>}
      <EmployeeStatement onError={setError} />
    </section>
  )
}
