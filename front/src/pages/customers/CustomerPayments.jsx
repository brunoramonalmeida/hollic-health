import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../../api'
import EmployeeStatement from '../../components/EmployeeStatement'
import PageHeader from '../../components/PageHeader'

/** The staff's view of a customer's payments statement (the client's "My payments"). */
export default function CustomerPayments() {
  const { id } = useParams()
  const [customer, setCustomer] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api(`/customers/${id}`).then(setCustomer).catch((err) => setError(err.message))
  }, [id])

  return (
    <section>
      <PageHeader
        back={{ to: `/customers/${id}`, label: customer ? customer.name : 'Customer' }}
        title={`Payments · ${customer ? customer.name : ''}`}
        hint="The customer's own part of every fee (its share in a corporate co-payment) and what its company paid — as the customer sees it in My payments."
      />
      {error && <p className="error">{error}</p>}
      <EmployeeStatement customerId={id} onError={setError} />
    </section>
  )
}
