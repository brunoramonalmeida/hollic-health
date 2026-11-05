import { useEffect, useState } from 'react'
import CompanyStatement from '../../components/CompanyStatement'
import PageHeader from '../../components/PageHeader'
import { api } from '../../api'

/** The company's statement: what it pays and what its employees pay. */
export default function PortalStatement() {
  const [agreements, setAgreements] = useState([])
  const [error, setError] = useState('')

  useEffect(() => {
    api('/portal/agreements').then(setAgreements).catch((err) => setError(err.message))
  }, [])

  return (
    <section>
      <PageHeader
        crumb="Company portal / Statement"
        title="Statement"
        hint="What your company pays — its part of each employee's fees and the shortfall of the months below the minimum — and what your employees pay, month by month and employee by employee. Export the payroll list to deduct each employee's part."
      />
      {error && <p className="error">{error}</p>}
      <CompanyStatement endpoint="/portal/statement" agreements={agreements} onError={setError} />
    </section>
  )
}
